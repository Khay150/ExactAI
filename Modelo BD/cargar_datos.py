#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Carga los JSON del proyecto (carpeta JSON/) a la base Postgres definida por
Modelo BD/schema.sql, de forma INCREMENTAL: no vacia la base.

Que hace en cada corrida (todo en UNA transaccion; si algo falla no se guarda nada):
  - Catalogos y plan de estudios (departamento, dia, cuatrimestre, aula, materia,
    carrera, grupos, correlativas): upsert a partir de los JSON. El plan de estudios
    (carrera_materia / correlativa / grupo_materia) se sincroniza completo porque sale
    de un unico archivo y nada lo referencia.
  - Docentes: upsert. Los docentes que ya estaban en la base (por ejemplo los creados
    a partir de horarios de cuatrimestres anteriores) se reutilizan, no se duplican.
  - Comisiones: SOLO se tocan las de los cuatrimestres que aparecen en los archivos
    de horarios que le pases con --horarios. Las de otros cuatrimestres quedan intactas.
    Las comisiones que ya no estan en el JSON de ese cuatrimestre se eliminan, salvo que
    sean demasiadas (proteccion contra un scraping incompleto; ver --forzar).
  - Eventos: se reemplazan los de los cuatrimestres presentes en eventos.json (y los que
    no tienen cuatrimestre). Los de otros cuatrimestres quedan intactos.

No modifica NINGUN archivo JSON de origen.

Uso tipico (el cuatrimestre nuevo ya tiene su horarios_<cuatri>.json):
    python cargar_datos.py --horarios horarios_2026_2c.json
    python cargar_datos.py --horarios horarios_2026_2c.json --dry-run     # solo muestra que cambiaria
    python cargar_datos.py --horarios horarios_2026_1c.json horarios_2026_2c.json
    python cargar_datos.py --reset --horarios horarios_2026_1c.json       # como antes: vacia y recarga

Otras opciones:
    --data-dir JSON            carpeta con los JSON (default: JSON)
    --eventos eventos.json     archivo de eventos (default: eventos.json)
    --cuatrimestres cuatrimestres_2026.json
    --docentes docentes_total.json
    --dsn postgresql://usuario:pass@localhost:5432/postgres
    --reporte reporte_carga_bd.json

Si no se pasa --dsn, psycopg2 arma la conexion con las variables de entorno
estandar de libpq (PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD); si no hay
ninguna seteada, usa los valores de DB_* de mas abajo.

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


# Si una corrida eliminaria mas de este porcentaje de las comisiones ya cargadas de un
# cuatrimestre (y al menos MIN_ELIMINAR), aborta: lo mas probable es un scraping incompleto.
MAX_ELIMINAR_PCT = 30
MIN_ELIMINAR = 5


class CargaAbortada(Exception):
    """La carga se frena a proposito (se hace rollback); el mensaje explica por que."""


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


def resolver_archivo(data_dir, nombre):
    p = Path(nombre)
    return p if p.is_absolute() else data_dir / p


# =============================================================================
# Reporte de incidencias y de cambios
# =============================================================================

class Reporte:
    def __init__(self):
        self.aulas_sin_match = []
        self.docentes_creados_desde_horarios = []
        self.bloques_sin_ninguna_aula_en_origen = 0
        self.comisiones = {}      # id_cuatri -> {nuevas, modificadas, eliminadas, total}
        self.eventos = {}         # agregados / quitados
        self.tablas_antes = {}
        self.tablas_despues = {}
        self.dry_run = False

    def as_dict(self):
        return {
            "dry_run": self.dry_run,
            "aulas_sin_match": self.aulas_sin_match,
            "docentes_creados_desde_horarios": sorted(set(self.docentes_creados_desde_horarios)),
            "bloques_sin_ninguna_aula_en_origen": self.bloques_sin_ninguna_aula_en_origen,
            "comisiones": self.comisiones,
            "eventos": self.eventos,
            "tablas_antes": self.tablas_antes,
            "tablas_despues": self.tablas_despues,
        }


