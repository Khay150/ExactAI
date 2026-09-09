import requests
from bs4 import BeautifulSoup
import json
import re


def scrape_materias_dc(url):

    response = requests.get(url)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    tabla_div = soup.find("div", class_="table-1")

    if not tabla_div:
        raise Exception("No se encontró la tabla")

    filas = tabla_div.find_all("tr")

    materias = []

    # Salteamos el header
    for fila in filas[1:]:

        columnas = fila.find_all("td")

        if len(columnas) < 5:
            continue

        # -------------------------
        # Materia y turno
        # -------------------------

        nombre_turno = columnas[0].get_text(" ", strip=True)

        materia = nombre_turno
        turno = None

        if "–" in nombre_turno:
            materia, turno = nombre_turno.rsplit("–", 1)

        elif "-" in nombre_turno:
            materia, turno = nombre_turno.rsplit("-", 1)

        materia = materia.strip()

        if turno:
            turno = turno.strip()

        # -------------------------
        # Profesores
        # -------------------------

        profesores_texto = columnas[3].get_text(" ", strip=True)

        profesores = [
            p.strip()
            for p in profesores_texto.split(";")
            if p.strip()
        ]

        # -------------------------
        # Horarios
        # -------------------------

        horarios_texto = columnas[4].get_text("\n", strip=True)

        patron = re.compile(
            r'([^:]+):\s*([A-Za-zÁÉÍÓÚáéíóúÑñ]+)\s+de\s+(\d{1,2}:\d{2})\s+a\s+(\d{1,2}:\d{2})'
        )

        horarios = []

        for match in patron.finditer(horarios_texto):

            horarios.append({
                "tipo": match.group(1).strip(),
                "dia": match.group(2).strip(),
                "hora_inicio": match.group(3),
                "hora_fin": match.group(4)
            })

        materias.append({
            "materia": materia,
            "turno": turno,
            "profesores": profesores,
            "horarios": horarios
        })

    return materias


if __name__ == "__main__":

    url = "https://www.dc.uba.ar/ya-se-encuentran-publicadas-las-materias-del-primer-cuatrimestre-de-2026/"

    datos = scrape_materias_dc(url)

    with open(
        "materias_dc_2026_1c.json",
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            datos,
            f,
            ensure_ascii=False,
            indent=2
        )

    print(f"Se guardaron {len(datos)} materias")