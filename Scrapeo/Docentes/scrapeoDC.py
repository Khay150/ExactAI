import requests
from bs4 import BeautifulSoup
import json
import re


URL = "https://www.dc.uba.ar/profesores/"


def fetch(url):
    response = requests.get(url)
    response.raise_for_status()
    return response.text


def parse_docentes(html):
    soup = BeautifulSoup(html, "html.parser")
    docentes = []

    bloques = soup.select("div.fusion-text")

    for bloque in bloques:
        texto = bloque.get_text(separator="\n").strip()

        lineas = [l.strip() for l in texto.split("\n") if l.strip()]

        nombre_completo = None
        mail = None

        for linea in lineas:
        
            # Detectar mail
            if "Email:" in linea:
                mail = linea.replace("Email:", "").strip()
                
            # Detectar nombre (más flexible)
            if not nombre_completo:
                if (
                    "Email:" not in linea
                    and "Dedicación" not in linea
                    and "Profesor" not in linea
                ):
                    nombre_completo = linea

        # Debug (opcional)
        if not nombre_completo or not mail:
            print("⚠️ No parseado:", lineas)
            continue
        
        nombre, apellido = separar_nombre(nombre_completo)

        docentes.append({
            "nombre_completo": nombre_completo,
            "nombre": nombre,
            "apellido": apellido,
            "mail": mail,
            "departamento": "DC"
        })

    return eliminar_duplicados(docentes)


def separar_nombre(nombre_completo):
    # Caso: "Apellido, Nombre"
    if "," in nombre_completo:
        apellido, nombre = [p.strip() for p in nombre_completo.split(",", 1)]
    else:
        # Caso: "Apellido Nombre"
        partes = nombre_completo.split()
        if len(partes) == 1:
            return partes[0], ""
        nombre = partes[-1]
        apellido = " ".join(partes[:-1])

    return nombre, apellido


def eliminar_duplicados(docentes):
    unique = {d["mail"]: d for d in docentes if d["mail"]}
    return list(unique.values())


def save_json(data, filename="docentes_dc.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"docentes": data}, f, indent=2, ensure_ascii=False)

def scrape_dc():
    html = fetch(URL)
    docentes = parse_docentes(html)
    return docentes


if __name__ == "__main__":
    
    docentes = scrape_dc()
    
    save_json(docentes)

    print(f"Docentes scrapeados: {len(docentes)}")