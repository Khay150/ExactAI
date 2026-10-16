#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eXACTai - orquestador del pipeline de datos (lo que n8n llama paso por paso).

Cada paso es un subcomando; termina con exit code 0 si salio bien y distinto de 0 si no
(n8n usa eso para frenar el flujo y avisar). Los scripts originales de tu proyecto NO se
reescriben: este archivo los ejecuta en orden, dentro de una carpeta de trabajo
(pipeline/work/<cuatri>/), sin tocar JSON/ hasta que la carga a la base salio bien.

    python pipeline.py descargar-csv        baja las 6 planillas (lunes..sabado) a datos_crudos/<dia>.csv
    python pipeline.py scrapear-horarios    Depto. de Matematica + Depto. de Computacion  -> work/<cuatri>/raw/
    python pipeline.py scrapear-eventos     calendario academico                           -> work/<cuatri>/raw/
    python pipeline.py transformar          generar_json -> combinar_horarios -> generar_aulas
                                            -> script_cuatrimestre (catalogo final, verano incluido)
                                            (todo en work/<cuatri>/build/, arrancando siempre de cero)
    python pipeline.py auditar              auditoria_final.py (exit 1 si hay problemas)
    python pipeline.py preview              cargar_datos.py --dry-run: que cambiaria en la base (no guarda nada)
    python pipeline.py cargar               carga real (solo si los datos son los mismos que se vieron en preview)
                                            y, si salio bien, copia los JSON nuevos a JSON/ (con backup)
    python pipeline.py indexar-rag          reindexa el RAG (rag/indexar.py): solo recalcula embeddings de lo que cambio
                                            (correr despues de 'cargar'; no se incluye en 'todo')
    python pipeline.py todo                 de descargar-csv a preview, parando en el primer error

Opciones: --cuatri 2026_2c (si no, el de config.json), --config otro.json, --forzar (solo cargar).

Variable de entorno opcional: EXACTAI_DSN = postgresql://usuario:pass@host:5432/base
(si no esta, cargar_datos.py usa PG* o los valores DB_* que tiene adentro).
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PIPE = Path(__file__).resolve().parent
RAIZ = PIPE.parent

SCRIPTS = {
    "dm": RAIZ / "Scrapeo" / "Horarios" / "scrapeo_horarios_DM.py",
    "dc": RAIZ / "Scrapeo" / "Horarios" / "scrapeo_horarios_DC.py",
    "eventos": RAIZ / "Scrapeo" / "Eventos" / "scrapeoEvento.py",
    "generar_json": RAIZ / "datos_crudos" / "generar_json.py",
    "cuatrimestres": RAIZ / "datos_crudos" / "script_cuatrimestre.py",
    "combinar": RAIZ / "Ajuste Horarios" / "combinar_horarios.py",
    "aulas": RAIZ / "Ajuste Horarios" / "generar_aulas.py",
    "auditoria": RAIZ / "Ajuste Horarios" / "auditoria_final.py",
    "cargar": RAIZ / "Modelo BD" / "cargar_datos.py",
    "rag": RAIZ / "rag" / "indexar.py",
}

DIR_CRUDOS = RAIZ / "datos_crudos"
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado"]
COLUMNAS_PLANILLA = ["Actividades", "Turno", "Clase", "Día", "Inicio", "Fin", "Pab.", "Aula", "Fecha"]
# JSON "fijos" que se copian a la carpeta de trabajo (el resto lo genera el pipeline)
JSON_ESTATICOS = ["carreras_materias.json", "docentes_total.json", "departamento.json", "dia.json"]
TIMEOUT_SCRIPT = 900


class PasoFallido(Exception):
    def __init__(self, mensaje, codigo=1):
        super().__init__(mensaje)
        self.codigo = codigo


# =============================================================================
# Contexto / config
# =============================================================================

class Ctx:
    def __init__(self, cuatri, cfg):
        m = re.fullmatch(r"(\d{4})_(\w+)", cuatri or "")
        if not m:
            raise PasoFallido(f"cuatri invalido {cuatri!r} (formato esperado: 2026_2c)")
        self.cuatri = cuatri
        self.anio = int(m.group(1))
        self.periodo = m.group(2)
        self.cfg = cfg
        self.work = PIPE / "work" / cuatri
        self.raw = self.work / "raw"
        self.build = self.work / "build"
        self.logs = self.work / "logs"
        self.json_dir = self.build / "JSON"
        self.f_horarios = f"horarios_{cuatri}.json"
        self.f_cuatris = f"cuatrimestres_{self.anio}.json"

    def env(self):
        env = dict(os.environ)
        env.update({
            "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
            "EXACTAI_CUATRI": self.cuatri, "EXACTAI_ANIO": str(self.anio),
            "EXACTAI_CUATRIMESTRES": f"JSON/{self.f_cuatris}",
        })
        return env