TABLAS = [
    "bloque_horario_aula", "bloque_horario", "comision_docente", "comision",
    "evento", "correlativa", "carrera_materia", "grupo_materia", "grupo_optativo",
    "docente_departamento", "docente", "carrera", "materia",
    "aula", "cuatrimestre", "dia", "departamento",
]


def contar_filas(cur):
    out = {}
    for t in TABLAS:
        cur.execute("SELECT count(*) FROM {}".format(t))  # nombres fijos de arriba, no input externo
        out[t] = cur.fetchone()[0]
    return out


def truncar_todo(cur):
    cur.execute("TRUNCATE TABLE {} RESTART IDENTITY CASCADE".format(", ".join(TABLAS)))


# =============================================================================
# Catalogos (upsert)
# =============================================================================

def cargar_departamentos(cur, data_dir):
    deps = load(data_dir / "departamento.json")
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO departamento (id_departamento, nombre, url) VALUES %s "
        "ON CONFLICT (id_departamento) DO UPDATE SET nombre = EXCLUDED.nombre, url = EXCLUDED.url",
        [(d["id_departamento"], d["nombre"], d.get("url")) for d in deps],
    )


def cargar_dias(cur, data_dir):
    dias = load(data_dir / "dia.json")
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO dia (id_dia, nombre) VALUES %s "
        "ON CONFLICT (id_dia) DO UPDATE SET nombre = EXCLUDED.nombre",
        [(d["id_dia"], d["nombre"]) for d in dias],
    )
    return {d["nombre"]: d["id_dia"] for d in dias}  # "Martes" -> "martes"


def cargar_cuatrimestres(cur, data_dir, archivo):
    cuatris = load(resolver_archivo(data_dir, archivo))["cuatrimestres"]
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO cuatrimestre (id_cuatri, anio, periodo, tipo, orden) VALUES %s "
        "ON CONFLICT (id_cuatri) DO UPDATE SET anio = EXCLUDED.anio, periodo = EXCLUDED.periodo, "
        "tipo = EXCLUDED.tipo, orden = EXCLUDED.orden",
        [(c["id_cuatri"], c["anio"], str(c["periodo"]), c["tipo"], c["orden"]) for c in cuatris],
    )


def cargar_aulas(cur, data_dir):
    aulas = load(data_dir / "aula.json")
    # Los aliases se ACUMULAN: un alias aprendido en un cuatrimestre anterior no se pierde
    # si el aula.json de ahora (generado a partir de los horarios nuevos) no lo trae.
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO aula (id_aula, numero, pabellon, aliases) VALUES %s "
        "ON CONFLICT (id_aula) DO UPDATE SET numero = EXCLUDED.numero, pabellon = EXCLUDED.pabellon, "
        "aliases = ARRAY(SELECT DISTINCT x FROM unnest(aula.aliases || EXCLUDED.aliases) AS x ORDER BY x)",
        [(a["id_aula"], str(a["numero"]), a["pabellon"], a.get("aliases") or []) for a in aulas],
    )

    cur.execute("SELECT id_aula, numero, pabellon, aliases FROM aula")
    exact = {}                       # (pabellon, "texto tal cual") -> id_aula
    por_pabellon = defaultdict(dict)  # pabellon -> {texto normalizado -> id_aula}
    for id_aula, numero, pabellon, aliases in cur.fetchall():
        exact[(pabellon, str(numero))] = id_aula
        por_pabellon[pabellon][norm_aula(numero)] = id_aula
        for alias in aliases or []:
            exact[(pabellon, str(alias))] = id_aula
            por_pabellon[pabellon][norm_aula(alias)] = id_aula
    return exact, por_pabellon


