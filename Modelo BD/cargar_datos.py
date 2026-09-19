#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Carga los JSON del proyecto (carpeta JSON/) a la base Postgres definida por
Modelo BD/schema.sql.

No modifica NINGUN archivo JSON de origen: los lee, resuelve las referencias
(aula, dia, docente, etc.) en memoria, y escribe el resultado en la base.

Es seguro correrlo mas de una vez: al empezar, vacia (TRUNCATE) todas las
tablas del esquema y vuelve a cargar todo desde cero a partir de los JSON
actuales. Pensado para correr de nuevo cada vez que estos cambien (por
ejemplo con un JSON/horarios_2026_2c.json el proximo cuatrimestre).

Uso:
    python cargar_datos.py
    python cargar_datos.py --dsn postgresql://usuario:pass@localhost:5432/exactai
    python cargar_datos.py --data-dir "JSON" --reporte reporte_carga_bd.json

Si no se pasa --dsn, psycopg2 arma la conexion con las variables de entorno
estandar de libpq (PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD); si no hay
ninguna seteada, prueba localhost con los defaults de tu instalacion.

Requiere: psycopg2-binary  ->  pip install psycopg2-binary
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras


# =============================================================================
# CONEXION A LA BASE: completa estos 5 datos con los de tu Postgres local.
#
# Se usan solo si NO le pasas --dsn por linea de comandos y NO tenes seteadas
# las variables de entorno PGHOST/PGDATABASE (en ese caso se ignoran estos
# valores y se usa eso en su lugar). O sea: para el uso normal, con dejar esto
# completo alcanza.
# =============================================================================

DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "postgres"        # el nombre de la base que creaste en tu Postgres
DB_USER = "postgres"        # tu usuario de Postgres
DB_PASSWORD = "admin"            # tu password de Postgres


# =============================================================================
# Normalizacion (mismos criterios que generar_aulas.py / combinar_horarios.py)
# =============================================================================

def quitar_acentos(s):
    s = unicodedata.normalize("NFD", str(s))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def norm_nombre(s):
    s = quitar_acentos(s).lower().strip()
    return re.sub(r"\s+", " ", s)


PREFIJOS_AULA = ["aula", "sala", "biblioteca", "centro de"]


def norm_aula(s):
    """Misma idea que normalizar() en generar_aulas.py: saca acentos, pasa a
    minuscula, saca lo que esta entre parentesis, saca prefijos tipo 'Aula'/
    'Sala'/'Labo' y colapsa espacios, para poder matchear el mismo lugar
    escrito de formas distintas."""
    s = quitar_acentos(str(s)).lower()
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for pref in PREFIJOS_AULA:
        if s.startswith(pref + " "):
            s = s[len(pref) + 1:].strip()
            break
    return s


def slugify(s):
    s = quitar_acentos(s).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "sin_nombre"


def to_int(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or not re.fullmatch(r"-?\d+", s):
        return None
    return int(s)


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# =============================================================================
# Reporte de incidencias (lo que no matcheo / se creo sobre la marcha)
# =============================================================================

class Reporte:
    def __init__(self):
        self.aulas_sin_match = []
        self.docentes_creados_desde_horarios = []
        self.bloques_sin_ninguna_aula_en_origen = 0

    def as_dict(self):
        return {
            "aulas_sin_match": self.aulas_sin_match,
            "docentes_creados_desde_horarios": sorted(set(self.docentes_creados_desde_horarios)),
            "bloques_sin_ninguna_aula_en_origen": self.bloques_sin_ninguna_aula_en_origen,
        }


# =============================================================================
# Borrado (para que el script se pueda correr mas de una vez sin duplicar)
# =============================================================================

TABLAS = [
    "bloque_horario_aula", "bloque_horario", "comision_docente", "comision",
    "evento", "correlativa", "carrera_materia", "grupo_materia", "grupo_optativo",
    "docente_departamento", "docente", "carrera", "materia",
    "aula", "cuatrimestre", "dia", "departamento",
]


def truncar_todo(cur):
    cur.execute("TRUNCATE TABLE {} RESTART IDENTITY CASCADE".format(", ".join(TABLAS)))


# =============================================================================
# Catalogos
# =============================================================================

def cargar_departamentos(cur, data_dir):
    deps = load(data_dir / "departamento.json")
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO departamento (id_departamento, nombre, url) VALUES %s",
        [(d["id_departamento"], d["nombre"], d.get("url")) for d in deps],
    )


