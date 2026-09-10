#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Separa los eventos que hoy tienen mas de un id_cuatri (los que hablan de
"1er Cuatrimestre y 1er Bimestre" o "2do Cuatrimestre y 3er Bimestre") en dos
eventos independientes, uno por cada id_cuatri, con su titulo ajustado.

De paso, ya que despues de este split TODOS los eventos quedan con un unico
id_cuatri, se aprovecha para pasar el campo "id_cuatri" de lista a string
(un solo valor), que es como lo pide el modelo relacional (Evento.id_cuatri
es una FK simple).
"""
import json

EV_PATH = "JSON/eventos.json"

# titulo original -> [(titulo nuevo, id_cuatri), ...]
SPLITS = {
    "Publicación de Materias del 1er Cuatrimestre y 1er Bimestre": [
        ("Publicación de Materias del 1er Cuatrimestre", "2026_1c"),
        ("Publicación de Materias del 1er Bimestre", "2026_1b"),
    ],
    "Inscripción a Materias del 1er Cuatrimestres y 1er Bimestre": [
        ("Inscripción a Materias del 1er Cuatrimestre", "2026_1c"),
        ("Inscripción a Materias del 1er Bimestre", "2026_1b"),
    ],
    "Inscripción a materias del 1er Cuatrimestre y 1er Bimestre para Ingresantes 2026": [
        ("Inscripción a materias del 1er Cuatrimestre para Ingresantes 2026", "2026_1c"),
        ("Inscripción a materias del 1er Bimestre para Ingresantes 2026", "2026_1b"),
    ],
    "Publicación de Materias del 2do Cuatrimestre y 3er Bimestre": [
        ("Publicación de Materias del 2do Cuatrimestre", "2026_2c"),
        ("Publicación de Materias del 3er Bimestre", "2026_3b"),
    ],
    "Inscripción a Materias del 2do Cuatrimestre y 3er Bimestre": [
        ("Inscripción a Materias del 2do Cuatrimestre", "2026_2c"),
        ("Inscripción a Materias del 3er Bimestre", "2026_3b"),
    ],
    "Inscripción a Materias del 2do Cuatrimestre y 3er Bimestre para Ingresantes 2026": [
        ("Inscripción a Materias del 2do Cuatrimestre para Ingresantes 2026", "2026_2c"),
        ("Inscripción a Materias del 3er Bimestre para Ingresantes 2026", "2026_3b"),
    ],
}

ev = json.load(open(EV_PATH, encoding="utf-8"))
eventos = ev["eventos"]

nuevos = []
separados = 0
convertidos_a_string = 0

for e in eventos:
    ids = e.get("id_cuatri", [])
    if len(ids) > 1:
        variantes = SPLITS.get(e["titulo"])
        if variantes is None:
            raise SystemExit(f"Evento con mas de un id_cuatri sin mapeo manual, revisar: {e}")
        for nuevo_titulo, cid in variantes:
            nuevo = dict(e)
            nuevo["titulo"] = nuevo_titulo
            nuevo["id_cuatri"] = cid
            nuevos.append(nuevo)
        separados += 1
    else:
        e["id_cuatri"] = ids[0] if ids else None
        convertidos_a_string += 1
        nuevos.append(e)

ev["eventos"] = nuevos
json.dump(ev, open(EV_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print(f"Eventos originales: {len(eventos)}")
print(f"Eventos separados en 2: {separados}")
print(f"Eventos con id_cuatri pasado de lista a string: {convertidos_a_string}")
print(f"Total de eventos ahora: {len(nuevos)}")
