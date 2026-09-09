#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
1) Reemplaza el nombre de materia por su id (segun JSON/carreras_materias.json)
   en comisiones.json, dm_2026_1c.json y materias_dc_2026_1c.json, y borra las
   filas cuya materia no matchea ningun alias.
2) Combina los tres archivos (ya con ids) en JSON/horarios_2026_1c.json con el
   formato unificado (materia/comision/id_cuatri/profesores/horarios).
Genera ademas JSON/reporte_merge_horarios.json con lo que se descarto y avisos.
"""
import json
import re
import shutil
import unicodedata
from collections import defaultdict

ID_CUATRI = "2026_1c"


def norm(s):
    s = unicodedata.normalize("NFC", str(s))
    s = re.sub(r"\s+", " ", s.strip())
    return s.lower()


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def slugify(s):
    if s is None:
        return "unica"
    s = str(s).strip()
    if not s:
        return "unica"
    s = s.lower()
    repl = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "ü": "u"}
    for a, b in repl.items():
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "unica"


def to_nombre_apellido(name):
    name = name.strip()
    if "," in name:
        ap, nom = name.split(",", 1)
        return f"{nom.strip()} {ap.strip()}".strip()
    return name


def norm_hora(v):
    if v is None:
        return None
    v = str(v).strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", v)
    if not m:
        return v
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def to_num(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    return s


# ---------------------------------------------------------------------------
# Cargar diccionario de materias y armar los 3 mapas de alias -> id
# ---------------------------------------------------------------------------
carreras = load("JSON/carreras_materias.json")
materias_dict = carreras["materias"]


def build_map(key):
    m = {}
    for mat in materias_dict:
        for v in mat["aliases_externos"].get(key, []):
            v = v.strip()
            if v:
                m[norm(v)] = mat["id"]
    return m


gs_map = build_map("google_sheets")
dm_map = build_map("dm")
dc_map = build_map("dc")

report = {"dropped": {}, "warnings": []}


def replace_and_filter(data, name_map, source_label):
    kept = []
    dropped_names = set()
    for entry in data:
        mid = name_map.get(norm(entry["materia"]))
        if mid is None:
            dropped_names.add(entry["materia"])
            continue
        entry = dict(entry)
        entry["materia"] = mid
        kept.append(entry)
    report["dropped"][source_label] = sorted(dropped_names)
    return kept


com_raw = load("comisiones.json")
dm_raw = load("dm_2026_1c.json")
dc_raw = load("materias_dc_2026_1c.json")

com_clean = replace_and_filter(com_raw, gs_map, "comisiones.json")
dm_clean = replace_and_filter(dm_raw, dm_map, "dm_2026_1c.json")
dc_clean = replace_and_filter(dc_raw, dc_map, "materias_dc_2026_1c.json")

for fname in ("comisiones.json", "dm_2026_1c.json", "materias_dc_2026_1c.json"):
    shutil.copy(fname, fname + ".bak")

save("comisiones.json", com_clean)
save("dm_2026_1c.json", dm_clean)
save("materias_dc_2026_1c.json", dc_clean)

# ---------------------------------------------------------------------------
# Armar "bloques" de horario+docentes a partir de dm (por tipo TPn/PC/etc)
# ---------------------------------------------------------------------------
dm_bloques = []
for row in dm_clean:
    profs = []
    for p in row.get("profesores", []):
        nombre = (p.get("nombre") or "").strip()
        if not nombre:
            continue
        rol = p.get("rol")
        rol = rol.strip() if isinstance(rol, str) and rol.strip() else None
        profs.append({"nombre": nombre, "rol": rol})
    horarios = []
    for dia in row.get("dias", []):
        horarios.append({
            "dia": dia,
            "hora_inicio": row.get("hora_inicio"),
            "hora_fin": row.get("hora_fin"),
        })
    dm_bloques.append({
        "materia": row["materia"],
        "turno": row.get("turno"),
        "tipo": row.get("tipo"),
        "profesores": profs,
        "horarios": horarios,
    })

dm_groups = defaultdict(list)
for b in dm_bloques:
    dm_groups[(b["materia"], b["turno"])].append(b)

TP_RE = re.compile(r"^tp\d+$", re.IGNORECASE)

comisiones_out = []
warnings = []


def merge_prof_lists(base, extra):
    seen = {(p["nombre"].lower(), p["rol"]) for p in base}
    for p in extra:
        k = (p["nombre"].lower(), p["rol"])
        if k not in seen:
            seen.add(k)
            base.append(p)


for (materia, turno), bloques in dm_groups.items():
    primaries = [b for b in bloques if TP_RE.match(b["tipo"] or "")]
    shared = [b for b in bloques if not TP_RE.match(b["tipo"] or "")]

    if primaries and len(primaries) > 1:
        for b in primaries:
            profs = list(b["profesores"])
            horarios = [dict(h, turno=turno, tipo=b["tipo"]) for h in b["horarios"]]
            for s in shared:
                horarios.extend(dict(h, turno=turno, tipo=s["tipo"]) for h in s["horarios"])
                merge_prof_lists(profs, s["profesores"])
            cid = f"{materia}_{slugify(turno)}_{slugify(b['tipo'])}"
            comisiones_out.append({
                "materia": materia, "comision": cid, "id_cuatri": ID_CUATRI,
                "profesores": profs, "horarios": horarios,
            })
    else:
        profs = []
        horarios = []
        for b in bloques:
            merge_prof_lists(profs, b["profesores"])
            horarios.extend(dict(h, turno=turno, tipo=b["tipo"]) for h in b["horarios"])
        cid = f"{materia}_{slugify(turno)}"
        comisiones_out.append({
            "materia": materia, "comision": cid, "id_cuatri": ID_CUATRI,
            "profesores": profs, "horarios": horarios,
        })

# ---------------------------------------------------------------------------
# dc ya viene a nivel de comision (una fila = una comision)
# ---------------------------------------------------------------------------
for row in dc_clean:
    profs = []
    for name in row.get("profesores", []):
        name = (name or "").strip()
        if not name:
            continue
        profs.append({"nombre": to_nombre_apellido(name), "rol": None})
    horarios = []
    for h in row.get("horarios", []):
        horarios.append({
            "dia": h.get("dia"),
            "hora_inicio": h.get("hora_inicio"),
            "hora_fin": h.get("hora_fin"),
            "turno": row.get("turno"),
            "tipo": h.get("tipo"),
        })
    cid = f"{row['materia']}_{slugify(row.get('turno'))}"
    comisiones_out.append({
        "materia": row["materia"], "comision": cid, "id_cuatri": ID_CUATRI,
        "profesores": profs, "horarios": horarios,
    })

# de-duplicar ids de comision por las dudas
seen_ids = {}
for c in comisiones_out:
    base = c["comision"]
    if base in seen_ids:
        seen_ids[base] += 1
        c["comision"] = f"{base}_{seen_ids[base]}"
    else:
        seen_ids[base] = 0

# ---------------------------------------------------------------------------
# Materias que solo aparecen en comisiones.json (sin fila en dm/dc) -> igual
# se agregan, sin docentes (no hay de donde sacarlos)
# ---------------------------------------------------------------------------
dm_dc_ids = {materia for materia, _ in dm_groups.keys()} | {r["materia"] for r in dc_clean}
com_only = [c for c in com_clean if c["materia"] not in dm_dc_ids]
com_only_by_key = defaultdict(list)
for c in com_only:
    com_only_by_key[(c["materia"], c.get("turno"), c.get("tipo"))].append(c)

for (materia, turno, tipo), rows in com_only_by_key.items():
    horarios = []
    for r in rows:
        horarios.append({
            "dia": r.get("dia"), "hora_inicio": r.get("hora_inicio"), "hora_fin": r.get("hora_fin"),
            "turno": turno, "tipo": tipo,
            "aulas": [{"aula": to_num(r.get("aula")), "pabellon": to_num(r.get("pabellon"))}],
        })
    cid_base = f"{materia}_{slugify(turno) if turno else slugify(tipo)}"
    cid = cid_base
    i = 1
    while cid in seen_ids:
        i += 1
        cid = f"{cid_base}_{i}"
    seen_ids[cid] = 0
    comisiones_out.append({
        "materia": materia, "comision": cid, "id_cuatri": ID_CUATRI,
        "profesores": [], "horarios": horarios,
    })
    warnings.append(
        f"Materia '{materia}' turno={turno!r} tipo={tipo!r}: solo tiene datos de comisiones.json "
        f"(sin docentes de dm/dc)."
    )

# ---------------------------------------------------------------------------
# Adjuntar aulas (de comisiones.json) a las comisiones armadas desde dm/dc,
# matcheando por (materia, dia, hora_inicio, hora_fin)
# ---------------------------------------------------------------------------
TIPO_ABBR = {
    "teorica": "t", "teorico": "t",
    "practica": "p",
    "practica con pc": "pc",
    "teorico-practica": "tp", "teorico practica": "tp",
    "seminario": "s",
}


def strip_accents(s):
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def tipo_key(s):
    if not s:
        return ""
    return strip_accents(str(s)).strip().lower()


aula_lookup = defaultdict(list)
for r in com_clean:
    key = (r["materia"], r.get("dia"), norm_hora(r.get("hora_inicio")), norm_hora(r.get("hora_fin")))
    aula_lookup[key].append({
        "turno_raw": r.get("turno"), "tipo_raw": r.get("tipo"),
        "aula": to_num(r.get("aula")), "pabellon": to_num(r.get("pabellon")),
        "used": False,
    })

for c in comisiones_out:
    if c["horarios"] and "aulas" in c["horarios"][0]:
        continue  # ya viene con aulas (grupo com_only)
    for h in c["horarios"]:
        key = (c["materia"], h.get("dia"), norm_hora(h.get("hora_inicio")), norm_hora(h.get("hora_fin")))
        candidates = aula_lookup.get(key)
        if not candidates:
            h["aulas"] = []
            warnings.append(
                f"Sin aula encontrada para materia '{c['materia']}' comision '{c['comision']}' "
                f"dia={h.get('dia')} {h.get('hora_inicio')}-{h.get('hora_fin')}"
            )
            continue

        wanted_tipo = tipo_key(h.get("tipo"))
        wanted_abbr = TIPO_ABBR.get(wanted_tipo)

        by_turno = [x for x in candidates if tipo_key(x["turno_raw"]) == wanted_tipo and wanted_tipo]
        by_tipo_abbr = [x for x in candidates if wanted_abbr and tipo_key(x["tipo_raw"]) == wanted_abbr]

        if by_turno:
            chosen = by_turno
        elif by_tipo_abbr:
            chosen = by_tipo_abbr
        elif len(candidates) >= 1:
            distinct_turnos = {tipo_key(x["turno_raw"]) for x in candidates}
            chosen = candidates
            if len(distinct_turnos) > 1:
                warnings.append(
                    f"Aula ambigua para materia '{c['materia']}' comision '{c['comision']}' "
                    f"tipo={h.get('tipo')!r} dia={h.get('dia')} {h.get('hora_inicio')}-{h.get('hora_fin')}: "
                    f"se asignaron TODAS las aulas de ese horario ({len(candidates)}) porque no se pudo "
                    f"distinguir por turno/tipo (revisar a mano)."
                )
        else:
            chosen = []

        seen = set()
        uniq = []
        for a in chosen:
            k = (a["aula"], a["pabellon"])
            if k not in seen:
                seen.add(k)
                uniq.append({"aula": a["aula"], "pabellon": a["pabellon"]})
            a["used"] = True
        h["aulas"] = uniq

for key, rows in aula_lookup.items():
    if key[0] not in dm_dc_ids:
        continue  # materia sin dm/dc: ya se resolvio 100% via el bloque com_only
    for r in rows:
        if not r["used"]:
            warnings.append(
                f"Aula sin comision asociada: materia={key[0]} dia={key[1]} {key[2]}-{key[3]} "
                f"turno/tipo original={r['turno_raw']!r}/{r['tipo_raw']!r} "
                f"aula={r['aula']} pabellon={r['pabellon']}"
            )

save("JSON/horarios_2026_1c.json", comisiones_out)

report["warnings"] = warnings
report["n_comisiones"] = len(comisiones_out)
save("JSON/reporte_merge_horarios.json", report)

print("OK")
print("comisiones generadas:", len(comisiones_out))
print("descartadas de comisiones.json:", len(report["dropped"]["comisiones.json"]))
print("descartadas de dm_2026_1c.json:", len(report["dropped"]["dm_2026_1c.json"]))
print("descartadas de materias_dc_2026_1c.json:", len(report["dropped"]["materias_dc_2026_1c.json"]))
print("avisos:", len(warnings))
