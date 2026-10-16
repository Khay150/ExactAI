#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Busqueda del RAG de eXACTai: la tool `buscar_info` que registra el agente.

Combina dos busquedas sobre la tabla rag_documento y las junta con Reciprocal Rank Fusion:
  - semantica: embedding de la pregunta contra el de cada documento (pgvector, distancia coseno)
  - por palabras: busqueda de texto en espanol sin tildes (nombres propios, aulas, codigos)
Asi una pregunta mal escrita o con otras palabras ("cuando me anoto") encuentra el documento
correcto, y un apellido exacto tambien.

Uso desde el agente (mismo estilo que herramientas_agente.py):
    sys.path.insert(0, ".../rag");  from buscar import buscar_info

Uso a mano:
    python rag/buscar.py "quien da algebra a la noche" --tipos comision -k 5
"""
import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(AQUI.parent / "agente"))   # herramientas_agente.py (conexion, norm, vigente)

import embeddings  # noqa: E402
import herramientas_agente as ha  # noqa: E402

TIPOS = ["comision", "materia", "evento", "docente", "carrera", "plan"]
CANDIDATOS = 30      # cuantos trae cada busqueda antes de fusionar
RRF_K = 60


# Jerga de estudiantes -> como aparece en los documentos. Solo afecta a la busqueda por palabras
# (la semantica ya se encarga del resto). Clave = prefijo del token; se agrega a la consulta, no la reemplaza.
JERGA = {
    "anot": ["inscripcion", "inscribir"],
    "inscrib": ["inscripcion"],
    "final": ["examen", "llamado"],
    "mesa": ["examen", "llamado"],
    "cuatri": ["cuatrimestre"],
    "profe": ["docente", "prof"],
    "segund": ["2do"],
    "primer": ["1er"],
    "tercer": ["3er"],
    "cursada": ["comision", "horario"],
    "cursar": ["comision", "horario"],
}


def _tokens(texto):
    # los digitos sueltos importan ("analisis 2" no es "analisis 3"); las letras sueltas no
    tokens = [t for t in re.findall(r"[a-z0-9]+", ha.norm(texto)) if len(t) >= 2 or t.isdigit()][:40]
    extra = []
    for t in tokens:
        for prefijo, equivalentes in JERGA.items():
            if t.startswith(prefijo):
                extra.extend(e for e in equivalentes if e not in tokens and e not in extra)
    return tokens + extra


def buscar_info(consulta, tipos=None, id_cuatri=None, id_materia=None, incluir_pasados=False,
                k=6, distancia_max=None, dsn=None):
    """Busqueda abierta sobre la informacion de la facultad (RAG). Usala para preguntas ambiguas,
    mal escritas o sin datos exactos ("¿quien da algebra a la noche?", "¿cuando me anoto a las
    materias?", "¿que materias hay en 2do anio de Datos?", "¿quien es Merzbacher?").
    NO la uses para calcular: horarios exactos de una comision conocida, solapamientos, armar un
    horario o correlativas faltantes tienen sus propias herramientas SQL. Si el resultado te
    sirve para identificar una comision o materia, confirma el dato exacto con esas herramientas.

    Args:
        consulta: la pregunta o frase del usuario, tal cual (no hace falta limpiarla).
        tipos: lista para acotar: "comision", "materia", "evento", "docente", "carrera", "plan".
        id_cuatri: cuatrimestre (ej. "2026_2c"). Si no se pasa, las comisiones salen del
            cuatrimestre vigente y los eventos son los que todavia no terminaron.
        id_materia: acota a una materia (id interno, ej. "algebra_1").
        incluir_pasados: True para incluir eventos que ya pasaron.
        k: cantidad de resultados (default 6).
        distancia_max: descarta los resultados que solo salieron por similitud semantica y estan
            mas lejos que esto (distancia coseno, 0 = identico, ~1 = sin relacion). Default: no
            filtra. Hay que calibrarlo con el modelo real (ver probar_rag.py).
    Devuelve {"resultados": [{id_doc, tipo, titulo, contenido, metadata, ...}], ...}: el campo
    `contenido` es el texto con la informacion; `metadata` trae ids para seguir con otras tools.
    """
    consulta = (consulta or "").strip()
    if not consulta:
        return {"consulta": consulta, "resultados": [], "nota": "consulta vacia"}
    if tipos:
        malos = set(tipos) - set(TIPOS)
        if malos:
            raise ValueError(f"tipos invalidos: {sorted(malos)}. Validos: {TIPOS}")
    k = max(1, min(int(k), 20))

    emb = embeddings.obtener_embedder()
    qvec = embeddings.a_pgvector(emb.consulta(consulta))
    tokens = _tokens(consulta)
    tsq = " | ".join(f"'{t}'" for t in tokens)

    conn = ha.conectar(dsn or os.environ.get("EXACTAI_DSN") or None)
    try:
        cur = conn.cursor()
        vigente = ha.cuatrimestre_vigente(cur)
        params = {"q": qvec, "tsq": tsq, "n": CANDIDATOS, "k": k,
                  "cuatri_com": id_cuatri or vigente, "hoy": date.today().isoformat()}
        donde = ["TRUE"]
        if tipos:
            donde.append("tipo = ANY(%(tipos)s)")
            params["tipos"] = list(tipos)
        donde.append("(tipo <> 'comision' OR id_cuatri = %(cuatri_com)s)")
        if id_cuatri:
            donde.append("(tipo <> 'evento' OR id_cuatri = %(id_cuatri)s)")
            params["id_cuatri"] = id_cuatri
        elif not incluir_pasados:
            fin = "coalesce(metadata->>'fecha_fin', metadata->>'fecha_ini')"
            donde.append(f"(tipo <> 'evento' OR {fin} IS NULL OR {fin} >= %(hoy)s)")
        if id_materia:
            donde.append("(id_materia = %(id_materia)s OR metadata->'materias' ? %(id_materia)s)")
            params["id_materia"] = id_materia
        filtro = " AND ".join(donde)

        if tokens:
            fts = f"""
                SELECT id_doc, row_number() OVER (ORDER BY ts_rank_cd(tsv, query) DESC) AS r
                FROM rag_documento, to_tsquery('es_unaccent', %(tsq)s) AS query
                WHERE tsv @@ query AND {filtro}
                ORDER BY ts_rank_cd(tsv, query) DESC LIMIT %(n)s"""
        else:
            fts = "SELECT NULL::text AS id_doc, NULL::bigint AS r WHERE false"

        cur.execute(f"""
            WITH vec AS (
                SELECT id_doc, row_number() OVER (ORDER BY embedding <=> %(q)s::vector) AS r
                FROM rag_documento
                WHERE embedding IS NOT NULL AND {filtro}
                ORDER BY embedding <=> %(q)s::vector LIMIT %(n)s
            ), fts AS ({fts}),
            comb AS (
                SELECT id_doc,
                       coalesce(1.0 / ({RRF_K} + v.r), 0) + coalesce(1.0 / ({RRF_K} + f.r), 0) AS score,
                       v.r AS rv, f.r AS rf
                FROM vec v FULL JOIN fts f USING (id_doc)
            )
            SELECT d.id_doc, d.tipo, d.titulo, d.contenido, d.metadata, d.id_cuatri, d.id_materia,
                   d.id_carrera, d.id_docente, c.score, c.rv, c.rf, d.embedding <=> %(q)s::vector
            FROM comb c JOIN rag_documento d USING (id_doc)
            ORDER BY c.score DESC, d.id_doc LIMIT %(k)s""", params)
        filas = cur.fetchall()
    finally:
        conn.close()

    resultados = []
    for (id_doc, tipo, titulo, contenido, metadata, f_cuatri, f_materia, f_carrera, f_docente,
         score, rv, rf, dist) in filas:
        if distancia_max is not None and not rf and (dist is None or dist > distancia_max):
            continue
        resultados.append({
            "id_doc": id_doc, "tipo": tipo, "titulo": titulo, "contenido": contenido,
            "id_cuatri": f_cuatri, "id_materia": f_materia, "id_carrera": f_carrera,
            "id_docente": f_docente, "metadata": metadata, "score": round(float(score), 5),
            "distancia": round(float(dist), 4) if dist is not None else None,
            "encontrado_por": ("semantica+palabras" if rv and rf else "semantica" if rv else "palabras"),
        })
    salida = {"consulta": consulta, "cuatrimestre_vigente": vigente, "resultados": resultados}
    if not resultados:
        salida["nota"] = ("Sin resultados. ¿Esta indexado? Corre: python rag/indexar.py. "
                          "O probá sin filtros / con otras palabras.")
    return salida


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("consulta")
    ap.add_argument("--tipos", nargs="+", choices=TIPOS)
    ap.add_argument("--cuatri", default=None)
    ap.add_argument("--materia", default=None)
    ap.add_argument("--pasados", action="store_true", help="incluir eventos que ya pasaron")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--distancia-max", type=float, default=None)
    ap.add_argument("--json", action="store_true", help="salida JSON completa")
    args = ap.parse_args()
    r = buscar_info(args.consulta, tipos=args.tipos, id_cuatri=args.cuatri, id_materia=args.materia,
                    incluir_pasados=args.pasados, k=args.k, distancia_max=args.distancia_max)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return
    print(f"Consulta: {r['consulta']}   (cuatrimestre vigente: {r.get('cuatrimestre_vigente')})")
    for i, x in enumerate(r["resultados"], 1):
        print(f"\n{i}. [{x['tipo']}] {x['titulo']}   score={x['score']} dist={x['distancia']} ({x['encontrado_por']})")
        for linea in x["contenido"].splitlines()[:6]:
            print(f"     {linea}")
    if r.get("nota"):
        print("\n" + r["nota"])


if __name__ == "__main__":
    main()
