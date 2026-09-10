#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chequeo integral de integridad referencial entre todos los JSON del proyecto."""
import json


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


carreras_mat = load("JSON/carreras_materias.json")
materias = carreras_mat["materias"]
grupos = carreras_mat["grupos_optativos"]
carreras = carreras_mat["carreras"]
docentes = load("JSON/docentes_total.json")["docentes"]
departamentos = load("JSON/departamento.json")
dias = load("JSON/dia.json")
aulas = load("JSON/aula.json")
cuatris = load("JSON/cuatrimestres_2026.json")["cuatrimestres"]
eventos = load("JSON/eventos.json")["eventos"]
horarios = load("JSON/horarios_2026_1c.json")

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
print("materia_id en planes sin Materia:", missing_plan_mat or "OK")
print("correlativas sin Materia:", missing_corr or "OK")
print("grupo_id en planes sin grupos_optativos:", missing_grupo_ref or "OK")

missing_grupo_mat = set()
for g in grupos:
    for mid in g["materias"]:
        if mid not in mat_ids:
            missing_grupo_mat.add((g["id"], mid))
print("materias de grupos_optativos sin Materia:", missing_grupo_mat or "OK")

print()
print("=== Departamento / Docente_Departamento ===")
codigos_sin_catalogo = dep_codes_docentes - dep_ids
print("codigos de departamento en docentes sin catalogo:", codigos_sin_catalogo or "OK")
sin_mail = [d["nombre_completo"] for d in docentes if not d.get("mail")]
print(f"docentes sin mail: {len(sin_mail)} de {len(docentes)}")

print()
print("=== Cuatrimestre / Evento ===")
ev_ids_bad = set()
multi = 0
for e in eventos:
    ids = e.get("id_cuatri", [])
    if len(ids) > 1:
        multi += 1
    for cid in ids:
        if cid not in cuatri_ids:
            ev_ids_bad.add(cid)
print("id_cuatri en eventos que no existen en catalogo:", ev_ids_bad or "OK")
print(f"eventos con mas de un id_cuatri (sigue pendiente el modelado N a N): {multi} de {len(eventos)}")
ordenes = [c["orden"] for c in cuatris]
print("ordenes duplicados:", [o for o in set(ordenes) if ordenes.count(o) > 1] or "OK (todos unicos)")

print()
print("=== Comision / Comision_Docente / Bloque_Horario (via horarios_2026_1c.json) ===")
mats_en_horarios = set()
cuatris_en_horarios = set()
dias_en_horarios = set()
aulas_en_horarios = set()
for com in horarios:
    mats_en_horarios.add(com["materia"])
    cuatris_en_horarios.add(com["id_cuatri"])
    for h in com.get("horarios", []):
        dias_en_horarios.add(h.get("dia"))
        for a in h.get("aulas", []):
            aulas_en_horarios.add((a.get("aula"), a.get("pabellon")))

print("materias en horarios sin existir en Materia:", mats_en_horarios - mat_ids or "OK")
print("id_cuatri en horarios sin existir en catalogo:", cuatris_en_horarios - cuatri_ids or "OK")
print("dias en horarios sin matchear Dia.nombre:", dias_en_horarios - dia_nombres or "OK")

# match de aulas via numero + aliases
aula_index = {}
for a in aulas:
    aula_index[(a["numero"], a["pabellon"])] = a["id_aula"]
    for alias in a.get("aliases", []):
        aula_index[(alias, a["pabellon"])] = a["id_aula"]
    # tambien probar version numerica del alias/numero
    for key in list(aula_index):
        pass

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
print("aulas usadas en horarios sin match en catalogo Aula (ni por numero ni por alias):", aulas_sin_match or "OK")

print()
print("=== Resumen tablas con datos ===")
print(f"Materia: {len(materias)}")
print(f"Carrera: {len(carreras)}")
print(f"Docente: {len(docentes)}")
print(f"Departamento: {len(departamentos)}")
print(f"Dia: {len(dias)}")
print(f"Aula: {len(aulas)}")
print(f"Cuatrimestre: {len(cuatris)}")
print(f"Evento: {len(eventos)}")
print(f"Comision (via horarios): {len(horarios)}")
print(f"GRUPO_OPTATIVO: {len(grupos)}")
