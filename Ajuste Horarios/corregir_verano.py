#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Separa el periodo "verano" en sus dos sub-periodos reales:
  - vacaciones (Diciembre): inscripcion/publicacion de materias + "Turno de
    Diciembre" (exámenes) -> es la preparacion del verano del año siguiente.
  - intensivo (Enero-Marzo): las clases del curso de verano en si + "Turno de
    Febrero-Marzo".

Reescribe JSON/cuatrimestres_2026.json (separando 2025_verano en
2025_verano_vac, y 2026_verano en 2026_verano_int + 2026_verano_vac nuevo) y
JSON/eventos.json (reasignando cada evento al id_cuatri que corresponde).
Tambien recalcula "orden" para que sea una secuencia cronologica unica entre
todos los cuatrimestres (2025 y 2026 juntos).
"""
import json

CU_PATH = "JSON/cuatrimestres_2026.json"
EV_PATH = "JSON/eventos.json"

cu = json.load(open(CU_PATH, encoding="utf-8"))
ev = json.load(open(EV_PATH, encoding="utf-8"))

cuatris = {c["id_cuatri"]: c for c in cu["cuatrimestres"] if c["id_cuatri"] not in ("2025_verano", "2026_verano")}

cuatris["2025_verano_vac"] = {"id_cuatri": "2025_verano_vac", "anio": 2025, "periodo": "verano", "tipo": "vacaciones"}
cuatris["2026_verano_int"] = {"id_cuatri": "2026_verano_int", "anio": 2026, "periodo": "verano", "tipo": "intensivo"}
cuatris["2026_verano_vac"] = {"id_cuatri": "2026_verano_vac", "anio": 2026, "periodo": "verano", "tipo": "vacaciones"}

ORDEN_CRONOLOGICO = [
    "2025_verano_vac",  # diciembre 2025
    "2026_verano_int",  # enero-marzo 2026
    "2026_1c",
    "2026_1b",
    "2026_2b",
    "2026_inv",
    "2026_2c",
    "2026_3b",
    "2026_4b",
    "2026_verano_vac",  # diciembre 2026 (preparacion del verano 2027)
]

faltantes = set(ORDEN_CRONOLOGICO) - set(cuatris)
sobrantes = set(cuatris) - set(ORDEN_CRONOLOGICO)
if faltantes or sobrantes:
    raise SystemExit(f"Revisar manualmente -> faltan: {faltantes}, sobran: {sobrantes}")

resultado = []
for i, cid in enumerate(ORDEN_CRONOLOGICO, start=1):
    c = cuatris[cid]
    c["orden"] = i
    resultado.append(c)

cu["cuatrimestres"] = resultado
json.dump(cu, open(CU_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# ---------------------------------------------------------------------------
# eventos.json: reasignar id_cuatri
# ---------------------------------------------------------------------------
TITULOS_DICIEMBRE_2026 = {
    "Exámenes del 1er Llamado del Turno de Diciembre",
    "Inscripción al 2do Llamado del Turno de Diciembre",
    "Exámenes del 2do Llamado del Turno de Diciembre",
    "Inscripción al 3er Llamado del Turno de Diciembre",
    "Exámenes del 3er Llamado del Turno de Diciembre",
}

cambios = []
for e in ev["eventos"]:
    nuevos = []
    for cid in e.get("id_cuatri", []):
        nuevo = cid
        if cid == "2025_verano":
            nuevo = "2025_verano_vac"
        elif cid == "2026_verano":
            nuevo = "2026_verano_vac" if e["titulo"] in TITULOS_DICIEMBRE_2026 else "2026_verano_int"
        if nuevo != cid:
            cambios.append((e["titulo"], e["fecha_ini"], cid, nuevo))
        nuevos.append(nuevo)
    e["id_cuatri"] = nuevos

json.dump(ev, open(EV_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("cuatrimestres.json actualizado:")
for c in resultado:
    print(f"  orden={c['orden']:<2} id={c['id_cuatri']:<16} anio={c['anio']} tipo={c['tipo']}")

print()
print(f"eventos.json: {len(cambios)} referencias de id_cuatri actualizadas")
for titulo, fecha, viejo, nuevo in cambios:
    print(f"  [{fecha}] '{titulo}': {viejo} -> {nuevo}")
