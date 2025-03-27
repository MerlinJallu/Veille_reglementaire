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
# 5) ANALYSE DES FLUX RSS & SERPAPI
# =============================================

def analyse_sujet(sujet):
    results = []
    seen_entries = load_seen_entries()

    # Recherche Google via SerpApi
    google_results = search_google_serpapi(sujet)
    for result in google_results:
        if result['link'] not in seen_entries:
            prompt = f"""
            Cet article mentionne-t-il un changement législatif ou réglementaire ?
            Titre : {result['title']}
            Lien : {result['link']}
            Réponds par :
            'Oui, résumé: <ton résumé>'
            ou
            'Non'
            """
            analysis = gpt_chat_completion(prompt)
            if analysis.lower().startswith("oui"):
                results.append({
                    "sujet": sujet,
                    "titre": result['title'],
                    "analyse": analysis,
                    "lien": result['link']
                })
            seen_entries.append(result['link'])

    save_seen_entries(seen_entries)
    return results


# =============================================
# 6) ROUTES FLASK
# =============================================

@app.route('/')
def index():
    return "API de Veille Réglementaire en cours d'exécution."


@app.route('/api/veille', methods=['POST'])
def veille():
    data = request.json
    sujets = data.get("sujets", [])

    if not sujets:
        return jsonify({"error": "Liste de sujets manquante."}), 400

    results = []

    for sujet in sujets:
        results += analyse_sujet(sujet)

    return jsonify(results), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
