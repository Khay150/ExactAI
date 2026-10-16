#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prueba del RAG de punta a punta, para correr y revisar a ojo (igual que probar_herramientas.py).

    python rag/probar_rag.py

1. Sincroniza el indice (escribe SOLO en la tabla rag_documento; no toca ninguna otra tabla).
   La primera vez descarga el modelo de embeddings (~0,5 GB) y tarda un par de minutos.
2. Sincroniza otra vez: tiene que decir "Embeddings a calcular: 0" (todo sin cambios).
3. Hace preguntas de ejemplo y muestra los 3 mejores resultados de cada una.

Para probar sin descargar el modelo (los resultados NO son semanticos, solo prueba el flujo):
    EXACTAI_EMB=fake python rag/probar_rag.py
"""
import os
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

import buscar  # noqa: E402
import embeddings  # noqa: E402
import indexar  # noqa: E402

PREGUNTAS = [
    # (pregunta, tipos para acotar o None)
    ("quien da algebra a la noche", None),
    ("a que hora cursa analisis 2", None),
    ("en que aula es fisica 4", None),
    ("cuando me anoto a las materias", ["evento"]),
    ("cuando son los finales de diciembre", ["evento"]),
    ("hasta cuando puedo inscribirme a los examenes", ["evento"]),
    ("mail de merzbacher", ["docente"]),
    ("que materias hay en segundo año de ciencias de datos", None),
    ("cuantas horas tiene la materia de quimica general", ["materia"]),
    ("cuanto dura la carrera de biotecnologia", None),
    ("mate 1", None),
]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    emb = embeddings.obtener_embedder()
    print(f"Modelo de embeddings: {emb.nombre} ({emb.dim} dimensiones)\n")

    print("=== 1) Sincronizando el índice ===")
    indexar.sincronizar()
    print("\n=== 2) Sincronizando de nuevo (no debería calcular ningún embedding) ===")
    r = indexar.sincronizar()
    if r["embebidos"] != 0:
        print(f"\n⚠ Se recalcularon {r['embebidos']} embeddings en la segunda corrida: el texto de algún documento "
              "no es estable. Avisame y lo reviso.")
    else:
        print("\n✔ Incremental OK: la segunda corrida no recalculó nada.")

    print("\n=== 3) Búsquedas de ejemplo ===")
    print("(dist = distancia coseno semántica: 0 = idéntico; sirve para calibrar distancia_max)")
    for pregunta, tipos in PREGUNTAS:
        res = buscar.buscar_info(pregunta, tipos=tipos, k=3)
        print(f"\n¿ {pregunta} ?" + (f"   [tipos: {', '.join(tipos)}]" if tipos else ""))
        for i, x in enumerate(res["resultados"], 1):
            print(f"  {i}. [{x['tipo']}] {x['titulo']}   ({x['encontrado_por']}, dist={x['distancia']})")
        if not res["resultados"]:
            print("  (sin resultados)")


if __name__ == "__main__":
    main()
