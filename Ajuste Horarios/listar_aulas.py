#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Recorre JSON/horarios_2026_1c.json y lista todas las aulas distintas que
aparecen, agrupadas por pabellon y ordenadas, para chequear contra la lista
armada a mano.
"""
import json
from collections import defaultdict

with open("JSON/horarios_2026_1c.json", encoding="utf-8") as f:
    data = json.load(f)

por_pabellon = defaultdict(set)

for comision in data:
    for h in comision.get("horarios", []):
        for a in h.get("aulas", []):
            aula = a.get("aula")
            pabellon = a.get("pabellon")
            if aula is None:
                continue
            por_pabellon[pabellon].add(aula)


def sort_key(v):
    # para que ordene numericamente cuando se puede, y por texto si no
    try:
        return (0, int(v))
    except (TypeError, ValueError):
        return (1, str(v))


def pabellon_sort_key(v):
    try:
        return (0, int(v))
    except (TypeError, ValueError):
        return (1, str(v))


for pabellon in sorted(por_pabellon.keys(), key=pabellon_sort_key):
    aulas = sorted(por_pabellon[pabellon], key=sort_key)
    print(f"--- Pabellón {pabellon} ({len(aulas)} aulas) ---")
    for a in aulas:
        print(f"  {a}")
    print()

total = sum(len(v) for v in por_pabellon.values())
print(f"Total aulas distintas: {total}")