def resolver_aula(pabellon, valor, exact, por_pabellon, reporte):
    if pabellon is None or valor is None or valor == "":
        reporte.aulas_sin_match.append({"pabellon": pabellon, "aula": valor})
        return None
    try:
        pabellon = int(pabellon)
    except (TypeError, ValueError):
        pass
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
        "INSERT INTO materia (id, carga_horaria, aliases_externos) VALUES %s "
        "ON CONFLICT (id) DO UPDATE SET carga_horaria = EXCLUDED.carga_horaria, "
        "aliases_externos = EXCLUDED.aliases_externos",
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
        "modalidad_optativas, cant_materias_optativas) VALUES %s "
        "ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre, duracion = EXCLUDED.duracion, "
        "titulo = EXCLUDED.titulo, cant_materias_obligatorias = EXCLUDED.cant_materias_obligatorias, "
        "modalidad_optativas = EXCLUDED.modalidad_optativas, "
        "cant_materias_optativas = EXCLUDED.cant_materias_optativas",
        [
            (c["id"], c["nombre"], c.get("duracion"), c.get("titulo"),
             c.get("cant_materias_obligatorias"), c.get("modalidad_optativas"),
             c.get("cant_materias_optativas"))
            for c in carreras
        ],
    )

    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO grupo_optativo (id_grupo, nombre, cantidad_a_elegir) VALUES %s "
        "ON CONFLICT (id_grupo) DO UPDATE SET nombre = EXCLUDED.nombre, "
        "cantidad_a_elegir = EXCLUDED.cantidad_a_elegir",
        [(g["id"], g["nombre"], g.get("cantidad_a_elegir")) for g in grupos],
    )

    # El plan de estudios sale de un solo archivo y ninguna otra tabla lo referencia:
    # se sincroniza completo (borrar + insertar) en vez de acumular filas duplicadas.
    cur.execute("DELETE FROM correlativa")
    cur.execute("DELETE FROM carrera_materia")
    cur.execute("DELETE FROM grupo_materia")

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

def cargar_docentes(cur, data_dir, archivo):
    docentes = load(resolver_archivo(data_dir, archivo))["docentes"]
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
        "INSERT INTO docente (id_docente, nombre, apellido, nombre_completo, mail) VALUES %s "
        "ON CONFLICT (id_docente) DO UPDATE SET nombre = EXCLUDED.nombre, apellido = EXCLUDED.apellido, "
        "nombre_completo = EXCLUDED.nombre_completo, mail = COALESCE(EXCLUDED.mail, docente.mail)",
        filas,
    )
    if filas_dep:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO docente_departamento (id_docente, id_departamento) VALUES %s ON CONFLICT DO NOTHING",
            sorted(set(filas_dep)),
        )

    # Docentes que ya estaban en la base pero no vienen en el archivo (los creados a partir
    # de horarios de cuatrimestres anteriores): se suman al diccionario para reutilizarlos
    # y para que un id nuevo no choque con uno existente.
    cur.execute("SELECT id_docente, nombre, apellido, nombre_completo FROM docente")
    for id_docente, nombre, apellido, nombre_completo in cur.fetchall():
        ids_usados.add(id_docente)
        lut.setdefault(norm_nombre(f"{nombre} {apellido}"), id_docente)
        lut.setdefault(norm_nombre(nombre_completo), id_docente)
    return lut, ids_usados


def resolver_o_crear_docente(cur, nombre_crudo, lut, ids_usados, reporte):
    """Los profesores que aparecen en horarios_<cuatri>.json vienen como
    'Nombre Apellido' (ya convertido en combinar_horarios.py). docentes_total.json
    solo cubre el personal de planta oficial de cada departamento, no todos los
    ayudantes/JTP que dan practicas -> una fraccion importante no va a matchear.
    Para esos, se crea un registro de docente nuevo a partir del nombre tal cual
    aparece en el horario (o se reutiliza si ya se creo en una carga anterior), y
    se deja anotado en el reporte para revisar mail/departamento a mano si hace falta."""
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

def snapshot_eventos(cur):
    cur.execute("SELECT titulo, fecha_ini, fecha_fin, tipo, id_cuatri FROM evento")
    return {tuple(None if v is None else str(v) for v in row) for row in cur.fetchall()}


