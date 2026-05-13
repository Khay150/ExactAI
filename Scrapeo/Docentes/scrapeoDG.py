import requests
from bs4 import BeautifulSoup
import json


URL_DG = "https://gl.fcen.uba.ar/docentes/"


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


def save_json(data, filename="docentes_dg.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"docentes": data}, f, indent=2, ensure_ascii=False)


# =========================
# PARSEO DE BLOQUE TEXTO
# =========================

def parsear_bloque_texto(container, departamento):
    docentes = []

    filas = container.select("p span")

    for fila in filas:
        texto = fila.get_text(strip=True)

        if "→" not in texto:
            continue

        try:
            nombre_part, mail = texto.split("→", 1)

            nombre_completo = nombre_part.strip()
            mail = mail.strip()

            if "@" not in mail:
                mail = None

            nombre, apellido = separar_nombre(nombre_completo)

            docentes.append({
                "nombre_completo": nombre_completo,
                "nombre": nombre,
                "apellido": apellido,
                "mail": mail,
                "departamento": departamento
            })

        except:
            continue

    return docentes


# =========================
# SCRAPER DG
# =========================

def scrape_dg():
    html = fetch(URL_DG)
    soup = BeautifulSoup(html, "html.parser")

    docentes = []

    containers = soup.select("div.elementor-widget-container")

    for i, cont in enumerate(containers):
        texto = cont.get_text(strip=True)

        # 🔹 detectar sección
        if texto == "Profesores" or texto == "Jefes de TP":
            if i + 1 < len(containers):
                siguiente = containers[i + 1]

                docentes += parsear_bloque_texto(siguiente, "DG")

    return docentes


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    docentes = scrape_dg()

    save_json(docentes)

    print(f"✅ Total DG: {len(docentes)}")