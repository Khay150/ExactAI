import json
import re
import requests
from bs4 import BeautifulSoup


URL = "https://web.dm.uba.ar/index.php/docencia/materias/horarios?ano=2026&cuatrimestre=1"


# --------------------------------------------------
# Utilidades
# --------------------------------------------------

DIAS = {
    "Lu": "Lunes",
    "Ma": "Martes",
    "Mi": "Miércoles",
    "Ju": "Jueves",
    "Vi": "Viernes",
    "Sa": "Sábado",
    "Do": "Domingo"
}


def normalizar_hora(hora):
    return f"{hora.zfill(2)}:00"


def hora_a_minutos(hora):
    h, m = map(int, hora.split(":"))
    return h * 60 + m


def calcular_turno(hora_inicio):

    h = int(hora_inicio.split(":")[0])

    if h < 12:
        return "mañana"
    elif h < 17:
        return "tarde"
    else:
        return "noche"


def obtener_numero_tipo(texto):

    m = re.search(r"(\d+)", texto)

    if m:
        return m.group(1)

    return None


# --------------------------------------------------
# Parse profesores
# --------------------------------------------------

def parsear_profesores(texto):

    profesores = []

    partes = [p.strip() for p in texto.split("-")]

    for parte in partes:

        parte = parte.strip()

        m = re.match(r"(.+?)\s*\((.*?)\)", parte)

        if m:

            profesores.append({
                "nombre": m.group(1).strip(),
                "rol": m.group(2).strip()
            })

        else:

            profesores.append({
                "nombre": parte,
                "rol": None
            })

    return profesores


# --------------------------------------------------
# Parse horario
# --------------------------------------------------

def parsear_horario(texto):

    texto = texto.replace("\n", " ").strip()

    m = re.search(
        r"([A-Za-z]{2}(?:\s+y\s+[A-Za-z]{2})*)\s*:\s*(\d+)\s*a\s*(\d+)",
        texto
    )

    if not m:
        return None

    dias_txt = m.group(1)
    inicio = normalizar_hora(m.group(2))
    fin = normalizar_hora(m.group(3))

    dias = []

    for d in re.split(r"\s+y\s+", dias_txt):
        dias.append(DIAS.get(d.strip(), d.strip()))

    return {
        "dias": dias,
        "hora_inicio": inicio,
        "hora_fin": fin
    }


# --------------------------------------------------
# Obtener tablas válidas
# --------------------------------------------------

html = requests.get(URL).text
soup = BeautifulSoup(html, "html.parser")

tablas = soup.find_all("table", class_="horarios")

resultado = []


# --------------------------------------------------
# Procesar materia por materia
# --------------------------------------------------

for tabla in tablas:

    materia = tabla.find("caption").get_text(strip=True)

    filas = []

    for tr in tabla.find_all("tr"):

        celdas = tr.find_all("td")

        if len(celdas) < 4:
            continue

        tipo = celdas[0].get_text(" ", strip=True)
        horario_txt = celdas[1].get_text(" ", strip=True)
        profesores_txt = celdas[2].get_text(" ", strip=True)

        horario = parsear_horario(horario_txt)

        if horario is None:
            continue

        filas.append({
            "tipo": tipo,
            "horario": horario,
            "profesores": parsear_profesores(profesores_txt)
        })

    # -----------------------------------------
    # Caso Teórico-Práctica
    # -----------------------------------------

    if (
        len(filas) == 1 and
        "teórico-práctica" in filas[0]["tipo"].lower()
    ):

        h = filas[0]["horario"]

        resultado.append({
            "materia": materia,
            "turno": calcular_turno(h["hora_inicio"]),
            "tipo": "TP",
            "profesores": filas[0]["profesores"],
            "dias": h["dias"],
            "hora_inicio": h["hora_inicio"],
            "hora_fin": h["hora_fin"]
        })

        continue

    # -----------------------------------------
    # Caso Teórica + Práctica simples
    # -----------------------------------------

    if (
        len(filas) == 2 and
        filas[0]["tipo"].lower().startswith("teórica") and
        filas[1]["tipo"].lower().startswith("práctica")
    ):

        inicio = min(
            filas[0]["horario"]["hora_inicio"],
            filas[1]["horario"]["hora_inicio"],
            key=hora_a_minutos
        )

        fin = max(
            filas[0]["horario"]["hora_fin"],
            filas[1]["horario"]["hora_fin"],
            key=hora_a_minutos
        )

        dias = sorted(set(
            filas[0]["horario"]["dias"] +
            filas[1]["horario"]["dias"]
        ))

        profesores = (
            filas[0]["profesores"] +
            filas[1]["profesores"]
        )

        resultado.append({
            "materia": materia,
            "turno": calcular_turno(inicio),
            "tipo": "TP",
            "profesores": profesores,
            "dias": dias,
            "hora_inicio": inicio,
            "hora_fin": fin
        })

        continue

    # -----------------------------------------
    # Casos numerados
    # -----------------------------------------

    teoricas = {}
    practicas = {}
    consultas = {}
    laboratorios = []

    for fila in filas:

        tipo_lower = fila["tipo"].lower()

        if tipo_lower.startswith("laboratorio"):
            laboratorios.append(fila)
            continue

        numero = obtener_numero_tipo(fila["tipo"])

        if numero is None:
            continue

        if tipo_lower.startswith("teórica"):
            teoricas[numero] = fila

        elif tipo_lower.startswith("práctica"):
            practicas[numero] = fila

        elif tipo_lower.startswith("consulta"):
            consultas[numero] = fila

    numeros = (
        set(teoricas.keys())
        | set(practicas.keys())
        | set(consultas.keys())
    )

    for numero in sorted(numeros, key=int):

        filas_grupo = []

        if numero in teoricas:
            filas_grupo.append(teoricas[numero])

        if numero in practicas:
            filas_grupo.append(practicas[numero])

        if numero in consultas:
            filas_grupo.append(consultas[numero])

        if not filas_grupo:
            continue

        inicio = min(
            (
                f["horario"]["hora_inicio"]
                for f in filas_grupo
            ),
            key=hora_a_minutos
        )

        fin = max(
            (
                f["horario"]["hora_fin"]
                for f in filas_grupo
            ),
            key=hora_a_minutos
        )

        dias = []
        profesores = []

        for f in filas_grupo:

            dias.extend(
                f["horario"]["dias"]
            )

            profesores.extend(
                f["profesores"]
            )

        resultado.append({
            "materia": materia,
            "turno": calcular_turno(inicio),
            "tipo": f"TP{numero}",
            "profesores": profesores,
            "dias": sorted(set(dias)),
            "hora_inicio": inicio,
            "hora_fin": fin
        })

    # -----------------------------------------
    # Laboratorios
    # -----------------------------------------

    for fila in laboratorios:

        h = fila["horario"]

        resultado.append({
            "materia": materia,
            "turno": calcular_turno(
                h["hora_inicio"]
            ),
            "tipo": "PC",
            "profesores": fila["profesores"],
            "dias": h["dias"],
            "hora_inicio": h["hora_inicio"],
            "hora_fin": h["hora_fin"]
        })


# --------------------------------------------------
# Guardar
# --------------------------------------------------

with open(
    "dm_2026_1c.json",
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        resultado,
        f,
        ensure_ascii=False,
        indent=2
    )

print(
    f"Se guardaron {len(resultado)} comisiones"
)