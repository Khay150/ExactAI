import requests
from bs4 import BeautifulSoup
import json


URL_PROF = "http://qb.fcen.uba.ar/personal/profesores/"
URL_JTP = "http://qb.fcen.uba.ar/personal/jefe-de-trabajos-practicos/"


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


def limpiar_texto(texto):
    return texto.replace("\xa0", "").strip()


def save_json(data, filename="docentes_dqb.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"docentes": data}, f, indent=2, ensure_ascii=False)


# =========================
# PARSEO LISTA
# =========================

def parsear_lista(soup, departamento):
    docentes = []

    items = soup.select("ul li")

    for li in items:
        # 🔹 mail
        a = li.find("a", href=True)
        mail = None

        if a and "mailto:" in a["href"]:
            mail = a["href"].replace("mailto:", "").strip()

        # 🔹 nombre (solo texto del li, sin span)
        textos = [
            t.strip()
            for t in li.contents
            if isinstance(t, str) and t.strip()
        ]

        if not textos:
            continue

        nombre_completo = limpiar_texto(textos[0])

        if not nombre_completo:
            continue

        nombre, apellido = separar_nombre(nombre_completo)

        docentes.append({
            "nombre_completo": nombre_completo,
            "nombre": nombre,
            "apellido": apellido,
            "mail": mail,
            "departamento": departamento
        })

    return docentes


# =========================
# SCRAPER DQB
# =========================

def scrape_dqb():
    docentes = []

    # 🔹 PROFESORES
    html_prof = fetch(URL_PROF)
    soup_prof = BeautifulSoup(html_prof, "html.parser")

    docentes += parsear_lista(soup_prof, "DQB")

    # 🔹 JTPs
    html_jtp = fetch(URL_JTP)
    soup_jtp = BeautifulSoup(html_jtp, "html.parser")

    docentes += parsear_lista(soup_jtp, "DQB")

    return docentes


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    docentes = scrape_dqb()

    save_json(docentes)

    print(f"✅ Total DQB: {len(docentes)}")