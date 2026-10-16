#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chequeo integral de integridad referencial entre todos los JSON del proyecto.

Se corre desde la carpeta que contiene JSON/ (el pipeline lo hace dentro de su
carpeta de trabajo). Termina con exit code 1 si encuentra PROBLEMAS (cosas que
romperian la carga a la base o dejarian datos colgados) y con 0 si solo hay
avisos. Ademas escribe auditoria.json con el detalle, que lee el pipeline/n8n.

Variables de entorno opcionales:
    EXACTAI_CUATRI          cuatrimestre a auditar (default 2026_1c)
    EXACTAI_CUATRIMESTRES   archivo de cuatrimestres (default JSON/cuatrimestres_2026.json)
"""
import json
import os
import re
import sys

CUATRI = os.environ.get("EXACTAI_CUATRI", "2026_1c")
F_HORARIOS = f"JSON/horarios_{CUATRI}.json"
F_CUATRIS = os.environ.get("EXACTAI_CUATRIMESTRES", "JSON/cuatrimestres_2026.json")

problemas = {}   # nombre del chequeo -> detalle (si hay algo, la auditoria falla)
avisos = {}      # nombre -> detalle (no frena el flujo)


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def check(nombre, malos):
    """Imprime el resultado de un chequeo y lo registra como problema si hay algo."""
    print(f"{nombre}:", malos or "OK")
    if malos:
        problemas[nombre] = sorted(map(str, malos))[:50]


def a_minutos(hhmm):
    """'8:00' / '08:00' -> minutos desde medianoche (None si el formato no sirve)."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", hhmm) if isinstance(hhmm, str) else None
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def ids_de_evento(e):
    ids = e.get("id_cuatri")
    if ids is None:
        return []
    return [ids] if isinstance(ids, str) else list(ids)


carreras_mat = load("JSON/carreras_materias.json")
materias = carreras_mat["materias"]
grupos = carreras_mat["grupos_optativos"]
carreras = carreras_mat["carreras"]
docentes = load("JSON/docentes_total.json")["docentes"]
departamentos = load("JSON/departamento.json")
dias = load("JSON/dia.json")
aulas = load("JSON/aula.json")
cuatris = load(F_CUATRIS)["cuatrimestres"]
eventos = load("JSON/eventos.json")["eventos"]
horarios = load(F_HORARIOS)

mat_ids = {m["id"] for m in materias}
grupo_ids = {g["id"] for g in grupos}
carrera_ids = {c["id"] for c in carreras}
dep_ids = {d["id_departamento"] for d in departamentos}
dep_codes_docentes = {d for doc in docentes for d in doc.get("departamentos", [])}
cuatri_ids = {c["id_cuatri"] for c in cuatris}
dia_nombres = {d["nombre"] for d in dias}

print("=== Materia / Carrera / Carrera_Materia / Correlativa ===")
missing_plan_mat = set()
missing_corr = set()
missing_grupo_ref = set()
for c in carreras:
    for p in c["plan"]:
        mid = p.get("materia_id")
        if mid and mid not in mat_ids:
            missing_plan_mat.add((c["id"], mid))
        for corr in p.get("correlativas", []) or []:
            if corr not in mat_ids:
                missing_corr.add((c["id"], corr))
        gid = p.get("grupo_id")
        if gid and gid not in grupo_ids:
            missing_grupo_ref.add((c["id"], gid))
check("materia_id en planes sin Materia", missing_plan_mat)
check("correlativas sin Materia", missing_corr)
check("grupo_id en planes sin grupos_optativos", missing_grupo_ref)

missing_grupo_mat = set()
for g in grupos:
    for mid in g["materias"]:
        if mid not in mat_ids:
            missing_grupo_mat.add((g["id"], mid))
check("materias de grupos_optativos sin Materia", missing_grupo_mat)

print()
print("=== Departamento / Docente_Departamento ===")
check("codigos de departamento en docentes sin catalogo", dep_codes_docentes - dep_ids)
sin_mail = [d["nombre_completo"] for d in docentes if not d.get("mail")]
print(f"docentes sin mail: {len(sin_mail)} de {len(docentes)}")
avisos["docentes_sin_mail"] = len(sin_mail)

print()
print("=== Cuatrimestre / Evento ===")
ev_ids_bad = set()
ev_sin_cuatri = 0
for e in eventos:
    ids = ids_de_evento(e)
    if not ids:
        ev_sin_cuatri += 1
    for cid in ids:
        if cid not in cuatri_ids:
            ev_ids_bad.add(cid)
