import feedparser
import openai
import os
import json
import time
from dotenv import load_dotenv

load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

CHECK_INTERVAL = 1800  # Toutes les 30 min

# Charger la liste des flux RSS depuis un fichier
def load_alerts():
    with open("rss_alerts.json", "r", encoding="utf-8") as file:
        return json.load(file)

# Charger les entrées déjà traitées
def load_seen_entries():
    try:
        with open("seen_entries.json", "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return []

# Sauvegarder les entrées traitées
def save_seen_entries(entries):
    with open("seen_entries.json", "w", encoding="utf-8") as file:
        json.dump(entries, file, ensure_ascii=False, indent=4)

# Générer le résumé IA avec GPT-4
def summarize_entry(entry, sujet):
    prompt = f"""
    Résume précisément cette publication réglementaire liée au sujet '{sujet}' :

    Titre : {entry.title}
    Contenu : {entry.summary}

    Format attendu :
    - Résumé clair :
    - Impacts / Restrictions :
    - Catégorie :
    """
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    return response.choices[0].message.content.strip()

# Ajouter automatiquement l'alerte au json
def add_new_alert(question):
    keywords = question.replace(" ", "%20")
    rss_url = f"https://news.google.com/rss/search?q={keywords}&hl=fr&gl=FR&ceid=FR:fr"

    alerts = load_alerts()
    alerts.append({
        "nom": question,
        "rss": rss_url
    })

    with open("rss_alerts.json", "w", encoding="utf-8") as file:
        json.dump(alerts, file, ensure_ascii=False, indent=4)

    print(f"✅ Nouvelle alerte ajoutée pour '{question}' avec flux RSS : {rss_url}")

# Vérifier et intégrer les nouveaux sujets depuis un fichier texte
def check_new_subjects():
    if os.path.exists("nouveaux_sujets.txt"):
        with open("nouveaux_sujets.txt", "r", encoding="utf-8") as file:
            lines = [line.strip() for line in file if line.strip()]

        if lines:
            for sujet in lines:
                add_new_alert(sujet)

            # Effacer le fichier après avoir ajouté les sujets
            open("nouveaux_sujets.txt", "w", encoding="utf-8").close()

# Fonction principale optimisée pour Power Automate
def check_alerts():
    while True:
        check_new_subjects()

        alerts = load_alerts()
        seen_entries = load_seen_entries()
        new_alertes_json = []

        for alert in alerts:
            feed = feedparser.parse(alert["rss"])
            sujet = alert["nom"]

            for entry in feed.entries:
                if entry.link not in seen_entries:
                    summary = summarize_entry(entry, sujet)

                    new_alert = {
                        "sujet": sujet,
                        "titre": entry.title,
                        "resume": summary,
                        "date": entry.published,
                        "lien": entry.link
                    }

                    new_alertes_json.append(new_alert)

                    seen_entries.append(entry.link)
                    save_seen_entries(seen_entries)

                    print(f"✅ Nouvel article traité : {entry.title}")

                    time.sleep(2)

        if new_alertes_json:
            timestamp = int(time.time())
            filename = f"alertes_{timestamp}.json"
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(new_alertes_json, f, ensure_ascii=False, indent=4)

            print(f"✅ Nouveau fichier créé pour Power Automate : {filename}")
        else:
            print("ℹ️ Aucune nouvelle alerte détectée.")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    print("🚀 Veille réglementaire active pour Power Automate...")
    check_alerts()