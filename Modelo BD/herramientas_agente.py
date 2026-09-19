#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tools de Fase 1 para el agente de eXACTai (ver claude/tools-agente.md).

Son funciones Python "puras" (sin ningun LLM de por medio) que consultan la
base Postgres cargada por cargar_datos.py. Estan pensadas para que el agente
las registre como tools, pero se pueden probar y usar solas desde cualquier
script (ver probar_herramientas.py).

No modifica NINGUN archivo JSON ni ninguna tabla: solo lee.

Decision de diseno (11/09/2026, confirmada con Franco): la tabla `materia`
NO tiene columna `nombre` en el schema actual (ver diseno-base-datos.md) y
un mismo id de materia puede tener nombres distintos segun la carrera (ej.
"analisis_1" es "Analisis I" en unas carreras y "Matematica I" en otras).
En vez de migrar el schema, `buscar_materia`/`resolver_materia` arman el
mapeo nombre -> id_materia leyendo carreras_materias.json directamente (la
misma fuente que uso el ETL), y solo usan la base para todo lo demas
(comisiones, horarios, docentes, correlativas, eventos). Si el dia de manana
esto se siente limitado, la alternativa mencionada era migrar el schema
(materia.nombre + carrera_materia.nombre) - queda documentada por si se
retoma.

Requiere: psycopg2-binary  ->  pip install psycopg2-binary
"""
import json
import re
import unicodedata
from contextlib import contextmanager
from datetime import date, time
from functools import lru_cache
from itertools import combinations
from pathlib import Path

import psycopg2
import psycopg2.extras

# =============================================================================
# CONEXION A LA BASE: mismos datos que en cargar_datos.py.
# =============================================================================

DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "postgres"
DB_USER = "postgres"
DB_PASSWORD = "admin"

# Carpeta JSON/, relativa a este archivo (Modelo BD/herramientas_agente.py
# -> ../JSON). Se puede pisar pasando data_dir a las funciones que lo usan.
DATA_DIR = Path(__file__).resolve().parent.parent / "JSON"


def conectar(dsn=None):
    dsn = dsn or f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    return psycopg2.connect(dsn)


@contextmanager
def _cursor(dsn=None):
    """Conexion de corta duracion para una sola tool call: se cierra sola al
    salir del bloque (a diferencia de `with conectar(dsn) as conn`, que en
    psycopg2 solo hace commit/rollback pero NO cierra la conexion)."""
    conn = conectar(dsn)
    try:
        with conn.cursor() as cur:
            yield conn, cur
    finally:
        conn.close()


# =============================================================================
# Normalizacion de texto (mismo criterio que cargar_datos.py)
# =============================================================================

def quitar_acentos(s):
    s = unicodedata.normalize("NFD", str(s))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def norm(s):
    s = quitar_acentos(s).lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# =============================================================================
# Indice nombre -> id_materia, armado desde carreras_materias.json (no desde
# la base: ver nota de diseno arriba). Se arma una sola vez por proceso.
# =============================================================================

@lru_cache(maxsize=4)
def _indice_materias(data_dir=None):
    data_dir = Path(data_dir) if data_dir else Path(DATA_DIR)
    with open(data_dir / "carreras_materias.json", encoding="utf-8") as f:
        data = json.load(f)

    # id_materia -> {"nombres": set(), "carreras": {id_carrera: nombre_en_esa_carrera}}
    info = {}
    for m in data["materias"]:
        info[m["id"]] = {"nombres": set(), "carreras": {}}

    for c in data["carreras"]:
        for item in c["plan"]:
            mid = item.get("materia_id")
            nom = item.get("nombre")
            if mid and nom and mid in info:
                info[mid]["nombres"].add(nom)
                info[mid]["carreras"][c["id"]] = nom

    # Tambien sumamos las variantes que aparezcan en aliases_externos (a
    # veces tienen el nombre completo, ej. "Quimica General e Inorganica I").
    for m in data["materias"]:
        for variantes in (m.get("aliases_externos") or {}).values():
            for v in variantes or []:
                if v:
                    info[m["id"]]["nombres"].add(v)

    # id -> nombre representativo para mostrar (el mas usado entre carreras;
    # a igualdad de uso, el mas largo, para que sea mas descriptivo).
    for mid, d in info.items():
        if d["nombres"]:
            d["nombre_principal"] = sorted(d["nombres"], key=lambda n: (-len(n), n))[0]
        else:
            d["nombre_principal"] = mid

    # indice invertido: variante normalizada -> set(id_materia)
    invertido = {}
    for mid, d in info.items():
        variantes = set(d["nombres"]) | {mid, mid.replace("_", " ")}
        for v in variantes:
            invertido.setdefault(norm(v), set()).add(mid)

    return info, invertido


def resolver_materia(nombre_o_id, data_dir=None, max_resultados=8):
    """Busca una materia por nombre, alias o id (case/acento-insensible,
    matchea substring en cualquier direccion). Devuelve una lista de
    candidatos ordenada por que tan bueno es el match:
        [{"id_materia": ..., "nombre": ..., "score": ...}, ...]
    score: 3 = coincide exacto, 2 = empieza igual, 1 = substring.
    """
    info, invertido = _indice_materias(data_dir)
    q = norm(nombre_o_id)
    if not q:
        return []

    # match directo por id
    if nombre_o_id in info:
        return [{"id_materia": nombre_o_id, "nombre": info[nombre_o_id]["nombre_principal"], "score": 3}]

    encontrados = {}  # id_materia -> mejor score
    for variante_norm, ids in invertido.items():
        if variante_norm == q:
            score = 3
        elif variante_norm.startswith(q) or q.startswith(variante_norm):
            score = 2
        elif q in variante_norm or variante_norm in q:
            score = 1
        else:
            continue
        for mid in ids:
            encontrados[mid] = max(encontrados.get(mid, 0), score)

    resultado = [
        {"id_materia": mid, "nombre": info[mid]["nombre_principal"], "score": score}
        for mid, score in encontrados.items()
    ]
    resultado.sort(key=lambda r: (-r["score"], r["nombre"]))
    return resultado[:max_resultados]


def nombre_materia(id_materia, data_dir=None):
    info, _ = _indice_materias(data_dir)
    d = info.get(id_materia)
    return d["nombre_principal"] if d else id_materia


def nombre_materia_en_carrera(id_materia, id_carrera, data_dir=None):
    info, _ = _indice_materias(data_dir)
    d = info.get(id_materia)
    if not d:
        return id_materia
    return d["carreras"].get(id_carrera, d["nombre_principal"])


# =============================================================================
# Cuatrimestre "vigente" por defecto: el de mayor `orden` entre los que
# tienen al menos una comision cargada. No hay fecha de hoy vs. rango de
# cuatrimestre en el schema, asi que se infiere de que datos hay cargados.
# =============================================================================

def cuatrimestre_vigente(cur):
    cur.execute("""
        SELECT c.id_cuatri
        FROM cuatrimestre c
        JOIN comision co ON co.id_cuatri = c.id_cuatri
        GROUP BY c.id_cuatri, c.orden
        ORDER BY c.orden DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    return row[0] if row else None