def cargar_eventos(cur, data_dir, archivo, reporte):
    eventos = load(resolver_archivo(data_dir, archivo))["eventos"]
    for e in eventos:
        if isinstance(e.get("id_cuatri"), list):
            raise CargaAbortada(
                f"El evento {e['titulo']!r} tiene mas de un id_cuatri: falta correr "
                "revisar scrapeoEvento.py (separa los eventos multicuatrimestre) antes de cargar")

    ids_en_archivo = sorted({e["id_cuatri"] for e in eventos if e.get("id_cuatri")})
    antes = snapshot_eventos(cur)

    # Se reemplazan los eventos de los cuatrimestres que trae el archivo (mas los que no
    # tienen cuatrimestre asignado todavia, que tambien se vuelven a scrapear). Los eventos
    # de cualquier otro cuatrimestre quedan como estaban.
    cur.execute("DELETE FROM evento WHERE id_cuatri = ANY(%s) OR id_cuatri IS NULL", (ids_en_archivo,))
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO evento (titulo, descripcion, fecha_ini, fecha_fin, tipo, id_cuatri) VALUES %s",
        [
            (e["titulo"], e.get("descripcion"), e["fecha_ini"], e.get("fecha_fin"),
             e.get("tipo"), e.get("id_cuatri"))
            for e in eventos
        ],
    )

    despues = snapshot_eventos(cur)
    reporte.eventos = {
        "cuatrimestres_reemplazados": ids_en_archivo,
        "total_en_archivo": len(eventos),
        "agregados": sorted(" | ".join(x or "-" for x in t) for t in (despues - antes)),
        "quitados": sorted(" | ".join(x or "-" for x in t) for t in (antes - despues)),
    }


# =============================================================================
# Comisiones, bloques horarios y aulas de cada bloque
# =============================================================================

SQL_FIRMAS = """
SELECT c.id_materia, c.comision, COALESCE(c.turno, ''),
  COALESCE((SELECT string_agg(d.id_docente || ':' || d.rol, ',' ORDER BY d.id_docente, d.rol)
            FROM comision_docente d WHERE d.id_comision = c.id_comision), '') AS docentes,
  COALESCE((SELECT string_agg(
              b.id_dia || ' ' || b.hora_inicio::text || '-' || b.hora_fin::text || ' ' || COALESCE(b.tipo, '') ||
              ' @' || COALESCE((SELECT string_agg(ba.id_aula, '+' ORDER BY ba.id_aula)
                                FROM bloque_horario_aula ba WHERE ba.id_bloque = b.id_bloque), ''),
              ' | ' ORDER BY b.id_dia, b.hora_inicio, b.hora_fin, COALESCE(b.tipo, ''))
            FROM bloque_horario b WHERE b.id_comision = c.id_comision), '') AS bloques
FROM comision c
WHERE c.id_cuatri = %s
"""


def firmas_cuatri(cur, id_cuatri):
    """(id_materia, comision) -> resumen del contenido (turno, docentes, bloques+aulas).
    Sirve para decir que comisiones son nuevas, cuales cambiaron y cuales desaparecieron."""
    cur.execute(SQL_FIRMAS, (id_cuatri,))
    return {(m, c): (t, d, b) for m, c, t, d, b in cur.fetchall()}


def cargar_comisiones(cur, horarios, dia_lut, aula_exact, aula_por_pabellon,
                      docente_lut, docente_ids_usados, reporte, forzar=False):
    # Una corrida puede traer comisiones de mas de un cuatrimestre: cada uno se procesa por separado.
    por_cuatri = defaultdict(list)
    for c in horarios:
        por_cuatri[c["id_cuatri"]].append(c)

    for id_cuatri, comisiones in sorted(por_cuatri.items()):
        antes = firmas_cuatri(cur, id_cuatri)
        claves_json = {(c["materia"], c["comision"]) for c in comisiones}
        ausentes = set(antes) - claves_json

        if (len(ausentes) >= MIN_ELIMINAR and antes
                and 100 * len(ausentes) / len(antes) > MAX_ELIMINAR_PCT and not forzar):
            raise CargaAbortada(
                f"Cuatrimestre {id_cuatri}: el archivo no trae {len(ausentes)} de las {len(antes)} "
                f"comisiones ya cargadas (>{MAX_ELIMINAR_PCT}%). Puede ser un scraping incompleto; "
                "si es correcto, volve a correr con --forzar.")

        for c in comisiones:
            turno_comision = next((h.get("turno") for h in c["horarios"] if h.get("turno")), None)
            cur.execute(
                "INSERT INTO comision (id_materia, id_cuatri, comision, turno) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (id_materia, comision, id_cuatri) DO UPDATE SET turno = EXCLUDED.turno "
                "RETURNING id_comision",
                (c["materia"], c["id_cuatri"], c["comision"], turno_comision),
            )
            id_comision = cur.fetchone()[0]

            # Se reconstruyen docentes y bloques de esta comision (conserva el id_comision).
            cur.execute("DELETE FROM comision_docente WHERE id_comision = %s", (id_comision,))
            cur.execute("DELETE FROM bloque_horario WHERE id_comision = %s", (id_comision,))

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

        for id_materia, comision in sorted(ausentes):
            cur.execute(
                "DELETE FROM comision WHERE id_materia = %s AND comision = %s AND id_cuatri = %s",
                (id_materia, comision, id_cuatri),
            )

        despues = firmas_cuatri(cur, id_cuatri)
        reporte.comisiones[id_cuatri] = {
            "total_en_archivo": len(comisiones),
            "nuevas": sorted("/".join(k) for k in set(despues) - set(antes)),
            "modificadas": sorted("/".join(k) for k in antes if k in despues and antes[k] != despues[k]),
            "eliminadas": sorted("/".join(k) for k in ausentes),
            "sin_cambios": sum(1 for k in antes if k in despues and antes[k] == despues[k]),
        }