def cargar_dias(cur, data_dir):
    dias = load(data_dir / "dia.json")
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO dia (id_dia, nombre) VALUES %s",
        [(d["id_dia"], d["nombre"]) for d in dias],
    )
    return {d["nombre"]: d["id_dia"] for d in dias}  # "Martes" -> "martes"


def cargar_cuatrimestres(cur, data_dir):
    cuatris = load(data_dir / "cuatrimestres_2026.json")["cuatrimestres"]
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO cuatrimestre (id_cuatri, anio, periodo, tipo, orden) VALUES %s",
        [(c["id_cuatri"], c["anio"], str(c["periodo"]), c["tipo"], c["orden"]) for c in cuatris],
    )


def cargar_aulas(cur, data_dir):
    aulas = load(data_dir / "aula.json")
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO aula (id_aula, numero, pabellon, aliases) VALUES %s",
        [(a["id_aula"], str(a["numero"]), a["pabellon"], a.get("aliases") or []) for a in aulas],
    )

    exact = {}                       # (pabellon, "texto tal cual") -> id_aula
    por_pabellon = defaultdict(dict)  # pabellon -> {texto normalizado -> id_aula}
    for a in aulas:
        exact[(a["pabellon"], str(a["numero"]))] = a["id_aula"]
        por_pabellon[a["pabellon"]][norm_aula(a["numero"])] = a["id_aula"]
        for alias in a.get("aliases", []):
            exact[(a["pabellon"], str(alias))] = a["id_aula"]
            por_pabellon[a["pabellon"]][norm_aula(alias)] = a["id_aula"]
    return exact, por_pabellon


def resolver_aula(pabellon, valor, exact, por_pabellon, reporte):
    if pabellon is None or valor is None or valor == "":
        reporte.aulas_sin_match.append({"pabellon": pabellon, "aula": valor})
        return None
    clave = (pabellon, str(valor))
    if clave in exact:
        return exact[clave]
    id_aula = por_pabellon.get(pabellon, {}).get(norm_aula(valor))
    if id_aula:
        return id_aula
    reporte.aulas_sin_match.append({"pabellon": pabellon, "aula": valor})
    return None


# =============================================================================
# Materias / carreras
# =============================================================================

def cargar_materias(cur, data_dir):
    data = load(data_dir / "carreras_materias.json")
    materias = data["materias"]
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO materia (id, carga_horaria, aliases_externos) VALUES %s",
        [
            (m["id"], to_int(m.get("carga_horaria")), psycopg2.extras.Json(m.get("aliases_externos") or {}))
            for m in materias
        ],
    )
    return data


def cargar_carreras_y_plan(cur, data):
    carreras = data["carreras"]
    grupos = data["grupos_optativos"]

    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO carrera (id, nombre, duracion, titulo, cant_materias_obligatorias, "
        "modalidad_optativas, cant_materias_optativas) VALUES %s",
        [
            (c["id"], c["nombre"], c.get("duracion"), c.get("titulo"),
             c.get("cant_materias_obligatorias"), c.get("modalidad_optativas"),
             c.get("cant_materias_optativas"))
            for c in carreras
        ],
    )

    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO grupo_optativo (id_grupo, nombre, cantidad_a_elegir) VALUES %s",
        [(g["id"], g["nombre"], g.get("cantidad_a_elegir")) for g in grupos],
    )

    grupo_materia_filas = sorted({(g["id"], mid) for g in grupos for mid in g.get("materias", [])})
    if grupo_materia_filas:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO grupo_materia (id_grupo, id_materia) VALUES %s",
            grupo_materia_filas,
        )

    carrera_materia_filas = []
    correlativa_filas = set()
    for c in carreras:
        for item in c["plan"]:
            carrera_materia_filas.append((
                c["id"], item.get("materia_id"), item.get("grupo_id"),
                item.get("tipo"), item.get("tipo_materia"),
                item.get("anio"), item.get("cuatrimestre"),
            ))
            mid = item.get("materia_id")
            if mid:
                for corr in item.get("correlativas") or []:
                    correlativa_filas.add((c["id"], mid, corr))

    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO carrera_materia (id_carrera, id_materia, id_grupo, tipo, "
        "tipo_materia, anio, cuatrimestre) VALUES %s",
        carrera_materia_filas,
    )
    if correlativa_filas:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO correlativa (id_carrera, id_materia, id_materia_correlativa) VALUES %s",
            sorted(correlativa_filas),
        )


