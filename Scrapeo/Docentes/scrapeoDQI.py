import requests
from bs4 import BeautifulSoup
import json


URL_PROF = "https://www.qi.fcen.uba.ar/profesores/"
URL_JTP = "https://www.qi.fcen.uba.ar/jtps/"


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

def separar_nombre_dqi(nombre_completo):
    partes = nombre_completo.strip().split()

    idx_punto = None

    # 🔹 buscar el ÚLTIMO punto
    for i in range(len(partes) - 1, -1, -1):
        if "." in partes[i]:
            idx_punto = i
            break

    if idx_punto is not None:
        nombre = " ".join(partes[:idx_punto + 1])
        apellido = " ".join(partes[idx_punto + 1:])
    else:
        nombre = partes[0]
        apellido = " ".join(partes[1:])

    return nombre, apellido


def save_json(data, filename="docentes_dqi.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"docentes": data}, f, indent=2, ensure_ascii=False)


# =========================
# PARSEO
# =========================

def parsear_personas(soup, departamento):
    docentes = []

    personas = soup.select("div.person-desc")

    for p in personas:
        # 🔹 nombre
        nombre_tag = p.select_one(".person-name")
        if not nombre_tag:
            continue

        nombre_completo = nombre_tag.get_text(strip=True)

        # 🔹 mail
        mail_tag = p.find("h6")
        mail = mail_tag.get_text(strip=True) if mail_tag else None

        if mail and "@" not in mail:
            mail = None

        nombre, apellido = separar_nombre_dqi(nombre_completo)

        docentes.append({
            "nombre_completo": nombre_completo,
            "nombre": nombre,
            "apellido": apellido,
            "mail": mail,
            "departamento": departamento
        })

    return docentes


# =========================
# SCRAPER DQI
# =========================

def scrape_dqi():
    docentes = []

    # 🔹 PROFESORES
    html_prof = fetch(URL_PROF)
    soup_prof = BeautifulSoup(html_prof, "html.parser")
    docentes += parsear_personas(soup_prof, "DQI")

    # 🔹 JTPs
    html_jtp = fetch(URL_JTP)
    soup_jtp = BeautifulSoup(html_jtp, "html.parser")
    docentes += parsear_personas(soup_jtp, "DQI")

    return docentes


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    docentes = scrape_dqi()

    save_json(docentes)

    print(f"✅ Total DQI: {len(docentes)}")