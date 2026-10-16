#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Indexador del RAG de eXACTai: pasa los datos de Postgres a documentos de texto, y guarda sus
embeddings en pgvector (tabla rag_documento, en la misma base).

Es INCREMENTAL: cada documento tiene un id estable y un hash de su texto. Solo se calcula el
embedding de los documentos nuevos o cuyo texto cambio (o si cambio el modelo de embeddings).
Los documentos que ya no existen en la base se borran del indice.

    python rag/indexar.py                    sincroniza (lo normal; lo llama el pipeline despues de cargar)
    python rag/indexar.py --dry-run          muestra que cambiaria, sin escribir ni calcular embeddings
    python rag/indexar.py --tipos comision evento     solo esos tipos
    python rag/indexar.py --reembeber-todo   recalcula todos los embeddings (ej. cambiaste de modelo con la misma dimension)
    python rag/indexar.py --recrear          borra la tabla y la rehace (cambio de dimension) + recalcula todo
    python rag/indexar.py --exportar rag/export      ademas guarda los textos en archivos .md para leerlos
    python rag/indexar.py --forzar           permite borrar muchos documentos de golpe (aborta si >30 % del indice)

Conexion: --dsn, o variable EXACTAI_DSN, o los DB_* de herramientas_agente.py.
Modelo de embeddings: ver embeddings.py (EXACTAI_EMB_MODEL).
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

import documentos  # noqa: E402
import embeddings  # noqa: E402
import herramientas_agente as ha  # noqa: E402

MAX_BORRADO = 0.30       # si faltan mas de este % de los documentos ya indexados, aborta (como cargar_datos.py)
LOTE_EMBEDDINGS = 64


def log(msg=""):
    print(msg, flush=True)


def conectar(dsn=None):
    return ha.conectar(dsn or os.environ.get("EXACTAI_DSN") or None)


# =============================================================================
# Schema
# =============================================================================

def tabla_existe(cur):
    cur.execute("SELECT to_regclass('rag_documento') IS NOT NULL")
    return cur.fetchone()[0]


def dim_de_tabla(cur):
    cur.execute("""SELECT atttypmod FROM pg_attribute
                   WHERE attrelid = 'rag_documento'::regclass AND attname = 'embedding'""")
    fila = cur.fetchone()
    return fila[0] if fila else None


def asegurar_schema(conn, dim, recrear=False):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
    if not cur.fetchone():
        raise RuntimeError(
            "Este Postgres no tiene la extension pgvector. Usa una imagen que la traiga "
            "(por ejemplo pgvector/pgvector:pg16) - ver rag/README.md.")
    if recrear:
        cur.execute("DROP TABLE IF EXISTS rag_documento")
    elif tabla_existe(cur):
        dim_actual = dim_de_tabla(cur)
        if dim_actual != dim:
            raise RuntimeError(
                f"La tabla rag_documento tiene embeddings de {dim_actual} dimensiones y el modelo actual "
                f"da {dim}. Si cambiaste de modelo, corre: python rag/indexar.py --recrear")
    sql = (AQUI / "schema_rag.sql").read_text(encoding="utf-8").replace("__DIM__", str(int(dim)))
    cur.execute(sql)
    conn.commit()


# =============================================================================
# Sincronizacion
# =============================================================================

def _meta_igual(a, b):
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def planificar(docs, existentes, modelo, reembeber_todo=False):
    """Clasifica los documentos. `existentes`: id_doc -> dict con hash_texto, hash_embebido, modelo,
    tiene_embedding, metadata, id_*."""
    plan = {"nuevos": [], "cambiados": [], "reembeber": [], "solo_metadata": [], "iguales": [], "borrar": []}
    ids_nuevos = set()
    for d in docs:
        ids_nuevos.add(d["id_doc"])
        e = existentes.get(d["id_doc"])
        if e is None:
            plan["nuevos"].append(d)
        elif e["hash_texto"] != d["hash_texto"]:
            plan["cambiados"].append(d)
        elif (reembeber_todo or not e["tiene_embedding"] or e["hash_embebido"] != d["hash_texto"]
              or e["modelo"] != modelo):
            plan["reembeber"].append(d)      # el texto es el mismo pero falta/está viejo el embedding
        elif (not _meta_igual(e["metadata"], d["metadata"]) or
              (e["id_cuatri"], e["id_materia"], e["id_carrera"], e["id_docente"]) !=
              (d["id_cuatri"], d["id_materia"], d["id_carrera"], d["id_docente"])):
            plan["solo_metadata"].append(d)  # cambio algo que no se "lee": se actualiza sin re-embeber
        else:
            plan["iguales"].append(d)
    plan["borrar"] = sorted(set(existentes) - ids_nuevos)
    return plan


