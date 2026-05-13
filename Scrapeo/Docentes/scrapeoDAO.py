import requests
from bs4 import BeautifulSoup
import json


URL_AT = "http://www.at.fcen.uba.ar/institucional/docentes-investigadores-becarios/#Profesores"


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
    partes = nombre_completo.strip().split()
    nombre = " ".join(partes[:-1])
    apellido = partes[-1]
    return nombre, apellido


def save_json(data, filename="docentes_dao.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"docentes": data}, f, indent=2, ensure_ascii=False)


# =========================
# PARSEO DE BLOQUES
# =========================

def parsear_bloques(seccion, departamento):
    docentes = []

    bloques = seccion.select("div.person div.col-xs-12.col-md-6")

    for b in bloques:
        # 🔹 nombre robusto
        h3 = b.find("h3")
        if not h3:
            continue

        a = h3.find("a")
        nombre_completo = a.get_text(strip=True) if a else h3.get_text(strip=True)

        # 🔹 mail
        mail_tag = b.select_one("h6")
        mail = mail_tag.get_text(strip=True) if mail_tag else None

        if mail and "@" not in mail:
            mail = None

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
# SCRAPER AT
# =========================

def scrape_dao():
    html = fetch(URL_AT)
    soup = BeautifulSoup(html, "html.parser")

    docentes = []

    # 🔹 PROFESORES
    seccion_prof = soup.select_one("div#Profesores")
    if seccion_prof:
        docentes += parsear_bloques(seccion_prof, "DAO")
    else:
        print("⚠️ No se encontró sección Profesores")

    # 🔹 JTPs
    seccion_jtp = soup.select_one("div#Jefesdetrabajosprácticos")
    if seccion_jtp:
        docentes += parsear_bloques(seccion_jtp, "DAO")
    else:
        print("⚠️ No se encontró sección JTPs")

    return docentes


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    docentes = scrape_dao()

    save_json(docentes)

    print(f"✅ Total AT: {len(docentes)}")