# =============================================================================
# Tool 1: buscar_materia
# =============================================================================

def buscar_materia(nombre_o_alias, id_cuatri=None, dsn=None, data_dir=None):
    """Datos generales de la/s materia/s que matchean `nombre_o_alias`, mas
    las comisiones que tiene en `id_cuatri` (por defecto, el cuatrimestre
    vigente)."""
    candidatos = resolver_materia(nombre_o_alias, data_dir)
    if not candidatos:
        return {"encontrado": False, "candidatos": []}

    resultado = {"encontrado": True, "materias": []}
    with _cursor(dsn) as (conn, cur):
        cuatri = id_cuatri or cuatrimestre_vigente(cur)
        for cand in candidatos:
            mid = cand["id_materia"]
            cur.execute("SELECT carga_horaria, aliases_externos FROM materia WHERE id = %s", (mid,))
            row = cur.fetchone()
            if not row:
                continue
            carga_horaria, aliases_externos = row

            cur.execute("""
                SELECT id_comision, comision, turno
                FROM comision
                WHERE id_materia = %s AND id_cuatri = %s
                ORDER BY comision
            """, (mid, cuatri))
            comisiones = [
                {"id_comision": r[0], "comision": r[1], "turno": r[2]}
                for r in cur.fetchall()
            ]

            resultado["materias"].append({
                "id_materia": mid,
                "nombre": cand["nombre"],
                "score": cand["score"],
                "carga_horaria": carga_horaria,
                "aliases_externos": aliases_externos,
                "id_cuatri": cuatri,
                "comisiones": comisiones,
            })
    return resultado


# =============================================================================
# Tool 2: info_comision
# =============================================================================

