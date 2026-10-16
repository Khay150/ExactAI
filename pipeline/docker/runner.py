"""Servidor minimo para que n8n ejecute los pasos de pipeline.py.

POST /run  {"step": "transformar", "cuatri": "2026_2c", "forzar": false}
  -> {"ok": bool, "returncode": int, "stdout": str, "stderr": str}
GET /health -> "ok"
Solo acepta pasos de la lista y corre de a uno por vez.
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROYECTO = Path(os.environ.get("EXACTAI_HOME", "/data/exactai"))
PIPELINE = PROYECTO / "pipeline" / "pipeline.py"
PASOS = {"descargar-csv", "scrapear-horarios", "scrapear-eventos", "transformar",
         "auditar", "preview", "cargar", "indexar-rag", "todo"}
TIMEOUT = int(os.environ.get("RUNNER_TIMEOUT", "1800"))
LOCK = threading.Lock()


class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        if code >= 400:
            sys.stderr.write("RECHAZADO %s: %s\n" % (code, str(obj.get("error") or obj.get("stderr") or "")[:300]))
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"ok": False, "error": "no existe"})

    def do_POST(self):
        if self.path != "/run":
            return self._json(404, {"ok": False, "error": "no existe"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            sys.stderr.write("cuerpo recibido: %r\n" % (self.rfile.peek() if hasattr(self.rfile, "peek") else "",))
            return self._json(400, {"ok": False, "error": f"JSON invalido: {e}"})
        sys.stderr.write("pedido: %r\n" % (body,))
        paso = body.get("step")
        if paso not in PASOS:
            return self._json(400, {"ok": False, "error": f"paso invalido: {paso}"})
        cmd = [sys.executable, str(PIPELINE), paso]
        cuatri = str(body.get("cuatri") or "").strip()
        if cuatri:
            if not all(c.isalnum() or c == "_" for c in str(cuatri)):
                return self._json(400, {"ok": False, "error": "cuatri invalido"})
            cmd += ["--cuatri", str(cuatri)]
        if body.get("forzar"):
            cmd.append("--forzar")
        if not LOCK.acquire(blocking=False):
            return self._json(409, {"ok": False, "error": "ya hay un paso corriendo"})
        try:
            env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
            r = subprocess.run(cmd, cwd=str(PROYECTO), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=TIMEOUT, env=env)
            self._json(200 if r.returncode == 0 else 500,
                       {"ok": r.returncode == 0, "returncode": r.returncode,
                             "stdout": r.stdout, "stderr": r.stderr})
        except subprocess.TimeoutExpired:
            self._json(500, {"ok": False, "returncode": -1, "stdout": "",
                             "stderr": f"timeout de {TIMEOUT}s"})
        finally:
            LOCK.release()

    def log_message(self, fmt, *a):
        sys.stderr.write("%s\n" % (fmt % a))


if __name__ == "__main__":
    puerto = int(os.environ.get("RUNNER_PORT", "8000"))
    print(f"runner escuchando en :{puerto}, proyecto={PROYECTO}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", puerto), H).serve_forever()
