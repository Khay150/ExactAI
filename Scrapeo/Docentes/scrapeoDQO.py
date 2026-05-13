import requests
from bs4 import BeautifulSoup
import json


URL_DQO = "https://www.qo.fcen.uba.ar/personal/profesores/"


# =========================
# FETCH
# =========================

def fetch(url):
    r = requests.get(url)
    r.raise_for_status()
    return r.text


# =========================
# UTILIDADES
# =========================

def separar_nombre(nombre_completo):
    if "," in nombre_completo:
        apellido, nombre = [p.strip() for p in nombre_completo.split(",", 1)]
    else:
        partes = nombre_completo.split()
        nombre = partes[-1]
        apellido = " ".join(partes[:-1])
    return nombre, apellido

def extraer_mail(item):
    # mailto
    mail_tag = item.select_one("div.social a[href^='mailto:']")
    if mail_tag:
        return mail_tag["href"].replace("mailto:", "").strip()

    # texto plano
    social = item.select_one("div.social")
    if social:
        texto = social.get_text(" ", strip=True)

        for palabra in texto.split():
            if "@" in palabra:
                return palabra.strip()

    return None


def save_json(data, filename="docentes_dqo.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"docentes": data}, f, indent=2, ensure_ascii=False)


# =========================
# SCRAPER DQO
# =========================

def scrape_dqo():
    html = fetch(URL_DQO)
    soup = BeautifulSoup(html, "html.parser")

    docentes = []

    items = soup.select("div.item")

    for item in items:
        # 🔹 nombre
        nombre_tag = item.select_one("h5.entry-title a")
        if not nombre_tag:
            continue

        nombre_completo = nombre_tag.get_text(strip=True)

        # 🔹 mail
        mail = extraer_mail(item)
        
        if mail and "@" not in mail:
            mail = None

        nombre, apellido = separar_nombre(nombre_completo)

        docentes.append({
            "nombre_completo": nombre_completo,
            "nombre": nombre,
            "apellido": apellido,
            "mail": mail,
            "departamento": "DQO"
        })

    return docentes


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    docentes = scrape_dqo()

    save_json(docentes)

    print(f"✅ Total DQO: {len(docentes)}")