def leer_existentes(cur, tipos=None):
    sql = """SELECT id_doc, hash_texto, hash_embebido, modelo, embedding IS NOT NULL, metadata,
                    id_cuatri, id_materia, id_carrera, id_docente FROM rag_documento"""
    params = []
    if tipos:
        sql += " WHERE tipo = ANY(%s)"
        params.append(list(tipos))
    cur.execute(sql, params)
    return {r[0]: {"hash_texto": r[1], "hash_embebido": r[2], "modelo": r[3], "tiene_embedding": r[4],
                   "metadata": r[5], "id_cuatri": r[6], "id_materia": r[7], "id_carrera": r[8],
                   "id_docente": r[9]} for r in cur.fetchall()}


def resumen_por_tipo(docs):
    return ", ".join(f"{t}: {n}" for t, n in sorted(Counter(d["tipo"] for d in docs).items())) or "-"


def exportar(docs, carpeta):
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    por_tipo = {}
    for d in docs:
        por_tipo.setdefault(d["tipo"], []).append(d)
    for tipo, lista in por_tipo.items():
        partes = [f"## {d['titulo']}\n\n`{d['id_doc']}`\n\n{d['contenido']}\n" for d in lista]
        (carpeta / f"{tipo}.md").write_text("\n---\n\n".join(partes), encoding="utf-8")
    log(f"Textos exportados a {carpeta} ({len(docs)} documentos en {len(por_tipo)} archivos)")


