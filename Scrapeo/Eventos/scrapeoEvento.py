import json
import re
import requests
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime

URL = "https://exactas.uba.ar/calendario-academico/"


# =========================
# FETCH
# =========================

def fetch(url):
    r = requests.get(url)
    r.raise_for_status()
    return r.text


# =========================
# CONVERSIÓN A ISO
# =========================

def a_iso(fecha_str):
    if not fecha_str:
        return None

    try:
        return datetime.strptime(fecha_str, "%d/%m/%Y").strftime("%Y-%m-%d")
    except:
        print("⚠️ Fecha inválida:", fecha_str)
        return None

# =========================
# INFERIR TIPO
# =========================

def inferir_tipo(titulo):
    t = titulo.lower()

    if "inscripción" in t:
        return "Inscripción"
    if "clases" in t:
        return "Clases"
    if "exámenes" in t:
        return "Exámenes"
    if "encuesta" in t:
        return "Encuestas"
    if "publicación" in t:
        return "Publicación"
    if "receso" in t:
        return "Receso"
    if "semana de la" in t:
        return "Semana de las Ciencias"

    return None

# =========================
# CALCULAR CUATRIMESTRE
# =========================

def calcular_id_cuatri(evento):
    titulo = evento["titulo"].lower()
    fecha = evento["fecha_ini"]

    if not fecha:
        return None

    año, mes, _ = fecha.split("-")
    mes = int(mes)

    # =========================
    # INTENSIVO DE INVIERNO
    # =========================
    if "invierno" in titulo:
        return f"{año}_inv"

    # =========================
    # BIMESTRES (con número)
    # =========================
    if "1er bimestre" in titulo or "primer bimestre" in titulo:
        return f"{año}_1b"

    if "2do bimestre" in titulo or "segundo bimestre" in titulo:
        return f"{año}_2b"

    if "3er bimestre" in titulo or "tercer bimestre" in titulo:
        return f"{año}_3b"

    if "4to bimestre" in titulo or "cuarto bimestre" in titulo:
        return f"{año}_4b"

    # =========================
    # VERANO
    # =========================
    if "verano" in titulo or "febrero-marzo" in titulo:
        return f"{año}_verano"

    # =========================
    # CUATRIMESTRES
    # =========================
    if "1er cuatrimestre" in titulo or "primer cuatrimestre" in titulo:
        return f"{año}_1c"

    if "2do cuatrimestre" in titulo or "segundo cuatrimestre" in titulo:
        return f"{año}_2c"

    # =========================
    # FALLBACK POR MES
    # =========================
    if mes in [1, 2, 3]:
        return f"{año}_verano"

    if mes in [4, 5, 6]:
        return f"{año}_1c"

    if mes == 7:
        return f"{año}_inv"  # julio suele ser invierno

    if mes in [8, 9, 10, 11]:
        return f"{año}_2c"

    return None

# =========================
# PARSEAR EVENTO NORMAL (:)
# =========================

def parsear_evento(texto):
    texto = texto.strip().strip('"')

    if ":" not in texto:
        return None

    titulo, resto = texto.split(":", 1)
    titulo = titulo.strip()
    resto = resto.strip()

    # caso "a definir"
    if "definir" in resto.lower():
        return {
            "titulo": titulo,
            "descripcion": None,
            "fecha_ini": None,
            "fecha_fin": None,
            "tipo": inferir_tipo(titulo),
            "id_cuatri": None
        }

    fechas = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", resto)

    if len(fechas) == 0:
        fecha_ini = None
        fecha_fin = None
    elif len(fechas) == 1:
        fecha_ini = a_iso(fechas[0])
        fecha_fin = None
    else:
        fecha_ini = a_iso(fechas[0])
        fecha_fin = a_iso(fechas[1])

    evento = {
        "titulo": titulo,
        "descripcion": None,
        "fecha_ini": fecha_ini,
        "fecha_fin": fecha_fin,
        "tipo": inferir_tipo(titulo),
        "id_cuatri": None
    }
    
    evento["id_cuatri"] = calcular_id_cuatri(evento)

    return evento


