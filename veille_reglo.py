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
            try:
                result = gpt_chat_completion(prompt)
                seen_entries.append(entry.link)
                save_seen_entries(seen_entries)

                if result.lower().startswith("oui"):
                    new_alertes_json.append({
                        "sujet": sujet,
                        "titre": entry.title,
                        "analyse": result,
                        "date": getattr(entry, 'published', ''),
                        "lien": entry.link
                    })
            except Exception as e:
                print(f"Erreur d'analyse GPT sur RSS : {e}")

    if new_alertes_json:
        save_new_alerts(new_alertes_json)

    return new_alertes_json

def full_analysis():
    sujets = ["FICT", "EUR-LEX", "CIDEF", "RASFF"]
    all_alerts = []

    # Analyse des flux RSS
    rss_alerts = check_alerts()
    all_alerts.extend(rss_alerts)

    # Recherche Google (SerpApi)
    for sujet in sujets:
        alerts = search_google_serpapi(sujet)
        all_alerts.extend(alerts)

    save_new_alerts(all_alerts)
    return all_alerts
    sujets = ["FICT", "EUR-LEX", "CIDEF", "RASFF"]
    all_alerts = []

    for sujet in sujets:
        alerts = search_google_serpapi(sujet)
        all_alerts.extend(alerts)

    save_new_alerts(all_alerts)
    return all_alerts

@app.route('/launch_research', methods=['POST'])
def launch_research():
    all_alerts = full_analysis()
    return jsonify({"status": "Recherche terminée", "alertes_trouvees": len(all_alerts)})

@app.route('/get_alertes', methods=['GET'])
def get_alertes():
    try:
        if os.path.exists("alertes_reglementaires.json"):
            with open("alertes_reglementaires.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            return jsonify(data)
        else:
            return jsonify({"error": "Aucune alerte trouvée."}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