def sincronizar(dsn=None, tipos=None, dry_run=False, reembeber_todo=False, recrear=False,
                forzar=False, carpeta_export=None, data_dir=None):
    conn = conectar(dsn)
    try:
        cur = conn.cursor()
        docs = documentos.generar_documentos(cur, tipos=tipos, data_dir=data_dir)
        log(f"Documentos generados desde la base: {len(docs)} ({resumen_por_tipo(docs)})")
        if carpeta_export:
            exportar(docs, carpeta_export)

        existe = tabla_existe(cur) and not recrear
        existentes = leer_existentes(cur, tipos) if existe else {}

        if dry_run and not existe:
            emb_modelo = None
            plan = planificar(docs, {}, None)
        else:
            emb = embeddings.obtener_embedder() if not dry_run else None
            emb_modelo = emb.nombre if emb else (next(iter(
                {e["modelo"] for e in existentes.values() if e["modelo"]}), None))
            plan = planificar(docs, existentes, emb_modelo,
                              reembeber_todo=reembeber_todo or recrear)

        a_embeber = plan["nuevos"] + plan["cambiados"] + plan["reembeber"]
        log("")
        log("=== Plan ===")
        log(f"Nuevos: {len(plan['nuevos'])} | Con texto cambiado: {len(plan['cambiados'])} | "
            f"Sin embedding al día: {len(plan['reembeber'])} | Solo metadata: {len(plan['solo_metadata'])} | "
            f"Sin cambios: {len(plan['iguales'])} | A borrar: {len(plan['borrar'])}")
        log(f"Embeddings a calcular: {len(a_embeber)} de {len(docs)}")
        for etiqueta, lista in (("nuevos", plan["nuevos"]), ("cambiados", plan["cambiados"])):
            if lista:
                muestra = ", ".join(d["id_doc"] for d in lista[:4])
                log(f"  {etiqueta}: {muestra}{' ...' if len(lista) > 4 else ''}")
        if plan["borrar"]:
            log(f"  a borrar: {', '.join(plan['borrar'][:4])}{' ...' if len(plan['borrar']) > 4 else ''}")

        # Salvaguarda por tipo (como cargar_datos.py con las comisiones): si desaparece mas del 30 %
        # de los documentos de un tipo, casi seguro la base quedo incompleta (scraping fallido).
        if not forzar:
            tipo_de = lambda id_doc: id_doc.split(":", 1)[0]  # noqa: E731
            existentes_por_tipo = Counter(tipo_de(i) for i in existentes)
            borrar_por_tipo = Counter(tipo_de(i) for i in plan["borrar"])
            for tipo, n in borrar_por_tipo.items():
                total_tipo = existentes_por_tipo[tipo]
                if total_tipo >= 20 and n / total_tipo > MAX_BORRADO:
                    raise RuntimeError(
                        f"Se borrarian {n} de {total_tipo} documentos de tipo '{tipo}' "
                        f"(>{int(MAX_BORRADO * 100)} %). Parece un error (¿base incompleta?). "
                        f"Revisa la base; si es a proposito: --forzar")

        if dry_run:
            log("\n(dry-run: no se escribió nada ni se calcularon embeddings)")
            return {"ok": True, "dry_run": True, "plan": {k: len(v) for k, v in plan.items()}}

        # --- embeddings (fuera de la transaccion: es lo lento) ---------------------------------
        if recrear or not existe:
            asegurar_schema(conn, emb.dim, recrear=recrear)
        else:
            asegurar_schema(conn, emb.dim)    # valida dimension, crea indices que falten
        vectores = {}
        if a_embeber:
            t0 = time.time()
            log(f"\nCalculando embeddings con {emb.nombre} ({emb.dim} dim)...")
            for i in range(0, len(a_embeber), LOTE_EMBEDDINGS):
                lote = a_embeber[i:i + LOTE_EMBEDDINGS]
                vecs = emb.docs([d["titulo"] + "\n" + d["contenido"] for d in lote])
                for d, v in zip(lote, vecs):
                    vectores[d["id_doc"]] = embeddings.a_pgvector(v)
                if (i // LOTE_EMBEDDINGS) % 5 == 0 or i + LOTE_EMBEDDINGS >= len(a_embeber):
                    log(f"  {min(i + LOTE_EMBEDDINGS, len(a_embeber))}/{len(a_embeber)}")
            log(f"Embeddings listos en {time.time() - t0:.1f} s")

        # --- escritura: una sola transaccion ---------------------------------------------------
        cur = conn.cursor()
        upsert = """
            INSERT INTO rag_documento (id_doc, tipo, id_cuatri, id_materia, id_carrera, id_docente,
                                       titulo, contenido, metadata, hash_texto, hash_embebido, modelo,
                                       embedding, actualizado_en, embebido_en)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::vector, now(), now())
            ON CONFLICT (id_doc) DO UPDATE SET
                tipo = EXCLUDED.tipo, id_cuatri = EXCLUDED.id_cuatri, id_materia = EXCLUDED.id_materia,
                id_carrera = EXCLUDED.id_carrera, id_docente = EXCLUDED.id_docente,
                titulo = EXCLUDED.titulo, contenido = EXCLUDED.contenido, metadata = EXCLUDED.metadata,
                hash_texto = EXCLUDED.hash_texto, hash_embebido = EXCLUDED.hash_embebido,
                modelo = EXCLUDED.modelo, embedding = EXCLUDED.embedding,
                actualizado_en = now(), embebido_en = now()"""
        filas = [(d["id_doc"], d["tipo"], d["id_cuatri"], d["id_materia"], d["id_carrera"], d["id_docente"],
                  d["titulo"], d["contenido"], json.dumps(d["metadata"], ensure_ascii=False),
                  d["hash_texto"], d["hash_texto"], emb.nombre, vectores[d["id_doc"]])
                 for d in a_embeber]
        if filas:
            psycopg2.extras.execute_batch(cur, upsert, filas, page_size=100)
        meta = [(json.dumps(d["metadata"], ensure_ascii=False), d["id_cuatri"], d["id_materia"],
                 d["id_carrera"], d["id_docente"], d["id_doc"]) for d in plan["solo_metadata"]]
        if meta:
            psycopg2.extras.execute_batch(cur, """
                UPDATE rag_documento SET metadata = %s::jsonb, id_cuatri = %s, id_materia = %s,
                       id_carrera = %s, id_docente = %s, actualizado_en = now() WHERE id_doc = %s""",
                                          meta, page_size=100)
        if plan["borrar"]:
            cur.execute("DELETE FROM rag_documento WHERE id_doc = ANY(%s)", (plan["borrar"],))
        conn.commit()
        cur.execute("SELECT tipo, count(*) FROM rag_documento GROUP BY 1 ORDER BY 1")
        total = dict(cur.fetchall())
        log(f"\nOK. Índice actual: {sum(total.values())} documentos ({', '.join(f'{k}: {v}' for k, v in total.items())})")
        return {"ok": True, "dry_run": False, "embebidos": len(a_embeber),
                "plan": {k: len(v) for k, v in plan.items()}, "total": total}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--tipos", nargs="+", choices=documentos.TIPOS, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reembeber-todo", action="store_true")
    ap.add_argument("--recrear", action="store_true")
    ap.add_argument("--forzar", action="store_true")
    ap.add_argument("--exportar", metavar="CARPETA", default=None)
    args = ap.parse_args()
    if args.recrear and args.tipos:
        ap.error("--recrear rehace toda la tabla: no se combina con --tipos")
    try:
        sincronizar(dsn=args.dsn, tipos=args.tipos, dry_run=args.dry_run,
                    reembeber_todo=args.reembeber_todo, recrear=args.recrear, forzar=args.forzar,
                    carpeta_export=args.exportar)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