# =========================
# SCRAPER
# =========================

def scrape_eventos():
    html = fetch(URL)
    soup = BeautifulSoup(html, "html.parser")

    cont = soup.select_one("div.entry-content")

    eventos = []
    titulo_actual = None

    for tag in cont.find_all(recursive=False):

        # cortar en Almanaque
        if tag.name == "h1" and "Almanaque" in tag.get_text():
            break

        # guardar títulos de contexto
        if tag.name in ["h2", "h3", "h4"]:
            titulo_actual = tag.get_text(strip=True)
            continue

        if tag.name != "p":
            continue

        # 🔥 respeta <br>
        lineas = list(tag.stripped_strings)

        for linea in lineas:

            if not linea or "Solo para personas" in linea:
                continue

            evento = None

            # =====================
            # CASO NORMAL (:)
            # =====================
            if ":" in linea:
                evento = parsear_evento(linea)

            # =====================
            # CASO ESPECIAL (sin :)
            # =====================
            else:
                fechas = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", linea)

                if fechas and titulo_actual:
                    fecha_ini = a_iso(fechas[0])
                    fecha_fin = a_iso(fechas[1] )if len(fechas) > 1 else None

                    evento = {
                        "titulo": titulo_actual,
                        "descripcion": None,
                        "fecha_ini": fecha_ini,
                        "fecha_fin": fecha_fin,
                        "tipo": inferir_tipo(titulo_actual),
                        "id_cuatri": None
                    }
                    
                    evento["id_cuatri"] = calcular_id_cuatri(evento)

            if evento:
                eventos.append(evento)

    return eventos


# =========================
# SAVE JSON
# =========================