# =============================================================================
# Resumen para humanos (lo que ve n8n antes de pedir la aprobacion)
# =============================================================================

def _muestra(lista, n=8):
    if not lista:
        return ""
    cabeza = ", ".join(lista[:n])
    return cabeza + (f", ... (+{len(lista) - n} mas)" if len(lista) > n else "")


def imprimir_resumen(rep):
    print()
    print("=== Resumen " + ("(SIMULACION: no se guardo nada) ===" if rep.dry_run else "===")
    )
    for id_cuatri, r in rep.comisiones.items():
        print(f"Comisiones {id_cuatri}: {r['total_en_archivo']} en el archivo -> "
              f"{len(r['nuevas'])} nuevas, {len(r['modificadas'])} modificadas, "
              f"{len(r['eliminadas'])} eliminadas, {r['sin_cambios']} sin cambios")
        for etiqueta, clave in (("  nuevas", "nuevas"), ("  modificadas", "modificadas"), ("  eliminadas", "eliminadas")):
            if r[clave]:
                print(f"{etiqueta}: {_muestra(r[clave])}")
    ev = rep.eventos
    if ev:
        print(f"Eventos ({', '.join(ev['cuatrimestres_reemplazados']) or 'sin cuatrimestre'}): "
              f"{ev['total_en_archivo']} en el archivo -> {len(ev['agregados'])} agregados/cambiados, "
              f"{len(ev['quitados'])} quitados/cambiados")
        if ev["agregados"]:
            print(f"  agregados: {_muestra(ev['agregados'], 5)}")
        if ev["quitados"]:
            print(f"  quitados: {_muestra(ev['quitados'], 5)}")
    cambios = {t: (rep.tablas_antes[t], rep.tablas_despues[t])
               for t in TABLAS if rep.tablas_antes[t] != rep.tablas_despues[t]}
    if cambios:
        print("Filas por tabla (antes -> despues): " + ", ".join(f"{t} {a}->{d}" for t, (a, d) in cambios.items()))
    else:
        print("Filas por tabla: sin cambios")
    print(f"Docentes nuevos creados desde horarios: {len(set(rep.docentes_creados_desde_horarios))}")
    print(f"Aulas que no matchearon el catalogo: {len(rep.aulas_sin_match)}")
    print(f"Bloques horarios sin aula en el JSON de origen: {rep.bloques_sin_ninguna_aula_en_origen}")


