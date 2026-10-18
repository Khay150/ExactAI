#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Interfaz web de eXACTai: un servidor FastAPI que expone el agente y sirve la pagina del chat.

    python web/servidor.py                     -> http://127.0.0.1:8000
    python web/servidor.py --modelo groq:openai/gpt-oss-20b
    python web/servidor.py --host 0.0.0.0      -> accesible desde el celular en la misma wifi (ojo: sin login)

Reusa tal cual agente/agente.py (mismo modelo, mismas tools, mismo RAG). Cada pestaña del navegador es una
conversacion con su propio historial (en memoria; se pierde al reiniciar el servidor).
"""
import argparse
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as EsperaAgotada
from contextlib import asynccontextmanager
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(RAIZ / "agente"))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

MAX_CARACTERES = 1000          # largo maximo de una pregunta
TTL_SESION = 2 * 60 * 60       # una charla inactiva mas de 2 h se descarta
MAX_SESIONES = 200
TIEMPO_MAX = 150               # segundos maximos por pregunta antes de avisarle al usuario
CONCURRENTES = 2               # preguntas al LLM en simultaneo (el plan gratis de Groq es chico)

_estado = {"bot": None, "modelo": None}
_sesiones: dict[str, dict] = {}
_lock_sesiones = threading.Lock()
_cupo = threading.BoundedSemaphore(CONCURRENTES)
_pool = ThreadPoolExecutor(max_workers=CONCURRENTES * 2, thread_name_prefix="agente")


class Pregunta(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    mensaje: str = Field(min_length=1, max_length=MAX_CARACTERES)


class Respuesta(BaseModel):
    respuesta: str
    herramientas: list[str] = []


def _limpiar_sesiones():
    ahora = time.time()
    for k in [k for k, s in _sesiones.items() if ahora - s["ultimo"] > TTL_SESION]:
        del _sesiones[k]
    while len(_sesiones) > MAX_SESIONES:           # por si alguien abusa: se va la mas vieja
        del _sesiones[min(_sesiones, key=lambda k: _sesiones[k]["ultimo"])]


def _sesion(sid: str) -> dict:
    with _lock_sesiones:
        _limpiar_sesiones()
        s = _sesiones.setdefault(sid, {"historial": [], "lock": threading.Lock(), "ultimo": time.time()})
        s["ultimo"] = time.time()
        return s


def _herramientas_usadas(mensajes) -> list[str]:
    """Nombres (sin repetir, en orden) de las tools que llamo el agente en este turno."""
    vistas = []
    for m in mensajes:
        for p in getattr(m, "parts", []):
            if getattr(p, "part_kind", "") == "tool-call" and p.tool_name not in vistas:
                vistas.append(p.tool_name)
    return vistas


@asynccontextmanager
async def vida(app: FastAPI):
    import agente
    _estado["modelo"] = os.environ.get("EXACTAI_WEB_MODELO") or None
    _estado["agente"] = agente
    _estado["bot"] = agente.crear_agente(agente.construir_modelo(_estado["modelo"]))
    yield


app = FastAPI(title="eXACTai", lifespan=vida, docs_url=None, redoc_url=None)


app.mount("/static", StaticFiles(directory=AQUI / "static"), name="static")


@app.get("/")
def inicio():
    return FileResponse(AQUI / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/salud")
def salud():
    return {"ok": _estado["bot"] is not None, "sesiones": len(_sesiones)}


@app.post("/api/chat", response_model=Respuesta)
def chat(p: Pregunta):
    agente = _estado["agente"]
    s = _sesion(p.session_id)
    if not s["lock"].acquire(blocking=False):      # doble clic / dos mensajes a la vez en la misma charla
        raise HTTPException(409, "Todavía estoy respondiendo la pregunta anterior.")
    try:
        t0 = time.time()
        with _cupo:
            fut = _pool.submit(agente.consultar, _estado["bot"], p.mensaje.strip(), list(s["historial"]))
            r = fut.result(timeout=TIEMPO_MAX)
        s["historial"] = agente.compactar_historial(r.all_messages())
        usadas = _herramientas_usadas(r.new_messages())
        print(f"[ok] {time.time() - t0:.1f}s  tools={usadas}  q={p.mensaje[:60]!r}", flush=True)
        return Respuesta(respuesta=str(r.output), herramientas=usadas)
    except EsperaAgotada:                           # el hilo sigue en segundo plano, pero su resultado se descarta
        print(f"[timeout] > {TIEMPO_MAX}s  q={p.mensaje[:60]!r}", file=sys.stderr, flush=True)
        raise HTTPException(504, "Tardé demasiado en responder. Probá reformular la pregunta o intentá de nuevo.")
    except Exception as e:                          # limite de Groq, base caida, Ollama apagado...
        print(f"[error] {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        raise HTTPException(502, "No pude responder ahora. Probá de nuevo en unos segundos.") from e
    finally:
        s["lock"].release()


@app.delete("/api/chat/{session_id}")
def reiniciar(session_id: str):
    with _lock_sesiones:
        _sesiones.pop(session_id, None)
    return {"ok": True}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--modelo", default=None, help="ej. groq:openai/gpt-oss-120b (o variable EXACTAI_LLM)")
    args = ap.parse_args()
    if args.modelo:
        os.environ["EXACTAI_WEB_MODELO"] = args.modelo
    import uvicorn
    print(f"eXACTai web -> http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
          "   (Ctrl+C para cortar)", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