def cargar_config(ruta):
    ruta = Path(ruta)
    if not ruta.exists():
        raise PasoFallido(f"No encuentro la config: {ruta}")
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


# =============================================================================
# Utilidades
# =============================================================================

def log(msg=""):
    print(msg, flush=True)


def correr_script(ctx, clave, cwd, args=(), extra_env=None, tolerar_fallo=False):
    """Ejecuta uno de los scripts del proyecto con cwd=cwd. Devuelve (returncode, salida)."""
    script = SCRIPTS[clave]
    if not script.exists():
        raise PasoFallido(f"No encuentro el script {script}")
    env = ctx.env()
    env.update(extra_env or {})
    ctx.logs.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run([sys.executable, str(script), *map(str, args)], cwd=str(cwd), env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=TIMEOUT_SCRIPT)
    except subprocess.TimeoutExpired:
        raise PasoFallido(f"{script.name} tardo mas de {TIMEOUT_SCRIPT}s y se corto")
    salida = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
    (ctx.logs / f"{clave}.log").write_text(salida, encoding="utf-8")
    if r.returncode != 0 and not tolerar_fallo:
        cola = "\n".join(salida.strip().splitlines()[-15:])
        raise PasoFallido(f"{script.name} fallo (codigo {r.returncode}):\n{cola}", codigo=1)
    return r.returncode, salida