# =============================================================================
# Docentes
# =============================================================================

def cargar_docentes(cur, data_dir):
    docentes = load(data_dir / "docentes_total.json")["docentes"]
    lut = {}          # "nombre apellido" normalizado -> id_docente
    ids_usados = set()
    filas = []
    filas_dep = []
    for d in docentes:
        base = slugify(d["nombre_completo"])
        id_docente = base
        i = 1
        while id_docente in ids_usados:
            i += 1
            id_docente = f"{base}_{i}"
        ids_usados.add(id_docente)

        filas.append((id_docente, d["nombre"], d["apellido"], d["nombre_completo"], d.get("mail") or None))
        for dep in d.get("departamentos", []):
            filas_dep.append((id_docente, dep))
        lut[norm_nombre(f"{d['nombre']} {d['apellido']}")] = id_docente

    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO docente (id_docente, nombre, apellido, nombre_completo, mail) VALUES %s",
        filas,
    )
    if filas_dep:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO docente_departamento (id_docente, id_departamento) VALUES %s",
            sorted(set(filas_dep)),
        )
    return lut, ids_usados


def resolver_o_crear_docente(cur, nombre_crudo, lut, ids_usados, reporte):
    """Los profesores que aparecen en horarios_2026_1c.json vienen como
    'Nombre Apellido' (ya convertido en combinar_horarios.py). docentes_total.json
    solo cubre el personal de planta oficial de cada departamento, no todos los
    ayudantes/JTP que dan practicas -> una fraccion importante no va a matchear
    (medido: ~66% de las apariciones profesor+comision). Para esos, se crea un
    registro de docente nuevo a partir del nombre tal cual aparece en el horario,
    y se deja anotado en el reporte para revisar mail/departamento a mano si hace
    falta."""
    clave = norm_nombre(nombre_crudo)
    if clave in lut:
        return lut[clave]

    partes = nombre_crudo.strip().split(" ")
    if len(partes) >= 2:
        nombre, apellido = " ".join(partes[:-1]), partes[-1]
    else:
        nombre, apellido = nombre_crudo, ""

    base = slugify(nombre_crudo)
    id_docente = base
    i = 1
    while id_docente in ids_usados:
        i += 1
        id_docente = f"{base}_{i}"
    ids_usados.add(id_docente)

    cur.execute(
        "INSERT INTO docente (id_docente, nombre, apellido, nombre_completo, mail) "
        "VALUES (%s, %s, %s, %s, NULL)",
        (id_docente, nombre, apellido, nombre_crudo),
    )
    lut[clave] = id_docente
    reporte.docentes_creados_desde_horarios.append(nombre_crudo)
    return id_docente


# =============================================================================
# Eventos
# =============================================================================

def cargar_eventos(cur, data_dir):
    eventos = load(data_dir / "eventos.json")["eventos"]
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO evento (titulo, descripcion, fecha_ini, fecha_fin, tipo, id_cuatri) VALUES %s",
        [
            (e["titulo"], e.get("descripcion"), e["fecha_ini"], e.get("fecha_fin"),
             e.get("tipo"), e.get("id_cuatri"))
            for e in eventos
        ],
    )


# =============================================================================
# Comisiones, bloques horarios y aulas de cada bloque
# =============================================================================

