#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prueba manual de las 6 tools de Fase 1 (herramientas_agente.py) contra tu
Postgres real. Corre casos concretos con datos reales del cuatrimestre
2026_1c e imprime el resultado, para revisar a ojo que cada tool devuelve
lo esperado antes de conectarla al agente.

Uso (desde la carpeta "Modelo BD", con tu Postgres corriendo):
    python probar_herramientas.py
"""
import json

from herramientas_agente import (
    buscar_materia, info_comision, proximas_fechas, chequear_solapamiento,
    armar_horario, correlativas_faltantes, resolver_materia,
)


def mostrar(titulo, obj):
    print(f"\n{'=' * 70}\n{titulo}\n{'=' * 70}")
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


# -----------------------------------------------------------------------
# resolver_materia: caso ambiguo (varias materias con "algebra" o "analisis")
# -----------------------------------------------------------------------
mostrar("resolver_materia('algebra')  (esperado: varios candidatos)",
        resolver_materia("algebra"))

mostrar("resolver_materia('Analisis I')  (esperado: un match fuerte, analisis_1)",
        resolver_materia("Analisis I"))

# -----------------------------------------------------------------------
# Tool 1: buscar_materia
# -----------------------------------------------------------------------
mostrar("buscar_materia('algebra 1')", buscar_materia("algebra 1"))

# -----------------------------------------------------------------------
# Tool 2: info_comision
# -----------------------------------------------------------------------
mostrar("info_comision(materia='algebra 1', turno='mañana')",
        info_comision(materia="algebra 1", turno="mañana"))

# -----------------------------------------------------------------------
# Tool 3: proximas_fechas
# -----------------------------------------------------------------------
mostrar("proximas_fechas(limite=5)", proximas_fechas(limite=5))
mostrar("proximas_fechas(tipo='Inscripción', limite=5)",
        proximas_fechas(tipo="Inscripción", limite=5))

# -----------------------------------------------------------------------
# Tool 4: chequear_solapamiento
# -----------------------------------------------------------------------
# algebra_1_manana_tp1 y alc_manana_tp1 son ambas Martes 09:00-14:00 en el
# JSON de origen (2026_1c) -> tienen que salir como solapadas.
res_com = info_comision(materia="algebra 1", turno="mañana")
ids_algebra = {c["comision"]: c["id_comision"] for c in res_com["comisiones"]}
res_alc = info_comision(materia="alc", turno="mañana")
ids_alc = {c["comision"]: c["id_comision"] for c in res_alc["comisiones"]}

id_1 = ids_algebra.get("algebra_1_manana_tp1")
id_2 = ids_alc.get("alc_manana_tp1")
if id_1 and id_2:
    mostrar(f"chequear_solapamiento([{id_1}, {id_2}])  (esperado: se solapan)",
            chequear_solapamiento([id_1, id_2]))
else:
    print("\n(no encontre las comisiones de ejemplo para el test de solapamiento;"
          " revisar nombres de comision)")

# -----------------------------------------------------------------------
# Tool 5: armar_horario
# -----------------------------------------------------------------------
mostrar(
    "armar_horario(['algebra_1', 'analisis_1'], preferencias={'turno': 'mañana'})",
    armar_horario(["algebra_1", "analisis_1"], preferencias={"turno": "mañana"}, max_resultados=3),
)

# -----------------------------------------------------------------------
# Tool 6: correlativas_faltantes
# -----------------------------------------------------------------------
# quimi_org requiere quimi_gen en lbt y lcb.
mostrar(
    "correlativas_faltantes('quimi_org', materias_aprobadas=[])  (esperado: falta quimi_gen)",
    correlativas_faltantes("quimi_org", materias_aprobadas=[]),
)
mostrar(
    "correlativas_faltantes('quimi_org', materias_aprobadas=['quimi_gen'])  (esperado: puede_cursar=true)",
    correlativas_faltantes("quimi_org", materias_aprobadas=["quimi_gen"]),
)

print("\nListo. Revisa arriba que cada resultado tenga sentido.")