def save_json(data, filename="eventos.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"eventos": data}, f, indent=2, ensure_ascii=False)



# =========================
# CARGAR JSON
# =========================

def load_json(filename="eventos.json"):
    with open(filename, "r", encoding="utf-8") as f:
        return json.load(f)["eventos"]

# =========================
# DETECTAR IDS
# =========================

def extraer_ids(evento):
    titulo = evento["titulo"].lower()
    fecha = evento["fecha_ini"]

    ids = set()

    if fecha:
        año, mes, _ = fecha.split("-")
        mes = int(mes)
    else:
        return []

    t = titulo.replace("º", "o").replace("°", "o")

    # dividir por "y"
    partes = re.split(r"\s+y\s+", t)

    for p in partes:

        if "cuatrimestre" in p:
            if re.search(r"\b(1er|1o|primer)", p):
                ids.add(f"{año}_1c")
            elif re.search(r"\b(2do|2o|segundo)", p):
                ids.add(f"{año}_2c")

        if "bimestre" in p:
            if re.search(r"\b(1er|1o|primer)", p):
                ids.add(f"{año}_1b")
            elif re.search(r"\b(2do|2o|segundo)", p):
                ids.add(f"{año}_2b")
            elif re.search(r"\b(3er|3o|tercer)", p):
                ids.add(f"{año}_3b")
            elif re.search(r"\b(4to|4o|cuarto)", p):
                ids.add(f"{año}_4b")

        if "invierno" in p:
            ids.add(f"{año}_inv")

        if "verano" in p:
            ids.add(f"{año}_verano")
            
    if not ids:
        if mes in [12, 1, 2, 3]:
            ids.add(f"{año}_verano")

        elif mes in [4, 5, 6]:
            ids.add(f"{año}_1c")

        elif mes == 7:
            ids.add(f"{año}_inv")

        elif mes in [8, 9, 10, 11]:
            ids.add(f"{año}_2c")


    return list(ids)

# =========================
# PROCESAR
# =========================

def procesar_eventos(eventos):
    nuevos = []

    for e in eventos:
        e["id_cuatri"] = extraer_ids(e)
        nuevos.append(e)

    return nuevos


# =========================
# VERANO + EVENTOS MULTICUATRI
# (antes: corregir_verano.py y separar_eventos_multicuatri.py, que habia que
#  correr despues del scrapeo; ahora el JSON sale ya listo para cargar)
# =========================

ORD = r"(?:1er|1ro|primer|2do|segundo|3er|tercer|4to|cuarto)"
UNIDAD = r"(?:cuatrimestres?|bimestres?)"

# "1er Cuatrimestre y 1er Bimestre", "2do Cuatrimestre y 3er Bimestre", etc.
PAR_MULTI = re.compile(
    rf"(?P<a>{ORD}\.?\s+{UNIDAD})\s+y\s+(?P<b>{ORD}\.?\s+{UNIDAD})",
    re.IGNORECASE,
)


def _singular(txt):
    return re.sub(r"(?i)(cuatrimestre|bimestre)s\b", r"\1", txt)


def dividir_titulo(titulo):
    """'Inscripcion a Materias del 1er Cuatrimestre y 1er Bimestre para X'
    -> ['... del 1er Cuatrimestre para X', '... del 1er Bimestre para X'].
    Devuelve None si el titulo no tiene ese patron."""
    m = PAR_MULTI.search(titulo)
    if not m:
        return None
    ini, fin = titulo[:m.start()], titulo[m.end():]
    return [ini + _singular(m.group("a")) + fin, ini + _singular(m.group("b")) + fin]


def resolver_verano(id_cuatri, fecha_ini):
    """El calendario junta todo el verano en '<anio>_verano'. Se separa en:
      - <anio>_verano_vac : diciembre (publicacion/inscripcion al curso de verano y
                            turno de examenes de diciembre: preparan el verano que viene)
      - <anio>_verano_int : enero-marzo (curso intensivo y turno Febrero-Marzo)"""
    if not id_cuatri.endswith("_verano"):
        return id_cuatri
    mes = int(fecha_ini.split("-")[1])
    return id_cuatri + ("_vac" if mes == 12 else "_int")


def normalizar_eventos(eventos):
    """Deja a cada evento con UN solo id_cuatri (string, o None si todavia no tiene
    fecha): parte los eventos que valen para dos periodos y separa el verano."""
    salida = []
    for e in eventos:
        ids = e.get("id_cuatri") or []
        if isinstance(ids, str):
            ids = [ids]

        if len(ids) > 1:
            titulos = dividir_titulo(e["titulo"])
            if titulos:
                variantes = []
                for t in titulos:
                    v = dict(e)
                    v["titulo"] = t
                    v["id_cuatri"] = extraer_ids(v)
                    variantes.append(v)
            else:
                print("⚠️ Evento con varios periodos y titulo sin patron conocido; "
                      f"se duplica sin cambiar el titulo: {e['titulo']!r} {ids}")
                variantes = [dict(e, id_cuatri=[i]) for i in sorted(ids)]
        else:
            variantes = [dict(e, id_cuatri=ids)]

        for v in variantes:
            vids = v["id_cuatri"]
            if len(vids) > 1:
                print(f"⚠️ No se pudo asignar un unico periodo a {v['titulo']!r}: {vids}")
            v["id_cuatri"] = resolver_verano(vids[0], v["fecha_ini"]) if vids else None
            salida.append(v)

    return salida


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    eventos = scrape_eventos()

    print(f"Eventos encontrados: {len(eventos)}")

    save_json(eventos)
    
    eventos = load_json()

    print(f"Eventos originales: {len(eventos)}")

    nuevos = procesar_eventos(eventos)

    print(f"Eventos procesados: {len(nuevos)}")

    nuevos = normalizar_eventos(nuevos)

    print(f"Eventos finales (multicuatri separados, verano resuelto): {len(nuevos)}")

    save_json(nuevos)