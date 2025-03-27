rom flask import Flask, jsonify, request
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

# Clé SerpApi (stockée dans .env : SERP_API_KEY=...)
SERP_API_KEY = os.getenv("SERP_API_KEY")


# =============================================
# 2) FONCTION DE CHARGEMENT DES FLUX RSS
# =============================================

def load_alerts():
    try:
        with open("rss_alerts.json", "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return []


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
        except openai.error.RateLimitError as e:
            time.sleep(10)
        except Exception as e:
            time.sleep(5)


# =============================================
# 4) RECHERCHE AVEC SERPAPI (GOOGLE)
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
# 5) ANALYSE DES FLUX RSS
# =============================================

def check_alerts():
    alerts = load_alerts()
    new_alertes_json = []

    for alert in alerts:
        feed = feedparser.parse(alert["rss"])
        sujet = alert["nom"]

        for entry in feed.entries:
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

            if result.lower().startswith("oui"):
                new_alertes_json.append({
                    "sujet": sujet,
                    "titre": entry.title,
                    "analyse": result,
                    "lien": entry.link
                })

    return new_alertes_json


# =============================================
# 6) COMBINAISON RSS + SERPAPI
# =============================================

def full_analysis(sujet):
    google_alerts = search_google_serpapi(sujet)
    relevant_alerts = []

    for result in google_alerts:
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
            relevant_alerts.append({
                "sujet": sujet,
                "titre": result['title'],
                "analyse": analysis,
                "lien": result['link']
            })

    return relevant_alerts


# =============================================
# 7) ROUTES FLASK
# =============================================

@app.route('/api/veille', methods=['POST'])
def veille():
    data = request.json
    sujets = data.get("sujets", [])

    if not sujets:
        return jsonify({"error": "Liste de sujets manquante."}), 400

    results = []

    for sujet in sujets:
        results += full_analysis(sujet)

    results += check_alerts()

    return jsonify(results), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