check("id_cuatri en eventos que no existen en catalogo", ev_ids_bad)
multi = [e["titulo"] for e in eventos if len(ids_de_evento(e)) > 1 or isinstance(e.get("id_cuatri"), list)]
check("eventos con mas de un id_cuatri (los separa scrapeoEvento.py: revisar el scraper)", multi)
ev_sin_fecha = [e["titulo"] for e in eventos if not e.get("fecha_ini")]
print(f"eventos sin fecha_ini (a definir): {len(ev_sin_fecha)} de {len(eventos)}; sin cuatrimestre: {ev_sin_cuatri}")
avisos["eventos_sin_fecha"] = len(ev_sin_fecha)
avisos["eventos_sin_cuatrimestre"] = ev_sin_cuatri
fechas_mal = [e["titulo"] for e in eventos
              if e.get("fecha_ini") and e.get("fecha_fin") and e["fecha_fin"] < e["fecha_ini"]]
check("eventos con fecha_fin < fecha_ini", fechas_mal)
ordenes = [c["orden"] for c in cuatris]
check("ordenes de cuatrimestre duplicados", [o for o in set(ordenes) if ordenes.count(o) > 1])

print()
print(f"=== Comision / Comision_Docente / Bloque_Horario (via {F_HORARIOS}) ===")
mats_en_horarios = set()
cuatris_en_horarios = set()
dias_en_horarios = set()
aulas_en_horarios = set()
bloques_mal = []
claves = {}
duplicadas = []
sin_horarios = []
for com in horarios:
    mats_en_horarios.add(com["materia"])
    cuatris_en_horarios.add(com["id_cuatri"])
    clave = (com["materia"], com["comision"], com["id_cuatri"])
    if clave in claves:
        duplicadas.append("/".join(clave))
    claves[clave] = True
    if not com.get("horarios"):
        sin_horarios.append(com["comision"])
    for h in com.get("horarios", []):
        dias_en_horarios.add(h.get("dia"))
        hi, hf = h.get("hora_inicio"), h.get("hora_fin")
        mi, mf = a_minutos(hi), a_minutos(hf)
        if mi is None or mf is None or mi >= mf:
            bloques_mal.append(f"{com['comision']} {h.get('dia')} {hi}-{hf}")
        for a in h.get("aulas", []):
            aulas_en_horarios.add((a.get("aula"), a.get("pabellon")))

check("materias en horarios sin existir en Materia", mats_en_horarios - mat_ids)
check("id_cuatri en horarios sin existir en catalogo", cuatris_en_horarios - cuatri_ids)
check(f"horarios que no son del cuatrimestre {CUATRI}", cuatris_en_horarios - {CUATRI})
check("dias en horarios sin matchear Dia.nombre", dias_en_horarios - dia_nombres)
check("bloques con hora invalida o hora_inicio >= hora_fin", bloques_mal)
check("comisiones duplicadas (materia, comision, cuatrimestre)", duplicadas)
if not horarios:
    problemas["horarios vacios"] = [F_HORARIOS]
if sin_horarios:
    print("comisiones sin ningun bloque horario:", len(sin_horarios))
avisos["comisiones_sin_horarios"] = len(sin_horarios)

# match de aulas via numero + aliases
aula_index = {}
for a in aulas:
    aula_index[(a["numero"], a["pabellon"])] = a["id_aula"]
    for alias in a.get("aliases", []):
        aula_index[(alias, a["pabellon"])] = a["id_aula"]

aulas_sin_match = set()
for aula_val, pab in aulas_en_horarios:
    if pab is None:
        continue
    candidatos = [aula_val, str(aula_val)]
    try:
        candidatos.append(int(aula_val))
    except (TypeError, ValueError):
        pass
    if not any((c, pab) in aula_index for c in candidatos):
        aulas_sin_match.add((aula_val, pab))
check("aulas usadas en horarios sin match en catalogo Aula (ni por numero ni por alias)", aulas_sin_match)

bloques_sin_aula = sum(1 for com in horarios for h in com.get("horarios", []) if not h.get("aulas"))
print(f"bloques horarios sin ninguna aula: {bloques_sin_aula}")
avisos["bloques_sin_aula"] = bloques_sin_aula

print()
print("=== Resumen tablas con datos ===")
resumen = {
    "Materia": len(materias), "Carrera": len(carreras), "Docente": len(docentes),
    "Departamento": len(departamentos), "Dia": len(dias), "Aula": len(aulas),
    "Cuatrimestre": len(cuatris), "Evento": len(eventos),
    f"Comision ({CUATRI})": len(horarios), "GRUPO_OPTATIVO": len(grupos),
}
for k, v in resumen.items():
    print(f"{k}: {v}")

print()
if problemas:
    print(f"AUDITORIA FALLIDA: {len(problemas)} chequeo(s) con problemas")
else:
    print("AUDITORIA OK (sin problemas; los avisos no frenan la carga)")

with open("auditoria.json", "w", encoding="utf-8") as f:
    json.dump({"ok": not problemas, "cuatri": CUATRI, "problemas": problemas,
               "avisos": avisos, "resumen": resumen}, f, ensure_ascii=False, indent=2)

sys.exit(1 if problemas else 0)
