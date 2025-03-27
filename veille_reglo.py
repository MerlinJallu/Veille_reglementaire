from flask import Flask, jsonify, request
import os
import time
import json
import requests
import feedparser
import openai
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from requests.exceptions import SSLError

# SerpApi
from serpapi import GoogleSearch

app = Flask(__name__)


# =============================================
# 1) CONFIGURATION / CHARGEMENT DE LA CLÉ OPENAI
# =============================================
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

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
        with open("alertes_reglementaires.json", "w", encoding="utf-8") as f:
            json.dump(new_alertes_json, f, ensure_ascii=False, indent=4)
        return "✅ Fichier alertes_reglementaires.json mis à jour."
    except Exception as e:
        return f"❌ Erreur lors de la sauvegarde des alertes : {e}"


# =============================================
# 3) GESTION DE L'API OPENAI
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
            time.sleep(5)


# =============================================
# 4) SERPAPI SEARCH
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
    final_results = [{"title": item.get("title"), "link": item.get("link")} for item in organic_results if item.get("link", "").startswith("http")]

    return final_results


# =============================================
# 5) ANALYSE DES FLUX RSS
# =============================================

def check_alerts():
    alerts = load_alerts()
    seen_entries = load_seen_entries()
    new_alerts = []

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
                    new_alerts.append({
                        "sujet": sujet,
                        "titre": entry.title,
                        "analyse": result,
                        "date": getattr(entry, 'published', ''),
                        "lien": entry.link
                    })
            except Exception as e:
                continue

    if new_alerts:
        save_new_alerts(new_alerts)

    return new_alerts


# =============================================
# 6) ROUTES FLASK
# =============================================

@app.route('/')
def index():
    return "API de Veille Réglementaire en cours d'exécution."


@app.route('/api/alerts', methods=['GET'])
def get_alerts():
    alerts = load_alerts()
    return jsonify(alerts)


@app.route('/api/seen_entries', methods=['GET'])
def get_seen_entries():
    seen_entries = load_seen_entries()
    return jsonify(seen_entries)


@app.route('/api/alerts', methods=['POST'])
def save_alerts():
    try:
        new_alerts = request.json.get('new_alerts', [])
        message = save_new_alerts(new_alerts)
        return jsonify({"message": message}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route('/api/analyse', methods=['POST'])
def analyse():
    data = request.json
    prompt = data.get("prompt", "")
    model = data.get("model", "gpt-4")

    if not prompt:
        return jsonify({"error": "Prompt manquant."}), 400

    result = gpt_chat_completion(prompt, model=model)
    return jsonify({"result": result})


@app.route('/api/search', methods=['POST'])
def search():
    data = request.json
    query = data.get("query")

    if not query:
        return jsonify({"error": "La requête est manquante."}), 400

    results = search_google_serpapi(query)
    return jsonify({"results": results})


@app.route('/api/rss', methods=['GET'])
def check_rss():
    results = check_alerts()
    return jsonify(results)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
