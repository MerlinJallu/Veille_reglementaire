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

# =============================================
# 1) CONFIGURATION / CHARGEMENT DE LA CLÉ OPENAI
# =============================================
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

# Clé SerpApi (stockée dans .env : SERP_API_KEY=...)
SERP_API_KEY = os.getenv("SERP_API_KEY")

# Intervalle entre deux itérations (en secondes)
CHECK_INTERVAL = 1800  # 30 minutes par défaut


# =============================================
# 2) FONCTIONS POUR GÉRER LES FICHIERS JSON
# =============================================

def load_alerts():
    """
    Charge la liste des flux RSS (sous forme de JSON).
    """
    with open("rss_alerts.json", "r", encoding="utf-8") as file:
        return json.load(file)


def load_seen_entries():
    """Charge la liste d'URLs déjà analysées."""
    try:
        with open("seen_entries.json", "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return []


def save_seen_entries(entries):
    """Sauvegarde la liste d'URLs déjà analysées."""
    with open("seen_entries.json", "w", encoding="utf-8") as file:
        json.dump(entries, file, ensure_ascii=False, indent=4)


def save_new_alerts(new_alertes_json):
    """
    Sauvegarde les alertes détectées dans alertes_reglementaires.json
    en les ajoutant à la fin du fichier existant.
    """
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
# 3) GESTION DE L'API OPENAI (GPT) AVEC BACKOFF
# =============================================

def gpt_chat_completion(prompt, model="gpt-4", temperature=0):
    """
    Appel de l'API OpenAI ChatCompletion avec un mécanisme 
    de "retry" en cas de RateLimitError.
    """
    import openai
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


# =============================================
# 4) RECHERCHE AVEC SERPAPI (AU LIEU DE SCRAPING GOOGLE)
# =============================================

def search_google_serpapi(query):
    """
    Effectue une recherche Google via l'API SerpApi.
    Retourne une liste de résultats (title, link).
    """
    if not SERP_API_KEY:
        print("❌ ERREUR: Aucune clé SerpApi détectée. Mets SERP_API_KEY dans .env")
        return []

    # Paramètres pour SerpApi
    params = {
        "engine": "google",
        "q": query,
        "hl": "fr",
        "gl": "fr",
        "api_key": SERP_API_KEY,
        "num": 10  # on récupère ~10 résultats
    }

    # Appel à SerpApi
    search = GoogleSearch(params)
    results_dict = search.get_dict()

    if "organic_results" not in results_dict:
        # En cas d'erreur / blocage / quota dépassé, on peut avoir pas de résultats
        print("Aucun résultat (ou limite SerpApi atteinte).")
        return []

    # On parse 'organic_results' pour récupérer les titres et liens
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
        # On NE met pas verify=False, donc on fait un vrai check SSL
        response = requests.get(url, headers=headers, timeout=10)
        
        # Si le certificat est invalide, SSLError sera levée ici
        soup = BeautifulSoup(response.text, 'html.parser')
        text_parts = []

        for tag in soup.find_all(['p', 'div', 'li', 'span']):
            extracted = tag.get_text(strip=True)
            if len(extracted.split()) > 3:
                text_parts.append(extracted)
        return " ".join(text_parts)

    except SSLError as e:
        print(f"Certificat invalide pour {url} => On ignore : {e}")
        return ""  # On renvoie une chaîne vide pour signaler échec
    except Exception as e:
        print(f"Erreur get_text_content({url}): {e}")
        return ""


def google_search_analysis(query):
    """
    1) Fait une recherche SerpApi pour le mot-clé
    2) Analyse GPT
    3) Ne loggue que les articles "Oui"
    4) Retourne la liste d'alertes pertinentes
    """
    variations = [
        query,
        f"{query} réglementation"
    ]

    all_search_results = []
    for variation in variations:
        # On appelle SerpApi
        results = search_google_serpapi(variation)
        # On ne print que le nombre de résultats, si tu veux :
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

        # On n'affiche rien si GPT répond "Non"
        if analysis_result.lower().startswith("oui"):
            # On log seulement le "Oui"
            print(f"✅ [GPT] {title} => {analysis_result}")
            relevant_alerts.append({
                "sujet": query,
                "titre": title,
                "analyse": analysis_result,
                "lien": url
            })

    return relevant_alerts


# =============================================
# 5) ANALYSE DES FLUX RSS
# =============================================

def check_alerts():
    """
    Parcourt la liste des flux RSS et vérifie via GPT 
    s'il y a un changement réglementaire.
    Retourne la liste d'alertes pertinentes (uniquement les 'Oui').
    """
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
                result = gpt_chat_completion(prompt, model="gpt-4", temperature=0)

                # On enregistre le lien comme vu (GPT a répondu)
                seen_entries.append(entry.link)
                save_seen_entries(seen_entries)

                # On n'affiche rien si "Non"
                if result.lower().startswith("oui"):
                    print(f"✅ [RSS] Article réglementaire : {entry.title} => {result}")
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


# =============================================
# 6) COMBINAISON RSS + SERPAPI
# =============================================

def full_analysis(sujet):
    """
    1) Analyse d'abord les flux RSS (check_alerts)
    2) Fait ensuite une recherche (SerpApi) pour le sujet
    3) Retourne la liste des alertes trouvées
    """
    print(f"\n🔍 Analyse RSS pour : {sujet}...")
    rss_alerts = check_alerts()

    print(f"\n🔍 Recherche Google (SerpApi) pour : {sujet}...")
    google_alerts = google_search_analysis(sujet)

    # Enregistre immédiatement les alertes Google
    if google_alerts:
        save_new_alerts(google_alerts)

    return rss_alerts + google_alerts


# =============================================
# 7) POINT D'ENTRÉE
# =============================================

if __name__ == "__main__":
    print("🚀 Veille active : Recherche de changements réglementaires...\n")

    # Sujets par défaut
    sujets = ["FICT", "EUR-LEX", "CIDEF", "RASFF"]

    for sujet in sujets:
        alerts = full_analysis(sujet)
        print(f"➡️ Nombre d'alertes détectées pour '{sujet}' : {len(alerts)}\n")
        # Petite pause entre deux sujets
        time.sleep(86400)