def leer_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def sha_datos(ctx):
    h = hashlib.sha256()
    for nombre in (ctx.f_horarios, "eventos.json", "aula.json", ctx.f_cuatris):
        p = ctx.json_dir / nombre
        if not p.exists():
            raise PasoFallido(f"Falta {p.name} en la carpeta de trabajo: corre 'transformar'")
        h.update(nombre.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def guardar_estado(ctx, paso, ok, detalle=""):
    ctx.work.mkdir(parents=True, exist_ok=True)
    p = ctx.work / "estado.json"
    estado = leer_json(p) if p.exists() else {}
    estado[paso] = {"ok": ok, "cuando": datetime.now().isoformat(timespec="seconds"), "detalle": detalle[:500]}
    p.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")


# =============================================================================
# Pasos
# =============================================================================

def url_csv(url):
    """Convierte el link 'pubhtml' de una hoja publicada en el link de descarga CSV de esa misma hoja."""
    if "output=csv" in url:
        return url
    m = re.search(r"/d/e/([A-Za-z0-9_-]+)/", url)
    g = re.search(r"gid=(\d+)", url)
    if not m or not g:
        raise PasoFallido(f"No pude armar el link CSV a partir de: {url}")
    return f"https://docs.google.com/spreadsheets/d/e/{m.group(1)}/pub?gid={g.group(1)}&single=true&output=csv"


def paso_descargar_csv(ctx):
    import requests
    hojas = ctx.cfg.get("hojas_cartelera", {})
    faltan = [d for d in DIAS if not hojas.get(d)]
    if faltan:
        raise PasoFallido(f"config.json: faltan links en hojas_cartelera para: {', '.join(faltan)}")

    descargados = {}
    for dia in DIAS:
        u = url_csv(hojas[dia])
        try:
            r = requests.get(u, timeout=60)
            r.raise_for_status()
        except requests.RequestException as e:
            raise PasoFallido(f"No pude bajar la planilla de {dia} ({u}): {e}")
        texto = r.content.decode("utf-8-sig", errors="replace")
        if texto.lstrip().lower().startswith(("<!doctype", "<html", "<")):
            raise PasoFallido(f"La planilla de {dia} devolvio HTML, no CSV (revisa que siga publicada): {u}")
        filas = list(csv.reader(io.StringIO(texto)))
        if len(filas) < 3:
            raise PasoFallido(f"La planilla de {dia} vino casi vacia ({len(filas)} filas)")
        cabecera = [c.strip() for c in filas[1]]    # generar_json.py usa header=1 (la fila 0 es un titulo)
        sin_columnas = [c for c in COLUMNAS_PLANILLA if c not in cabecera]
        if sin_columnas:
            raise PasoFallido(f"La planilla de {dia} cambio de formato: faltan las columnas {sin_columnas} "
                              f"en la 2da fila (encontre: {cabecera})")
        descargados[dia] = (texto, len(filas) - 2)

    # recien cuando bajaron y validaron las 6, se reemplazan los archivos
    DIR_CRUDOS.mkdir(exist_ok=True)
    for dia, (texto, n) in descargados.items():
        (DIR_CRUDOS / f"{dia}.csv").write_text(texto, encoding="utf-8", newline="")
        log(f"  {dia}.csv: {n} filas")
    log(f"OK: 6 planillas guardadas en {DIR_CRUDOS}")


def _url_dm(ctx):
    plantilla = ctx.cfg.get("dm_url")
    if not plantilla:
        raise PasoFallido("config.json: falta dm_url")
    m = re.fullmatch(r"(\d+)c", ctx.periodo)
    if "{cuatrimestre}" in plantilla and not m:
        raise PasoFallido(f"Para el periodo {ctx.periodo!r} no se puede armar la URL del DM sola: "
                          "pone la URL completa en dm_url (sin {anio}/{cuatrimestre})")
    return plantilla.format(anio=ctx.anio, cuatrimestre=m.group(1) if m else "")


def _url_dc(ctx):
    """Nota del Depto. de Computacion: dc_urls (a mano) manda; si no, se arma con dc_url."""
    manual = (ctx.cfg.get("dc_urls") or {}).get(ctx.cuatri)
    if manual:
        return manual
    plantilla = ctx.cfg.get("dc_url")
    ordinal = {"1c": "primer", "2c": "segundo"}.get(ctx.periodo)
    if not plantilla or not ordinal:
        raise PasoFallido(
            f"No se puede armar la nota del DC para {ctx.cuatri}: agregala en config.json -> "
            f"dc_urls -> \"{ctx.cuatri}\"")
    return plantilla.format(ordinal=ordinal, anio=ctx.anio)


def paso_scrapear_horarios(ctx):
    dc_url = _url_dc(ctx)
    ctx.raw.mkdir(parents=True, exist_ok=True)
    for viejo in (ctx.raw / f"dm_{ctx.cuatri}.json", ctx.raw / f"materias_dc_{ctx.cuatri}.json"):
        viejo.unlink(missing_ok=True)

    correr_script(ctx, "dm", ctx.raw, extra_env={"EXACTAI_DM_URL": _url_dm(ctx)})
    correr_script(ctx, "dc", ctx.raw, extra_env={"EXACTAI_DC_URL": dc_url})

    for nombre in (f"dm_{ctx.cuatri}.json", f"materias_dc_{ctx.cuatri}.json"):
        p = ctx.raw / nombre
        if not p.exists():
            raise PasoFallido(f"El scraper no genero {nombre}")
        n = len(leer_json(p))
        if n == 0:
            raise PasoFallido(f"{nombre} quedo vacio: la pagina todavia no publica las materias de {ctx.cuatri}, "
                              "o cambio su estructura")
        log(f"  {nombre}: {n} registros")
    log("OK: horarios scrapeados (DM + DC)")


def paso_scrapear_eventos(ctx):
    ctx.raw.mkdir(parents=True, exist_ok=True)
    (ctx.raw / "eventos.json").unlink(missing_ok=True)
    correr_script(ctx, "eventos", ctx.raw)
    p = ctx.raw / "eventos.json"
    if not p.exists():
        raise PasoFallido("El scraper de eventos no genero eventos.json")
    n = len(leer_json(p)["eventos"])
    if n == 0:
        raise PasoFallido("El calendario academico no devolvio ningun evento (cambio la pagina?)")
    log(f"OK: {n} eventos scrapeados")


def paso_transformar(ctx):
    requeridos = [ctx.raw / f"dm_{ctx.cuatri}.json", ctx.raw / f"materias_dc_{ctx.cuatri}.json",
                  ctx.raw / "eventos.json"]
    faltan = [p.name for p in requeridos if not p.exists()]
    if faltan:
        raise PasoFallido(f"Faltan archivos scrapeados: {', '.join(faltan)} (corre scrapear-horarios / scrapear-eventos)")
    if not (DIR_CRUDOS / "lunes.csv").exists():
        raise PasoFallido("Faltan los CSV de las planillas (corre descargar-csv)")

    # Siempre desde cero: combinar_horarios.py reescribe sus archivos de entrada
    # (reemplaza nombres por ids), asi que no se puede correr dos veces sobre lo mismo.
    if ctx.build.exists():
        shutil.rmtree(ctx.build)
    ctx.json_dir.mkdir(parents=True)
    for nombre in JSON_ESTATICOS:
        shutil.copy2(RAIZ / "JSON" / nombre, ctx.json_dir / nombre)
    shutil.copy2(requeridos[0], ctx.build)
    shutil.copy2(requeridos[1], ctx.build)

    log("1/4 comisiones desde las planillas (generar_json)...")
    correr_script(ctx, "generar_json", ctx.build, extra_env={"EXACTAI_CSV_DIR": str(DIR_CRUDOS)})
    log("2/4 merge de comisiones + DM + DC (combinar_horarios)...")
    _, salida = correr_script(ctx, "combinar", ctx.build)
    for linea in salida.splitlines():
        if linea.startswith(("comisiones generadas", "descartadas", "avisos")):
            log("  " + linea)
    log("3/4 catalogo de aulas (generar_aulas)...")
    _, salida = correr_script(ctx, "aulas", ctx.build)
    for linea in salida.splitlines():
        if linea.startswith(("Total aulas", "Aulas a las que", "Variantes usadas")) or linea.startswith("  pabellon="):
            log("  " + linea)
    log("4/4 cuatrimestres (script_cuatrimestre)...")
    correr_script(ctx, "cuatrimestres", ctx.build)
    shutil.move(str(ctx.build / ctx.f_cuatris), str(ctx.json_dir / ctx.f_cuatris))
    # los eventos ya salen listos del scraper (multicuatri separados, verano resuelto)
    shutil.copy2(ctx.raw / "eventos.json", ctx.json_dir / "eventos.json")

    n = len(leer_json(ctx.json_dir / ctx.f_horarios))
    ne = len(leer_json(ctx.json_dir / "eventos.json")["eventos"])
    log(f"OK: {n} comisiones y {ne} eventos listos en {ctx.build}")


def paso_auditar(ctx):
    if not (ctx.json_dir / ctx.f_horarios).exists():
        raise PasoFallido("No hay datos transformados (corre 'transformar')")
    (ctx.build / "auditoria.json").unlink(missing_ok=True)
    rc, salida = correr_script(ctx, "auditoria", ctx.build, tolerar_fallo=True)
    (ctx.build / "auditoria.txt").write_text(salida, encoding="utf-8")
    p = ctx.build / "auditoria.json"
    if not p.exists():
        raise PasoFallido("La auditoria no termino (no genero auditoria.json):\n" +
                          "\n".join(salida.strip().splitlines()[-15:]))
    res = leer_json(p)
    avisos = res["avisos"]
    log("Avisos (no frenan la carga): " + ", ".join(f"{k}={v}" for k, v in avisos.items()))
    log("Resumen: " + ", ".join(f"{k}={v}" for k, v in res["resumen"].items()))
    if not res["ok"]:
        lineas = []
        for nombre, detalle in res["problemas"].items():
            lineas.append(f"- {nombre}: {len(detalle)} caso(s), ej: {', '.join(detalle[:5])}")
        raise PasoFallido("AUDITORIA FALLIDA, no se carga nada:\n" + "\n".join(lineas) +
                          f"\n(detalle completo: {ctx.build / 'auditoria.txt'})")
    log("OK: auditoria sin problemas")


def _args_cargar(ctx, reporte, extra=()):
    args = ["--data-dir", ctx.json_dir, "--horarios", ctx.f_horarios, "--eventos", "eventos.json",
            "--cuatrimestres", ctx.f_cuatris, "--reporte", reporte, *extra]
    dsn = os.environ.get("EXACTAI_DSN")
    if dsn:
        args += ["--dsn", dsn]
    return args


def _exigir_auditoria_ok(ctx):
    p = ctx.build / "auditoria.json"
    if not p.exists() or not leer_json(p).get("ok"):
        raise PasoFallido("La auditoria no esta aprobada para estos datos (corre 'auditar')")


def paso_preview(ctx):
    _exigir_auditoria_ok(ctx)
    huella = sha_datos(ctx)
    rc, salida = correr_script(ctx, "cargar", ctx.build,
                               _args_cargar(ctx, ctx.build / "reporte_preview.json", ["--dry-run"] +
                                            (["--forzar"] if ctx.cfg.get("_forzar") else [])),
                               tolerar_fallo=True)
    if rc != 0:
        raise PasoFallido("La simulacion de carga fallo:\n" + "\n".join(salida.strip().splitlines()[-12:]), codigo=rc)
    (ctx.build / ".preview_hash").write_text(huella, encoding="utf-8")
    inicio = salida.find("=== Resumen")
    log(salida[inicio:].strip() if inicio >= 0 else salida.strip())
    log("OK: preview listo (no se guardo nada en la base)")


def paso_cargar(ctx, forzar=False):
    _exigir_auditoria_ok(ctx)
    marca = ctx.build / ".preview_hash"
    if not marca.exists() or marca.read_text(encoding="utf-8").strip() != sha_datos(ctx):
        raise PasoFallido("No hay un preview vigente para estos datos (cambiaron, no hubo preview, o el preview ya se uso en una carga): "
                          "corre 'preview' de nuevo antes de cargar", codigo=3)

    rc, salida = correr_script(ctx, "cargar", ctx.build,
                               _args_cargar(ctx, ctx.build / "reporte_carga_bd.json", ["--forzar"] if forzar else []),
                               tolerar_fallo=True)
    if rc != 0:
        raise PasoFallido("La carga a la base fallo (no se guardo nada):\n" +
                          "\n".join(salida.strip().splitlines()[-12:]), codigo=rc)
    inicio = salida.find("=== Resumen")
    log(salida[inicio:].strip() if inicio >= 0 else salida.strip())

    # Base cargada OK -> recien ahora se actualizan los JSON del proyecto (con backup de lo que se pisa)
    backup = PIPE / "backups" / datetime.now().strftime("%Y%m%d_%H%M%S")
    copiados = []
    for nombre in (ctx.f_horarios, "eventos.json", "aula.json", ctx.f_cuatris):
        destino = RAIZ / "JSON" / nombre
        if destino.exists():
            backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destino, backup / nombre)
        shutil.copy2(ctx.json_dir / nombre, destino)
        copiados.append(nombre)
    shutil.copy2(ctx.build / "reporte_carga_bd.json", RAIZ / "reporte_carga_bd.json")
    marca.unlink(missing_ok=True)   # un preview aprobado sirve para UNA carga
    log(f"OK: base cargada. JSON/ actualizado: {', '.join(copiados)}" +
        (f" (backup de lo anterior en {backup})" if backup.exists() else ""))