def _bloques_de_comisiones(cur, ids_comision):
    if not ids_comision:
        return {}
    cur.execute("""
        SELECT b.id_comision, b.id_bloque, d.nombre AS dia, b.hora_inicio, b.hora_fin,
               b.turno, b.tipo
        FROM bloque_horario b
        JOIN dia d ON d.id_dia = b.id_dia
        WHERE b.id_comision = ANY(%s)
        ORDER BY b.id_comision, b.hora_inicio
    """, (list(ids_comision),))
    filas = cur.fetchall()

    id_bloques = [f[1] for f in filas]
    aulas_por_bloque = {}
    if id_bloques:
        cur.execute("""
            SELECT ba.id_bloque, a.id_aula, a.numero, a.pabellon
            FROM bloque_horario_aula ba
            JOIN aula a ON a.id_aula = ba.id_aula
            WHERE ba.id_bloque = ANY(%s)
        """, (id_bloques,))
        for id_bloque, id_aula, numero, pabellon in cur.fetchall():
            aulas_por_bloque.setdefault(id_bloque, []).append(
                {"id_aula": id_aula, "numero": numero, "pabellon": pabellon})

    por_comision = {}
    for id_comision, id_bloque, dia, hora_inicio, hora_fin, turno, tipo in filas:
        por_comision.setdefault(id_comision, []).append({
            "id_bloque": id_bloque,
            "dia": dia,
            "hora_inicio": str(hora_inicio)[:5],
            "hora_fin": str(hora_fin)[:5],
            "turno": turno,
            "tipo": tipo,
            "aulas": aulas_por_bloque.get(id_bloque, []),
        })
    return por_comision


def _docentes_de_comisiones(cur, ids_comision):
    if not ids_comision:
        return {}
    cur.execute("""
        SELECT cd.id_comision, doc.nombre_completo, cd.rol
        FROM comision_docente cd
        JOIN docente doc ON doc.id_docente = cd.id_docente
        WHERE cd.id_comision = ANY(%s)
        ORDER BY cd.id_comision, cd.rol
    """, (list(ids_comision),))
    por_comision = {}
    for id_comision, nombre_completo, rol in cur.fetchall():
        por_comision.setdefault(id_comision, []).append({"nombre": nombre_completo, "rol": rol})
    return por_comision


def info_comision(id_comision=None, materia=None, turno=None, id_cuatri=None,
                   dsn=None, data_dir=None):
    """Horario completo (dia/hora/tipo/aulas) y docentes (con rol) de una o
    mas comisiones. Se puede pedir por `id_comision` directo, o por
    `materia` (nombre/alias/id) + `turno` opcional. Si `materia` matchea mas
    de una materia, devuelve {"ambiguo": True, "opciones": [...]}  en vez de
    adivinar."""
    with _cursor(dsn) as (conn, cur):
        if id_comision is not None:
            ids = [id_comision]
        else:
            if not materia:
                return {"error": "hay que pasar id_comision o materia"}
            candidatos = resolver_materia(materia, data_dir)
            fuertes = [c for c in candidatos if c["score"] == candidatos[0]["score"]] if candidatos else []
            if not candidatos:
                return {"encontrado": False, "comisiones": []}
            if len(fuertes) > 1:
                return {"ambiguo": True, "opciones": fuertes}

            mid = fuertes[0]["id_materia"]
            cuatri = id_cuatri or cuatrimestre_vigente(cur)
            sql = "SELECT id_comision FROM comision WHERE id_materia = %s AND id_cuatri = %s"
            params = [mid, cuatri]
            if turno:
                sql += " AND turno = %s"
                params.append(turno)
            cur.execute(sql, params)
            ids = [r[0] for r in cur.fetchall()]
            if not ids:
                return {"encontrado": False, "comisiones": []}

        cur.execute("""
            SELECT id_comision, id_materia, comision, turno, id_cuatri
            FROM comision WHERE id_comision = ANY(%s)
        """, (ids,))
        base = {r[0]: {"id_comision": r[0], "id_materia": r[1], "materia": nombre_materia(r[1], data_dir),
                       "comision": r[2], "turno": r[3], "id_cuatri": r[4]} for r in cur.fetchall()}

        bloques = _bloques_de_comisiones(cur, ids)
        docentes = _docentes_de_comisiones(cur, ids)

    comisiones = []
    for id_com, datos in base.items():
        datos["bloques"] = bloques.get(id_com, [])
        datos["docentes"] = docentes.get(id_com, [])
        comisiones.append(datos)
    comisiones.sort(key=lambda c: c["comision"])
    return {"encontrado": True, "comisiones": comisiones}


# =============================================================================
# Tool 3: proximas_fechas
# =============================================================================

