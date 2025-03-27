import os
import time
import json
import requests
import feedparser
import openai
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from requests.exceptions import SSLError
from flask import Flask, jsonify
import threading

# SerpApi
from serpapi import GoogleSearch

app = Flask(__name__)

# =============================================
# 1) CONFIGURATION / CHARGEMENT DE LA CLÉ OPENAI
# =============================================
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

# Clé SerpApi (stockée dans .env : SERP_API_KEY=...)
SERP_API_KEY = os.getenv("SERP_API_KEY")

# =============================================
# 2) FONCTIONS POUR GÉRER LES FICHIERS JSON
# =============================================

def load_alerts():
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

        print(f"✅ Fichier alertes_reglementaires.json mis à jour.")
    except Exception as e:
        print(f"❌ Erreur lors de la sauvegarde des alertes : {e}")

def update_status(status, progression=0):
    with open("status.json", "w", encoding="utf-8") as f:
        json.dump({"en_cours": status, "progression": progression}, f)

# =============================================
# 3) GESTION DE L'API OPENAI (GPT)
# =============================================

def gpt_chat_completion(prompt, model="gpt-4", temperature=0):
    while True:
        try:
            response = openai.ChatCompletion.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature
            )
            return response.choices[0].message.content.strip()
        except openai.error.RateLimitError:
            time.sleep(10)
        except Exception as e:
            print(f"Erreur OpenAI : {e}")
            time.sleep(5)

# =============================================
# 4) RECHERCHE GOOGLE (SERPAPI)
# =============================================

def search_google_serpapi(query):
    if not SERP_API_KEY:
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
        return []

    organic_results = results_dict["organic_results"]
    final_results = []

    for item in organic_results:
        title = item.get("title", "")
        link = item.get("link", "")
        if link.startswith("http"):
            final_results.append({"title": title, "link": link})

    return final_results

# =============================================
# 5) GESTION DU TEXTE HTML
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
    except Exception as e:
        return ""

def filter_alerts(links):
    relevant_alerts = []
    for link in links:
        text_content = get_text_content(link['link'])
        if not text_content:
            continue

        prompt = f"""
Cet article parle-t-il d'un changement législatif ou réglementaire officiel ?
Titre : {link['title']}

Texte :
{text_content[:1500]}

Réponds uniquement par :
"Oui, résumé: <ton résumé>"
ou
"Non"
"""
        analysis = gpt_chat_completion(prompt)
        if analysis.lower().startswith("oui"):
            relevant_alerts.append({"title": link['title'], "link": link['link'], "analyse": analysis})
    return relevant_alerts

# =============================================
# 6) ANALYSE DES FLUX RSS
# =============================================

def check_alerts():
    alerts = load_alerts()
    seen_entries = load_seen_entries()
    new_alertes_json = []

    for alert in alerts:
        feed = feedparser.parse(alert["rss"])
        sujet = alert["nom"]

        for entry in feed.entries:
            if entry.link in seen_entries:
                continue

            prompt = f"""
Vérifie si cet article mentionne un changement réglementaire officiel en lien avec le sujet '{sujet}'.
Titre : {entry.title}
Contenu : {entry.summary}

Réponds uniquement par :
'Oui, résumé: <ton résumé>'
ou
'Non'
            """
            result = gpt_chat_completion(prompt)
            seen_entries.append(entry.link)
            if result.lower().startswith("oui"):
                new_alertes_json.append({
                    "sujet": sujet,
                    "titre": entry.title,
                    "analyse": result,
                    "lien": entry.link
                })
                
    save_seen_entries(seen_entries)
    return new_alertes_json

# =============================================
# 7) ANALYSE TOTALE
# =============================================

def full_analysis():
    sujets = ["FICT", "EUR-LEX", "CIDEF", "RASFF"]
    all_alerts = []
    
    rss_alerts = check_alerts()
    all_alerts.extend(rss_alerts)

    for sujet in sujets:
        links = search_google_serpapi(sujet)
        filtered_alerts = filter_alerts(links)
        all_alerts.extend(filtered_alerts)

    save_new_alerts(all_alerts)
    update_status(False, 100)
    return all_alerts

def async_analysis():
    update_status(True, 0)
    full_analysis()

# =============================================
# 8) ROUTES FLASK
# =============================================

@app.route('/launch_research', methods=['POST'])
def launch_research():
    print("🚀 Lancement de la recherche...")
    try:
        async_analysis()  # Appel direct sans threading pour vérifier si ça marche sur Heroku
        return jsonify({"status": "Recherche lancée avec succès"})
    except Exception as e:
        print(f"❌ Erreur lors du lancement de la recherche : {e}")
        return jsonify({"status": "Erreur lors du lancement de la recherche"}), 500

@app.route('/get_alertes', methods=['GET'])
def get_alertes():
    if os.path.exists("alertes_reglementaires.json"):
        with open("alertes_reglementaires.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    return jsonify([])

@app.route('/get_status', methods=['GET'])
def get_status():
    if os.path.exists("status.json"):
        with open("status.json", "r", encoding="utf-8") as f:
            status_data = json.load(f)
        return jsonify(status_data)
    return jsonify({"en_cours": False, "progression": 0})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
