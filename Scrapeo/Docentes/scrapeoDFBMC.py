import requests
from bs4 import BeautifulSoup
import json


URL_PROF = "https://www.fbmc.fcen.uba.ar/institucional/docentes/profesores/"
URL_JTP = "https://www.fbmc.fcen.uba.ar/institucional/docentes/jtp/"


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

def limpiar_y_separar_nombre(nombre_completo):
    partes = nombre_completo.strip().split()

    # 🔹 eliminar prefijos
    if partes and partes[0] in ["Dr.", "Dra.", "Dra", "Dr"]:
        partes = partes[1:]

    # 🔹 buscar último punto
    idx_punto = None
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


def extraer_mail(item):
    a = item.select_one("div.stm-teacher__socials a[href^='mailto:']")
    if a:
        return a["href"].replace("mailto:", "").strip()
    return None


# =========================
# DEDUPLICACIÓN
# =========================

def deduplicar_docentes(lista):
    
    unicos = {}

    for d in lista:
        key = d["mail"] if d["mail"] else d["nombre_completo"].lower()

        if key not in unicos:
            unicos[key] = d
        else:
            # completar mail si antes era None
            if not unicos[key]["mail"] and d["mail"]:
                unicos[key]["mail"] = d["mail"]

    return list(unicos.values())


# =========================
# PARSEO
# =========================

def parsear_docentes(soup, departamento):
    docentes = []

    items = soup.select("div.stm-teacher__info")

    for item in items:
        nombre_tag = item.select_one("div.stm-teacher__name")
        if not nombre_tag:
            continue

        nombre_completo = nombre_tag.get_text(strip=True)

        mail = extraer_mail(item)

        if mail and "@" not in mail:
            mail = None

        nombre, apellido = limpiar_y_separar_nombre(nombre_completo)

        docentes.append({
            "nombre_completo": nombre_completo,
            "nombre": nombre,
            "apellido": apellido,
            "mail": mail,
            "departamento": departamento
        })

    return docentes


# =========================
# SCRAPER PRINCIPAL
# =========================

def scrape_dfbmc():
    docentes = []

    # 🔹 Profesores
    html_prof = fetch(URL_PROF)
    soup_prof = BeautifulSoup(html_prof, "html.parser")
    docentes += parsear_docentes(soup_prof, "DFBMC")

    # 🔹 JTPs
    html_jtp = fetch(URL_JTP)
    soup_jtp = BeautifulSoup(html_jtp, "html.parser")
    docentes += parsear_docentes(soup_jtp, "DFBMC")

    # 🔥 deduplicación final
    docentes = deduplicar_docentes(docentes)

    return docentes


# =========================
# GUARDADO JSON
# =========================

def save_json(data, filename="docentes_dfbmc.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"docentes": data}, f, indent=2, ensure_ascii=False)


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    docentes = scrape_dfbmc()

    save_json(docentes)

    print(f"✅ Total DFBMC (sin duplicados): {len(docentes)}")