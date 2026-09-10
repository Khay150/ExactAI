#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genera JSON/aula.json a partir de la lista de aulas por pabellon que armo Franco
a mano. Cada aula queda con un id_aula generado (slug de pabellon+numero),
el "numero" tal cual (como texto, para no perder aulas que no son numericas)
y el pabellon.
"""
import json
import re
import unicodedata

PABELLON_0 = [
    "1101", "1102", "1103", "1104", "1105", "1106", "1107", "1108", "1109",
    "1110", "1111", "1112", "1113", "1114", "1115",
    "1203", "1204", "1205", "1206", "1207", "1208", "1209",
    "1301", "1302", "1303", "1304", "1305", "1306", "1307", "1308", "1309",
    "1401", "1402", "1403",
]

PABELLON_1 = [
    "E24",
    "Laboratorio Bajas Temperaturas",
    "Laboratorio de Resonancia Magnetica Nuclear",
    "Laboratorio de Mecanica Elemental",
    "Laboratorio de Fotonica para Alumnos",
    "Laboratorio de Neurociencia Integrativa",
    "Laboratorio de Procesamiento de Imagenes",
    "Laboratorio de Sistemas Dinamicos",
    "Laboratorio de Microscopia y Microespectoscopia",
    "Laboratorio de Nanoscopia Lec",
    "Laboratorio de Laser Lec",
    "Laboratorio de Elect. Cuantica",
    "Laboratorio de Elect. Lec",
    "Biblioteca Lec",
    "Centro de Microscopias Avanzadas",
    "Laboratorio de Polimeros Materiales Compuestos",
    "Aula Federman",
    "Laboratorio 4",
    "Laboratorio 5",
    "Laboratorio de Ondas",
    "Aula 10",
    "Aula 11",
    "Aula Magna",
    "Aula 2",
    "Aula 3",
    "Aula 4",
    "Aula 5",
    "Aula 6",
    "Aula 7",
    "Aula 8",
    "Aula 9",
    "Labo 1",
    "Labo 2",
]

PABELLON_2 = [
    "Aula Magna",
    "Sala A (sector Biblioteca)",
    "Sala B (sector Biblioteca)",
    "Aula 101",
    "Aula 102",
    "Aula 103",
    "Aula 104",
    "Aula 5",
    "Aula 6",
    "Aula 107",
    "Aula 108",
    "Aula 109",
    "Aula 110",
    "Aula 11",
    "Aula 12",
    "Aula 13",
    "Aula 111",
    "Aula 112",
    "Aula 113",
    "Aula 114",
    "Aula 115",
    "Aula 116",
    "Aula 117",
    "Aula 201",
    "Aula 202",
    "Aula 203",
    "Aula 204",
    "Aula 205",
    "Aula 206",
    "Aula 207",
    "Aula 208",
    "Aula 209",
    "Aula 210",
    "Aula 211",
    "Aula 212",
    "Aula 214",
]

PABELLONES = {
    "0": PABELLON_0,
    "1": PABELLON_1,
    "2": PABELLON_2,
}


def slugify(s):
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def to_num(v):
    return int(v) if re.fullmatch(r"\d+", v) else v


PREFIJOS = ["aula", "sala", "biblioteca", "centro de"]
# OJO: "laboratorio"/"labo" queda sin sacar a proposito: si lo sacaramos,
# "Labo 2" y "Aula 2" normalizarian a la misma clave ("2") y serian
# indistinguibles. Los "Labo N" del scraping ya vienen con el prefijo tal
# cual, asi que matchean igual sin necesidad de sacarselo.


def normalizar(s):
    """Clave para matchear el mismo lugar escrito de formas distintas:
    saca acentos, pasa a minuscula, saca lo que esta entre parentesis,
    saca prefijos tipo 'Aula '/'Laboratorio '/'Sala ' y colapsa espacios."""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"\([^)]*\)", "", s)  # saca "(sector biblioteca)"
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for pref in PREFIJOS:
        if s.startswith(pref + " "):
            s = s[len(pref) + 1:].strip()
            break
    return s


aulas = []
seen_ids = set()
for pabellon, lista in PABELLONES.items():
    for numero in lista:
        base_id = f"p{pabellon}_{slugify(numero)}"
        aula_id = base_id
        i = 1
        while aula_id in seen_ids:
            i += 1
            aula_id = f"{base_id}_{i}"
        seen_ids.add(aula_id)
        aulas.append({
            "id_aula": aula_id,
            "numero": to_num(numero),
            "pabellon": int(pabellon),
            "aliases": [],
        })

# ---------------------------------------------------------------------------
# Matchear contra las variantes que aparecen scrapeadas en horarios_2026_1c.json
# para completar "aliases" automaticamente, y avisar de lo que no matcheo.
# ---------------------------------------------------------------------------
from collections import defaultdict

por_pabellon_raw = defaultdict(set)
try:
    with open("JSON/horarios_2026_1c.json", encoding="utf-8") as f:
        horarios = json.load(f)
    for c in horarios:
        for h in c.get("horarios", []):
            for a in h.get("aulas", []):
                if a.get("aula") is None:
                    continue
                por_pabellon_raw[a.get("pabellon")].add(str(a.get("aula")))
except FileNotFoundError:
    print("(no encontre JSON/horarios_2026_1c.json, salteo el matching de aliases)")

# indice normalizado -> aula, por pabellon
indice = defaultdict(dict)
for a in aulas:
    key = normalizar(a["numero"])
    indice[a["pabellon"]][key] = a

sin_match = []
for pabellon, raws in por_pabellon_raw.items():
    if pabellon is None:
        continue
    p = int(pabellon)
    for raw in raws:
        key = normalizar(raw)
        aula = indice.get(p, {}).get(key)
        if aula is None:
            sin_match.append((p, raw))
            continue
        # si el texto crudo no es exactamente igual al "numero" del catalogo,
        # lo guardo como alias para poder matchear directo despues
        if str(raw) != str(aula["numero"]) and raw not in aula["aliases"]:
            aula["aliases"].append(raw)

with open("JSON/aula.json", "w", encoding="utf-8") as f:
    json.dump(aulas, f, ensure_ascii=False, indent=2)

print("Total aulas:", len(aulas))
for pabellon in PABELLONES:
    print(f"  Pabellon {pabellon}: {len(PABELLONES[pabellon])}")

con_alias = [a for a in aulas if a["aliases"]]
print()
print(f"Aulas a las que se les agrego alias automaticamente: {len(con_alias)}")
for a in con_alias:
    print(f"  {a['id_aula']} (numero={a['numero']!r}, pabellon={a['pabellon']}): aliases={a['aliases']}")

if sin_match:
    print()
    print("Variantes usadas en horarios_2026_1c.json que NO matchearon con ninguna aula del catalogo:")
    for p, raw in sorted(sin_match):
        print(f"  pabellon={p} valor={raw!r}")
