import requests
from bs4 import BeautifulSoup
import json
import re
from urllib.parse import urljoin
import time


BASE_URL = "https://web.dm.uba.ar"
LIST_URL = "https://web.dm.uba.ar/index.php/institucional/integrantes/profesores"


def fetch(url):
    response = requests.get(url)
    response.raise_for_status()
    return response.text


# 🔹 Paso 1: obtener links de docentes
def get_docente_links(html):
    soup = BeautifulSoup(html, "html.parser")
    links = []

    for a in soup.select("a.splms-person-title"):
        href = a.get("href")
        if href:
            full_url = urljoin(BASE_URL, href)
            links.append(full_url)

    return list(set(links))  # evitar duplicados


# 🔹 Paso 2: parsear cada docente
def parse_docente(html):
    soup = BeautifulSoup(html, "html.parser")

    # Nombre
    nombre_tag = soup.select_one("h3.splms-person-title")
    if not nombre_tag:
        return None

    nombre_completo = extraer_nombre_limpio(nombre_tag)

    # Mail
    mail_tag = soup.select_one("p.splms-person-email a")
    mail = mail_tag.text.strip() if mail_tag else None

    if not mail or not es_mail_valido(mail):
        print("⚠️ Mail inválido o faltante:", nombre_completo)
        return None

    nombre, apellido = separar_nombre(nombre_completo)

    return {
        "nombre_completo": nombre_completo,
        "nombre": nombre,
        "apellido": apellido,
        "mail": mail,
        "departamento": "DM"
    }


def separar_nombre(nombre_completo):
    if "," in nombre_completo:
        apellido, nombre = [p.strip() for p in nombre_completo.split(",", 1)]
    else:
        partes = nombre_completo.split()
        nombre = partes[-1]
        apellido = " ".join(partes[:-1])

    return nombre, apellido


def es_mail_valido(mail):
    return re.match(r"[^@]+@[^@]+\.[^@]+", mail)


def eliminar_duplicados(docentes):
    unique = {}
    for d in docentes:
        unique[d["mail"]] = d
    return list(unique.values())


def save_json(data, filename="docentes_dm.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"docentes": data}, f, indent=2, ensure_ascii=False)


def extraer_nombre_limpio(nombre_tag):
    textos = [
        t.strip()
        for t in nombre_tag.contents
        if isinstance(t, str) and t.strip()
    ]
    return textos[0] if textos else None

# 🔥 PIPELINE COMPLETO
def scrape_dm():
    print("🔍 Obteniendo lista de docentes...")
    html = fetch(LIST_URL)

    links = get_docente_links(html)
    print(f"🔗 Docentes encontrados: {len(links)}")

    docentes = []

    for i, link in enumerate(links):
        try:
            print(f"➡️ ({i+1}/{len(links)}) {link}")

            html_doc = fetch(link)
            docente = parse_docente(html_doc)

            if docente:
                docentes.append(docente)

            time.sleep(0.5)  # evitar bloquearte

        except Exception as e:
            print("❌ Error en:", link, e)

    docentes = eliminar_duplicados(docentes)

    return docentes


if __name__ == "__main__":
    
    docentes = scrape_dm()
    
    save_json(docentes)

    print(f"Docentes scrapeados: {len(docentes)}")