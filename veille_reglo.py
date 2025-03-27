import os
import time
import json
import threading
import requests
import feedparser
import openai
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from requests.exceptions import SSLError
from flask import Flask, jsonify, request

# SerpApi
from serpapi import GoogleSearch

# --- Initialisation de l'application Flask ---
app = Flask(__name__)

# Chargement des variables d'environnement
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
SERP_API_KEY = os.getenv("SERP_API_KEY")

# Variables globales pour stocker l'état de l'analyse
analysis_results = None
analysis_in_progress = False

# =============================================
# 1) FONCTIONS DE GESTION DES FICHIERS JSON
# =============================================
def load_alerts_file():
    with open("rss_alerts.json", "r", encoding="utf-8") as file:
        return json.load(file)

def load_seen_entries():
    try:
        with open("seen_entries.json", "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return []

def save_seen_entries(entries):
    with open("seen_entries.json", "w", encoding="utf-8") as file:
        json.dump(entries, file, ensure_ascii=False, indent=4)

def save_new_alerts(new_alertes_json):
    try:
        if os.path.exists("alertes_reglementaires.json"):
            with open("alertes_reglementaires.json", "r", encoding="utf-8") as f:
                try:
                    existing_data = json.load(f)
                except json.JSONDecodeError:
                    existing_data = []
        else:
            existing_data = []
        updated_data = existing_data + new_alertes_json
        with open("alertes_reglementaires.json", "w", encoding="utf-8") as f:
            json.dump(updated_data, f, ensure_ascii=False, indent=4)
        print("✅ Fichier alertes_reglementaires.json mis à jour.")
    except Exception as e:
        print(f"❌ Erreur lors de la sauvegarde des alertes : {e}")

# =============================================
# 2) FONCTIONS DE TRAITEMENT (RSS, SERPAPI, GPT)
# =============================================
def get_text_content(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        text_parts = []
        for tag in soup.find_all(['p', 'div', 'li', 'span']):
            extracted = tag.get_text(strip=True)
            if len(extracted.split()) > 3:
                text_parts.append(extracted)
        return " ".join(text_parts)
    except SSLError as e:
        print(f"Certificat invalide pour {url} => On ignore : {e}")
        return ""
    except Exception as e:
        print(f"Erreur get_text_content({url}): {e}")
        return ""

def gpt_chat_completion(prompt, model="gpt-4", temperature=0):
    while True:
        try:
            response = openai.ChatCompletion.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature
            )
            return response.choices[0].message.content.strip()
        except openai.error.RateLimitError as e:
            print(f"[GPT Rate Limit] {str(e)}\nAttente 10 secondes...")
            time.sleep(10)
        except Exception as e:
            print(f"[GPT Error] {str(e)}\nAttente 5 secondes avant retry...")
            time.sleep(5)

def search_google_serpapi(query):
    if not SERP_API_KEY:
        print("❌ ERREUR: Aucune clé SerpApi détectée. Mets SERP_API_KEY dans .env")
        return []
    params = {
        "engine": "google",
        "q": query,
        "hl": "fr",
        "gl": "fr",
        "api_key": SERP_API_KEY,
        "num": 10
    }
    search = GoogleSearch(params)
    results_dict = search.get_dict()
    if "organic_results" not in results_dict:
        print("Aucun résultat (ou limite SerpApi atteinte).")
        return []
    organic_results = results_dict["organic_results"]
    final_results = []
    for item in organic_results:
        title = item.get("title", "")
        link = item.get("link", "")
        if link.startswith("http"):
            final_results.append({"title": title, "link": link})
    return final_results

def google_search_analysis(query):
    variations = [query, f"{query} réglementation"]
    all_search_results = []
    for variation in variations:
        results = search_google_serpapi(variation)
        print(f"[SerpApi] {variation} => {len(results)} résultats")
        all_search_results.extend(results)
        time.sleep(3)
    relevant_alerts = []
    found_urls = set()
    for result in all_search_results:
        url = result['link']
        title = result['title']
        if url in found_urls:
            continue
        found_urls.add(url)
        text_page = get_text_content(url)
        if not text_page:
            continue
        excerpt = text_page[:1500]
        prompt = f"""
Cet article mentionne-t-il un changement législatif ou réglementaire (loi, décret, arrêté, directive) ?
Titre : {title}
Extrait :
{excerpt}

Réponds simplement :
"Oui, résumé: <ton résumé>"
ou
"Non"
"""
        analysis_result = gpt_chat_completion(prompt, model="gpt-4", temperature=0)
        if analysis_result.lower().startswith("oui"):
            print(f"✅ [GPT] {title} => {analysis_result}")
            relevant_alerts.append({
                "sujet": query,
                "titre": title,
                "analyse": analysis_result,
                "lien": url
            })
    return relevant_alerts

def rss_analysis(sujet, rss_url):
    feed = feedparser.parse(rss_url)
    alerts = []
    seen_entries = load_seen_entries()
    for entry in feed.entries:
        if entry.link in seen_entries:
            continue
        prompt = f"""
Vérifie si cet article mentionne un changement réglementaire officiel 
en lien avec le sujet '{sujet}'.

Titre : {entry.title}
Contenu : {entry.summary}

Réponds uniquement par :
'Oui, résumé: <ton résumé>'
ou
'Non'
        """
        try:
            result = gpt_chat_completion(prompt, model="gpt-3.5-turbo", temperature=0)
            seen_entries.append(entry.link)
            save_seen_entries(seen_entries)
            if result.lower().startswith("oui"):
                print(f"✅ [RSS] Article réglementaire : {entry.title} => {result}")
                alerts.append({
                    "sujet": sujet,
                    "titre": entry.title,
                    "analyse": result,
                    "date": getattr(entry, 'published', ''),
                    "lien": entry.link
                })
        except Exception as e:
            print(f"Erreur d'analyse GPT sur RSS : {e}")
    return alerts

def full_analysis(sujet):
    """
    Combine l'analyse RSS et la recherche via SerpApi pour un sujet donné.
    """
    alerts = []
    try:
        rss_configs = load_alerts_file()
    except Exception as e:
        print(f"Erreur lors du chargement de rss_alerts.json : {e}")
        rss_configs = []
    for config in rss_configs:
        if config.get("nom").lower() == sujet.lower():
            alerts.extend(rss_analysis(sujet, config.get("rss")))
            break
    alerts.extend(google_search_analysis(sujet))
    return alerts

def run_analysis():
    """
    Fonction lancée en arrière-plan par le POST /trigger.
    Parcourt tous les sujets définis dans rss_alerts.json.
    """
    global analysis_results, analysis_in_progress
    analysis_in_progress = True
    results = []
    try:
        rss_configs = load_alerts_file()
    except Exception as e:
        print(f"Erreur lors du chargement de rss_alerts.json : {e}")
        rss_configs = []
    for config in rss_configs:
        sujet = config.get("nom")
        results.extend(full_analysis(sujet))
        time.sleep(1)  # petite pause pour éviter une surcharge d'appels API
    # Mise à jour du fichier d'alertes
    if results:
        save_new_alerts(results)
    analysis_results = results
    analysis_in_progress = False

# =============================================
# 3) ENDPOINTS API
# =============================================

@app.route("/trigger", methods=["POST"])
def trigger_analysis():
    """
    Déclenche l'analyse en arrière-plan via un POST.
    Le flux Power Automate envoie un POST, récupère un accusé,
    puis attend et déclenche le GET une fois terminé.
    """
    global analysis_in_progress
    if analysis_in_progress:
        return jsonify({"status": "analysis already in progress"}), 200
    thread = threading.Thread(target=run_analysis)
    thread.start()
    return jsonify({"status": "analysis started"}), 202

@app.route("/alerts", methods=["GET"])
def get_alerts():
    """
    Renvoie les résultats de l'analyse. Si l'analyse est en cours,
    une réponse indiquant qu'il faut réessayer plus tard est renvoyée.
    """
    global analysis_in_progress, analysis_results
    if analysis_in_progress:
        return jsonify({"status": "analysis in progress, please try later"}), 202
    if analysis_results is None:
        return jsonify({"status": "no analysis run yet"}), 200
    return jsonify(analysis_results), 200

# =============================================
# 4) POINT D'ENTRÉE DE L'APPLICATION
# =============================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
