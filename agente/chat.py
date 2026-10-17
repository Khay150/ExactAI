#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chat por consola con el agente de eXACTai.   python agente/chat.py     (salir: Enter vacio, 'salir' o Ctrl+C)

--verbose  muestra que herramientas llamo el agente y con que argumentos (para entender sus decisiones).
--modelo   ej. groq:openai/gpt-oss-120b  /  ollama:llama3.1:8b   (o variable EXACTAI_LLM)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agente  # noqa: E402
from pydantic_ai.messages import ToolCallPart  # noqa: E402


def mostrar_tools(mensajes):
    for m in mensajes:
        for p in getattr(m, "parts", []):
            if isinstance(p, ToolCallPart):
                print(f"   [tool] {p.tool_name}({p.args})")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelo", default=None)
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()
    bot = agente.crear_agente(agente.construir_modelo(args.modelo))
    print("eXACTai — escribí tu pregunta (Enter vacío para salir)\n")
    historial = []
    while True:
        try:
            q = input("vos> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in ("salir", "exit", "quit"):
            break
        try:
            r = agente.consultar(bot, q, historial)
        except Exception as e:      # limite de la API, Ollama apagado, etc.
            print(f"\n⚠ Error: {e}\n")
            continue
        historial = agente.compactar_historial(r.all_messages())
        if args.verbose:
            mostrar_tools(r.new_messages())
        print(f"\neXACTai> {r.output}\n")


if __name__ == "__main__":
    main()