# =============================================================================
# main
# =============================================================================

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default="JSON",
                    help="carpeta con los JSON de origen (default: JSON)")
    ap.add_argument("--horarios", nargs="+", default=["horarios_2026_1c.json"],
                    help="archivo(s) de horarios a cargar, dentro de --data-dir "
                         "(default: horarios_2026_1c.json). Solo se tocan los cuatrimestres que traigan.")
    ap.add_argument("--eventos", default="eventos.json", help="archivo de eventos (default: eventos.json)")
    ap.add_argument("--cuatrimestres", default="cuatrimestres_2026.json",
                    help="archivo de cuatrimestres (default: cuatrimestres_2026.json)")
    ap.add_argument("--docentes", default="docentes_total.json",
                    help="archivo de docentes (default: docentes_total.json)")
    ap.add_argument("--dsn", default=None,
                    help="cadena de conexion postgresql://... (default: variables PG* / localhost)")
    ap.add_argument("--reporte", default="reporte_carga_bd.json",
                    help="donde guardar el reporte (default: reporte_carga_bd.json)")
    ap.add_argument("--dry-run", action="store_true",
                    help="hace todo dentro de la transaccion, muestra el resumen y deshace (no guarda nada)")
    ap.add_argument("--reset", action="store_true",
                    help="vacia TODAS las tablas antes de cargar (el comportamiento de antes)")
    ap.add_argument("--forzar", action="store_true",
                    help="permite eliminar muchas comisiones de un cuatrimestre (ver MAX_ELIMINAR_PCT)")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        sys.exit(f"No encuentro la carpeta de datos: {data_dir.resolve()}")

    archivos_horarios = [resolver_archivo(data_dir, h) for h in args.horarios]
    for p in [*archivos_horarios, resolver_archivo(data_dir, args.eventos),
              resolver_archivo(data_dir, args.cuatrimestres), resolver_archivo(data_dir, args.docentes)]:
        if not p.exists():
            sys.exit(f"No encuentro el archivo: {p}")

    if args.dsn:
        dsn = args.dsn
    elif os.environ.get("PGHOST") or os.environ.get("PGDATABASE"):
        dsn = None  # hay variables de entorno PG* seteadas: que las use psycopg2 directo
    else:
        dsn = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    conn = psycopg2.connect(dsn) if dsn else psycopg2.connect()
    reporte = Reporte()
    reporte.dry_run = args.dry_run

    try:
        with conn.cursor() as cur:
            reporte.tablas_antes = contar_filas(cur)

            if args.reset:
                print("--reset: vaciando TODAS las tablas...")
                truncar_todo(cur)

            print("Catalogos (departamento, dia, cuatrimestre, aula)...")
            cargar_departamentos(cur, data_dir)
            dia_lut = cargar_dias(cur, data_dir)
            cargar_cuatrimestres(cur, data_dir, args.cuatrimestres)
            aula_exact, aula_por_pabellon = cargar_aulas(cur, data_dir)

            print("Materias / carreras / plan de estudios / correlativas...")
            data_materias = cargar_materias(cur, data_dir)
            cargar_carreras_y_plan(cur, data_materias)

            print("Docentes...")
            docente_lut, docente_ids_usados = cargar_docentes(cur, data_dir, args.docentes)

            print("Eventos...")
            cargar_eventos(cur, data_dir, args.eventos, reporte)

            horarios = []
            for p in archivos_horarios:
                print(f"Leyendo {p.name}...")
                horarios.extend(load(p))
            print("Comisiones, bloques horarios y aulas de cada bloque...")
            cargar_comisiones(cur, horarios, dia_lut, aula_exact, aula_por_pabellon,
                              docente_lut, docente_ids_usados, reporte, forzar=args.forzar)

            reporte.tablas_despues = contar_filas(cur)

        if args.dry_run:
            conn.rollback()
            print("Simulacion terminada: se deshizo todo, la base no cambio.")
        else:
            conn.commit()
            print("Listo, commit hecho.")
    except CargaAbortada as e:
        conn.rollback()
        print(f"CARGA ABORTADA (no se guardo nada): {e}", file=sys.stderr)
        sys.exit(2)
    except Exception:
        conn.rollback()
        print("Hubo un error, se hizo rollback: no se guardo nada en la base.", file=sys.stderr)
        raise
    finally:
        conn.close()

    with open(args.reporte, "w", encoding="utf-8") as f:
        json.dump(reporte.as_dict(), f, ensure_ascii=False, indent=2)

    imprimir_resumen(reporte)
    print(f"Reporte completo en: {args.reporte}")


if __name__ == "__main__":
    main()