def paso_indexar_rag(ctx):
    """Reindexa el RAG (pgvector) con lo que hay en la base. Incremental: solo recalcula lo que cambio."""
    args = []
    dsn = os.environ.get("EXACTAI_DSN")
    if dsn:
        args += ["--dsn", dsn]
    rc, salida = correr_script(ctx, "rag", RAIZ, args, tolerar_fallo=True)
    if rc != 0:
        raise PasoFallido("El indexado del RAG fallo (la base ya esta cargada; solo falta reindexar):\n" +
                          "\n".join(salida.strip().splitlines()[-12:]), codigo=rc)
    inicio = salida.find("=== Plan")
    log(salida[inicio:].strip() if inicio >= 0 else salida.strip())
    log("OK: RAG reindexado")


PASOS = {
    "descargar-csv": paso_descargar_csv,
    "scrapear-horarios": paso_scrapear_horarios,
    "scrapear-eventos": paso_scrapear_eventos,
    "transformar": paso_transformar,
    "auditar": paso_auditar,
    "preview": paso_preview,
    "indexar-rag": paso_indexar_rag,
}
SECUENCIA_TODO = ["descargar-csv", "scrapear-horarios", "scrapear-eventos", "transformar", "auditar", "preview"]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paso", choices=[*PASOS, "cargar", "todo"])
    ap.add_argument("--cuatri", help="ej. 2026_2c (default: el de config.json)")
    ap.add_argument("--config", default=str(PIPE / "config.json"))
    ap.add_argument("--forzar", action="store_true",
                    help="en preview/cargar: permite eliminar muchas comisiones de un cuatrimestre ya cargado")
    args = ap.parse_args()

    paso = args.paso
    try:
        cfg = cargar_config(args.config)
        cfg["_forzar"] = args.forzar
        ctx = Ctx(args.cuatri or cfg.get("cuatri"), cfg)
        log(f"[{paso}] cuatrimestre {ctx.cuatri}")
        if paso == "todo":
            for p in SECUENCIA_TODO:
                log(f"\n--- {p} ---")
                try:
                    PASOS[p](ctx)
                except PasoFallido:
                    guardar_estado(ctx, p, False)
                    raise
                guardar_estado(ctx, p, True)
            log("\nTodo listo hasta el preview. Para guardar en la base: pipeline.py cargar")
        elif paso == "cargar":
            paso_cargar(ctx, forzar=args.forzar)
            guardar_estado(ctx, paso, True)
        else:
            PASOS[paso](ctx)
            guardar_estado(ctx, paso, True)
    except PasoFallido as e:
        print(f"ERROR en '{paso}': {e}", file=sys.stderr, flush=True)
        try:
            guardar_estado(ctx, paso, False, str(e))
        except Exception:
            pass
        sys.exit(e.codigo)


if __name__ == "__main__":
    main()