def proximas_fechas(tipo=None, id_cuatri=None, desde=None, incluir_pasadas=False,
                     limite=20, dsn=None):
    """Eventos (inscripciones, publicaciones, examenes, etc.) ordenados por
    fecha_ini. `desde` (date, default hoy) filtra el piso salvo que
    `incluir_pasadas=True`."""
    desde = desde or date.today()
    sql = "SELECT id_evento, titulo, descripcion, fecha_ini, fecha_fin, tipo, id_cuatri FROM evento WHERE 1=1"
    params = []
    if not incluir_pasadas:
        sql += " AND (fecha_ini IS NULL OR fecha_ini >= %s)"
        params.append(desde)
    if tipo:
        sql += " AND tipo = %s"
        params.append(tipo)
    if id_cuatri:
        sql += " AND id_cuatri = %s"
        params.append(id_cuatri)
    sql += " ORDER BY fecha_ini NULLS LAST LIMIT %s"
    params.append(limite)

    with _cursor(dsn) as (conn, cur):
        cur.execute(sql, params)
        eventos = [
            {
                "id_evento": r[0], "titulo": r[1], "descripcion": r[2],
                "fecha_ini": r[3].isoformat() if hasattr(r[3], "isoformat") else r[3],
                "fecha_fin": r[4].isoformat() if hasattr(r[4], "isoformat") else r[4],
                "tipo": r[5], "id_cuatri": r[6],
            }
            for r in cur.fetchall()
        ]
    return eventos


# =============================================================================
# Tool 4: chequear_solapamiento
# =============================================================================

def _se_solapan(b1, b2):
    return b1["dia"] == b2["dia"] and b1["hora_inicio"] < b2["hora_fin"] and b1["hora_fin"] > b2["hora_inicio"]


def chequear_solapamiento(ids_comision, dsn=None, data_dir=None):
    """Dado un conjunto de comisiones (de cualquier materia) que el usuario
    esta considerando, devuelve los pares de bloques que se pisan."""
    with _cursor(dsn) as (conn, cur):
        cur.execute("""
            SELECT id_comision, id_materia, comision FROM comision WHERE id_comision = ANY(%s)
        """, (list(ids_comision),))
        info_comisiones = {r[0]: {"id_materia": r[1], "materia": nombre_materia(r[1], data_dir), "comision": r[2]}
                            for r in cur.fetchall()}
        bloques_por_comision = _bloques_de_comisiones(cur, ids_comision)

    bloques_planos = [
        {**b, "id_comision": id_com}
        for id_com, bs in bloques_por_comision.items() for b in bs
    ]

    solapamientos = []
    for b1, b2 in combinations(bloques_planos, 2):
        if b1["id_comision"] == b2["id_comision"]:
            continue
        if _se_solapan(b1, b2):
            solapamientos.append({
                "comision_1": info_comisiones.get(b1["id_comision"]), "bloque_1": b1,
                "comision_2": info_comisiones.get(b2["id_comision"]), "bloque_2": b2,
            })

    return {"hay_solapamiento": bool(solapamientos), "solapamientos": solapamientos}


# =============================================================================
# Tool 5: armar_horario
# =============================================================================

def _hhmm(s):
    h, m = s.split(":")
    return time(int(h), int(m))


def _penalidad(bloques, preferencias):
    if not preferencias:
        return 0
    pen = 0
    turno_pref = preferencias.get("turno")
    evitar_dias = set(preferencias.get("evitar_dias") or [])
    no_antes = preferencias.get("no_antes_de")
    no_despues = preferencias.get("no_despues_de")
    no_antes_t = _hhmm(no_antes) if no_antes else None
    no_despues_t = _hhmm(no_despues) if no_despues else None

    for b in bloques:
        if turno_pref and b["turno"] and b["turno"] != turno_pref:
            pen += 1
        if b["dia"] in evitar_dias:
            pen += 2
        hi = _hhmm(b["hora_inicio"])
        hf = _hhmm(b["hora_fin"])
        if no_antes_t and hi < no_antes_t:
            pen += 1
        if no_despues_t and hf > no_despues_t:
            pen += 1
    return pen