def cargar_comisiones(cur, data_dir, dia_lut, aula_exact, aula_por_pabellon,
                       docente_lut, docente_ids_usados, reporte):
    horarios = load(data_dir / "horarios_2026_1c.json")

    for c in horarios:
        turno_comision = next((h.get("turno") for h in c["horarios"] if h.get("turno")), None)
        cur.execute(
            "INSERT INTO comision (id_materia, id_cuatri, comision, turno) "
            "VALUES (%s, %s, %s, %s) RETURNING id_comision",
            (c["materia"], c["id_cuatri"], c["comision"], turno_comision),
        )
        id_comision = cur.fetchone()[0]

        for p in c.get("profesores", []):
            id_docente = resolver_o_crear_docente(cur, p["nombre"], docente_lut, docente_ids_usados, reporte)
            cur.execute(
                "INSERT INTO comision_docente (id_comision, id_docente, rol) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (id_comision, id_docente, p.get("rol") or "Sin rol"),
            )

        for h in c.get("horarios", []):
            id_dia = dia_lut.get(h["dia"])
            if id_dia is None:
                raise ValueError(f"Dia desconocido: {h['dia']!r} en comision {c['comision']!r}")

            cur.execute(
                "INSERT INTO bloque_horario (id_comision, id_dia, hora_inicio, hora_fin, turno, tipo) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id_bloque",
                (id_comision, id_dia, h["hora_inicio"], h["hora_fin"], h.get("turno"), h.get("tipo")),
            )
            id_bloque = cur.fetchone()[0]

            aulas_del_horario = h.get("aulas") or []
            if not aulas_del_horario:
                reporte.bloques_sin_ninguna_aula_en_origen += 1
            for a in aulas_del_horario:
                id_aula = resolver_aula(a.get("pabellon"), a.get("aula"), aula_exact, aula_por_pabellon, reporte)
                if id_aula:
                    cur.execute(
                        "INSERT INTO bloque_horario_aula (id_bloque, id_aula) VALUES (%s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (id_bloque, id_aula),
                    )


# =============================================================================
# main
# =============================================================================

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default="JSON",
                     help="carpeta con los JSON de origen (default: JSON)")
    ap.add_argument("--dsn", default=None,
                     help="cadena de conexion postgresql://... (default: variables PG* / localhost)")
    ap.add_argument("--reporte", default="reporte_carga_bd.json",
                     help="donde guardar el reporte de incidencias (default: reporte_carga_bd.json)")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        sys.exit(f"No encuentro la carpeta de datos: {data_dir.resolve()}")

    if args.dsn:
        dsn = args.dsn
    elif os.environ.get("PGHOST") or os.environ.get("PGDATABASE"):
        dsn = None  # hay variables de entorno PG* seteadas: que las use psycopg2 directo
    else:
        dsn = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    conn = psycopg2.connect(dsn) if dsn else psycopg2.connect()
    reporte = Reporte()

    try:
        with conn.cursor() as cur:
            print("Vaciando tablas...")
            truncar_todo(cur)

            print("Catalogos (departamento, dia, cuatrimestre, aula)...")
            cargar_departamentos(cur, data_dir)
            dia_lut = cargar_dias(cur, data_dir)
            cargar_cuatrimestres(cur, data_dir)
            aula_exact, aula_por_pabellon = cargar_aulas(cur, data_dir)

            print("Materias / carreras / plan de estudios / correlativas...")
            data_materias = cargar_materias(cur, data_dir)
            cargar_carreras_y_plan(cur, data_materias)

            print("Docentes...")
            docente_lut, docente_ids_usados = cargar_docentes(cur, data_dir)

            print("Eventos...")
            cargar_eventos(cur, data_dir)

            print("Comisiones, bloques horarios y aulas de cada bloque...")
            cargar_comisiones(cur, data_dir, dia_lut, aula_exact, aula_por_pabellon,
                               docente_lut, docente_ids_usados, reporte)

        conn.commit()
        print("Listo, commit hecho.")
    except Exception:
        conn.rollback()
        print("Hubo un error, se hizo rollback: no se guardo nada en la base.", file=sys.stderr)
        raise
    finally:
        conn.close()

    with open(args.reporte, "w", encoding="utf-8") as f:
        json.dump(reporte.as_dict(), f, ensure_ascii=False, indent=2)

    print()
    print("=== Resumen ===")
    print(f"Aulas del JSON que no matchearon contra el catalogo aula.json: {len(reporte.aulas_sin_match)}")
    print(f"Docentes creados a partir de horarios (no estaban en docentes_total.json): "
          f"{len(set(reporte.docentes_creados_desde_horarios))}")
    print(f"Bloques horarios sin ninguna aula en el JSON de origen: {reporte.bloques_sin_ninguna_aula_en_origen}")
    print(f"Reporte completo en: {args.reporte}")


if __name__ == "__main__":
    main()
