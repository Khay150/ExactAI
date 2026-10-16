#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Corre preguntas de ejemplo contra el agente y muestra que herramientas eligio y que respondio.
    python agente/probar_agente.py  [--modelo groq:openai/gpt-oss-120b]
Para revisar a ojo: ¿eligio la herramienta correcta? ¿la respuesta coincide con lo que devolvio?"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agente  # noqa: E402
from pydantic_ai.messages import ToolCallPart  # noqa: E402

PREGUNTAS = [
    "¿A qué hora y en qué aula se cursa Física 4?",
    "¿Quién da álgebra a la noche?",
    "¿Cuándo me puedo anotar a las materias del próximo cuatrimestre?",
    "¿Cuándo son los finales de diciembre?",
    "Quiero cursar Análisis II y Física 4, ¿se pisan los horarios?",
    "Armame un horario con Análisis II, Álgebra I y Física 4 sin cursar los viernes",
    "¿Qué necesito aprobar para cursar Análisis II? Ya aprobé Análisis I.",
    "¿Qué materias hay en segundo año de Ciencias de Datos?",
    "¿Cuál es el mail de Merzbacher?",
    "¿Cuántas horas tiene Química General?",
    "¿Cuál es la capital de Francia?",
]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelo", default=None)
    args = ap.parse_args()
    bot = agente.crear_agente(agente.construir_modelo(args.modelo))
    for q in PREGUNTAS:
        print(f"\n{'=' * 70}\n¿ {q} ?")
        try:
            r = agente.consultar(bot, q)
        except Exception as e:
            print(f"  ⚠ Error: {e}")
            continue
        for m in r.new_messages():
            for p in getattr(m, "parts", []):
                if isinstance(p, ToolCallPart):
                    print(f"  [tool] {p.tool_name}({p.args})")
        print(f"\n  {r.output}")


if __name__ == "__main__":
    main()