def armar_horario(materias, id_cuatri=None, preferencias=None, max_resultados=5,
                   dsn=None, data_dir=None):
    """Backtracking sobre "una comision por materia": para cada combinacion
    sin solapamientos, calcula una penalidad segun `preferencias`
    ({turno?, evitar_dias?: [dia,...], no_antes_de?: "HH:MM",
    no_despues_de?: "HH:MM"}) y devuelve las mejores `max_resultados`,
    ordenadas de menor a mayor penalidad."""
    resueltas = []
    for m in materias:
        candidatos = resolver_materia(m, data_dir)
        if not candidatos:
            return {"error": f"no encontre ninguna materia para '{m}'"}
        mejor_score = candidatos[0]["score"]
        fuertes = [c for c in candidatos if c["score"] == mejor_score]
        if len(fuertes) > 1:
            return {"ambiguo": True, "materia_pedida": m, "opciones": fuertes}
        resueltas.append(fuertes[0]["id_materia"])

    with _cursor(dsn) as (conn, cur):
        cuatri = id_cuatri or cuatrimestre_vigente(cur)
        opciones_por_materia = []
        for mid in resueltas:
            cur.execute("""
                SELECT id_comision, comision, turno FROM comision
                WHERE id_materia = %s AND id_cuatri = %s ORDER BY comision
            """, (mid, cuatri))
            comisiones = cur.fetchall()
            if not comisiones:
                return {"error": f"'{mid}' no tiene comisiones en {cuatri}"}
            ids_com = [c[0] for c in comisiones]
            bloques_por_com = _bloques_de_comisiones(cur, ids_com)
            opciones_por_materia.append([
                {"id_materia": mid, "materia": nombre_materia(mid, data_dir),
                 "id_comision": c[0], "comision": c[1], "turno": c[2],
                 "bloques": bloques_por_com.get(c[0], [])}
                for c in comisiones
            ])

    resultados = []
    MAX_COMBOS = 5000  # tope de seguridad; con pocas materias/comisiones no se acerca

    def backtrack(i, elegidas, bloques_ocupados):
        if len(resultados) >= MAX_COMBOS:
            return
        if i == len(opciones_por_materia):
            resultados.append(list(elegidas))
            return
        for opcion in opciones_por_materia[i]:
            choca = any(_se_solapan(b, ocupado) for b in opcion["bloques"] for ocupado in bloques_ocupados)
            if choca:
                continue
            elegidas.append(opcion)
            backtrack(i + 1, elegidas, bloques_ocupados + opcion["bloques"])
            elegidas.pop()

    backtrack(0, [], [])

    if not resultados:
        return {"encontrado": False, "combinaciones": [],
                "motivo": "no hay ninguna combinacion sin superposiciones para esas materias"}

    combos = []
    for combo in resultados:
        todos_bloques = [b for opcion in combo for b in opcion["bloques"]]
        combos.append({
            "penalidad": _penalidad(todos_bloques, preferencias),
            "comisiones": [
                {"id_materia": o["id_materia"], "materia": o["materia"],
                 "id_comision": o["id_comision"], "comision": o["comision"], "turno": o["turno"]}
                for o in combo
            ],
        })
    combos.sort(key=lambda c: c["penalidad"])
    return {"encontrado": True, "total_combinaciones": len(combos), "combinaciones": combos[:max_resultados]}


# =============================================================================
# Tool 6: correlativas_faltantes
# =============================================================================

def correlativas_faltantes(id_materia, materias_aprobadas=None, id_carrera=None,
                            dsn=None, data_dir=None):
    """Compara las correlativas de `id_materia` (id, no nombre - resolver
    antes con resolver_materia si hace falta) contra `materias_aprobadas`
    (lista de id_materia). Si no se pasa `id_carrera`, devuelve el resultado
    agrupado por cada carrera que tenga a esa materia con correlativas,
    porque pueden diferir entre carreras."""
    aprobadas = set(materias_aprobadas or [])
    sql = "SELECT id_carrera, id_materia_correlativa FROM correlativa WHERE id_materia = %s"
    params = [id_materia]
    if id_carrera:
        sql += " AND id_carrera = %s"
        params.append(id_carrera)

    with _cursor(dsn) as (conn, cur):
        cur.execute(sql, params)
        filas = cur.fetchall()

    por_carrera = {}
    for carrera, correlativa in filas:
        por_carrera.setdefault(carrera, set()).add(correlativa)

    if not por_carrera:
        return {"id_materia": id_materia, "materia": nombre_materia(id_materia, data_dir),
                "sin_correlativas_registradas": True, "resultado": []}

    resultado = []
    for carrera, requeridas in por_carrera.items():
        faltantes = requeridas - aprobadas
        resultado.append({
            "id_carrera": carrera,
            "correlativas_requeridas": [
                {"id_materia": c, "nombre": nombre_materia_en_carrera(c, carrera, data_dir)}
                for c in sorted(requeridas)
            ],
            "faltantes": [
                {"id_materia": c, "nombre": nombre_materia_en_carrera(c, carrera, data_dir)}
                for c in sorted(faltantes)
            ],
            "puede_cursar": not faltantes,
        })
    return {"id_materia": id_materia, "materia": nombre_materia(id_materia, data_dir),
            "sin_correlativas_registradas": False, "resultado": resultado}
