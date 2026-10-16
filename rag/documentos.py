#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arma los documentos de texto en lenguaje natural que se indexan en el RAG, a partir de lo que
ya esta en Postgres (cargado por cargar_datos.py). No escribe nada en la base: solo lee.

Tipos de documento (uno por cada cosa):
    comision   una comision de una materia en un cuatrimestre: horarios, aulas, docentes
    materia    la materia: nombres/alias, carga horaria, carreras donde esta, correlativas, cuando se dicta
    evento     una fecha importante del calendario academico
    docente    un docente: departamento, mail y que dicta en cada cuatrimestre
    carrera    datos generales de la carrera
    plan       materias de un anio del plan de estudios de una carrera

El texto de cada documento es DETERMINISTA (no incluye "hoy", ni ids autoincrementales): si los
datos no cambiaron, el texto es identico y el hash no cambia, asi no se vuelve a calcular el embedding.

Los nombres de materia salen de carreras_materias.json (la tabla `materia` no tiene nombre), con el
mismo indice que usa herramientas_agente.py.
"""
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent / "agente"))
import herramientas_agente as ha  # noqa: E402  (resolver nombres de materia, cuatrimestre vigente)

TIPOS = ["comision", "materia", "evento", "docente", "carrera", "plan"]

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
ORDINAL = {"1": "1er", "2": "2do", "3": "3er", "4": "4to"}
ANIO_PALABRA = {1: "primer", 2: "segundo", 3: "tercer", 4: "cuarto", 5: "quinto", 6: "sexto", 7: "séptimo"}
ORDEN_DIAS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
ORDEN_ROLES = {"Prof.": 0, "JTP": 1, "Ay1": 2, "Ay2": 3}


# =============================================================================
# Formato de textos
# =============================================================================

def hash_de(titulo, contenido):
    return hashlib.sha256((titulo + "\n" + contenido).encode("utf-8")).hexdigest()


def _doc(id_doc, tipo, titulo, contenido, id_cuatri=None, id_materia=None, id_carrera=None,
         id_docente=None, metadata=None):
    return {"id_doc": id_doc, "tipo": tipo, "titulo": titulo, "contenido": contenido,
            "id_cuatri": id_cuatri, "id_materia": id_materia, "id_carrera": id_carrera,
            "id_docente": id_docente, "metadata": metadata or {},
            "hash_texto": hash_de(titulo, contenido)}


def nombre_cuatri(fila):
    """fila = (id_cuatri, anio, periodo, tipo, orden)"""
    id_cuatri, anio, periodo, tipo, _ = fila
    periodo = str(periodo)
    if tipo == "cuatrimestre":
        return f"{ORDINAL.get(periodo, periodo + 'º')} cuatrimestre de {anio}"
    if tipo == "bimestre":
        return f"{ORDINAL.get(periodo, periodo + 'º')} bimestre de {anio}"
    if tipo == "intensivo":
        if periodo == "verano":
            return f"curso intensivo de verano de {anio}"
        if periodo == "invierno":
            return f"curso de invierno de {anio}"
    if tipo == "vacaciones":
        return f"receso de verano de {anio}"
    return str(id_cuatri)


def _fecha(d):
    return f"{d.day} de {MESES[d.month - 1]}"


def texto_fecha(ini, fin):
    if ini is None and fin is None:
        return "todavía a definir (aún no está publicada la fecha)"
    if ini is None:
        return f"hasta el {_fecha(fin)} de {fin.year}"
    if fin is None or fin == ini:
        return f"el {_fecha(ini)} de {ini.year}"
    if ini.year == fin.year:
        return f"del {_fecha(ini)} al {_fecha(fin)} de {fin.year}"
    return f"del {_fecha(ini)} de {ini.year} al {_fecha(fin)} de {fin.year}"


def hhmm(t):
    return t.strftime("%H:%M")


def franja(t):
    h = t.hour
    return "mañana" if h < 13 else ("tarde" if h < 17 else "noche")


_ROMANOS = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6", "VII": "7", "VIII": "8"}
_ARABES = {v: k for k, v in _ROMANOS.items()}


def variante_numeral(nombre):
    """'Análisis II' -> 'Análisis 2' y 'Matemática 1' -> 'Matemática I' (None si no termina en numeral)."""
    partes = nombre.strip().rsplit(" ", 1)
    if len(partes) == 2:
        base, ultimo = partes
        if ultimo in _ROMANOS:
            return f"{base} {_ROMANOS[ultimo]}"
        if ultimo in _ARABES:
            return f"{base} {_ARABES[ultimo]}"
    return None


def nombre_docente(nombre, apellido):
    ap = (apellido or "").strip()
    if ap.isupper():
        ap = ap.title()
    return f"{(nombre or '').strip()} {ap}".strip()


def texto_aula(numero, pabellon):
    n = str(numero).strip()
    if n.isdigit():
        return f"aula {n} del Pabellón {pabellon}"
    if n.lower().startswith("aula "):
        n = "aula " + n[5:]
    return f"{n} del Pabellón {pabellon}"


def unir(items, conj="y"):
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {conj} " + items[-1]


def ubicacion_plan(anio, cuat):
    if anio is None:
        return "sin año asignado (optativa o a elegir)"
    s = f"{anio}º año"
    if cuat:
        s += f", {ORDINAL.get(str(cuat), str(cuat) + 'º')} cuatrimestre"
    return s


# =============================================================================
# Contexto: catalogos chicos cargados una sola vez
# =============================================================================

class Contexto:
    def __init__(self, cur, data_dir=None):
        self.cur = cur
        self.info, _ = ha._indice_materias(data_dir)
        self._canonicos = {}
        cur.execute("SELECT id_cuatri, anio, periodo, tipo, orden FROM cuatrimestre")
        self.cuatris = {r[0]: r for r in cur.fetchall()}
        self.vigente = ha.cuatrimestre_vigente(cur)
        cur.execute("SELECT id_dia, nombre FROM dia")
        self.dias = dict(cur.fetchall())
        cur.execute("SELECT id_aula, numero, pabellon FROM aula")
        self.aulas = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        cur.execute("SELECT id_departamento, nombre FROM departamento")
        self.deptos = dict(cur.fetchall())
        cur.execute("""SELECT id, nombre, duracion, titulo, cant_materias_obligatorias,
                              modalidad_optativas, cant_materias_optativas FROM carrera ORDER BY id""")
        self.carreras = {r[0]: r for r in cur.fetchall()}
        cur.execute("SELECT id_docente, nombre, apellido, mail FROM docente ORDER BY id_docente")
        self.docentes = {r[0]: r for r in cur.fetchall()}

    def cuatri(self, id_cuatri):
        fila = self.cuatris.get(id_cuatri)
        return nombre_cuatri(fila) if fila else (id_cuatri or "sin cuatrimestre")

    def orden_cuatri(self, id_cuatri):
        fila = self.cuatris.get(id_cuatri)
        return fila[4] if fila else 0

    def materia(self, id_materia):
        """Nombre "canonico" para mostrar: el que mas carreras usan (a igualdad, el mas corto).
        `nombre_principal` de herramientas_agente es el mas largo, que a veces es un nombre de
        cartelera en mayusculas con varias variantes pegadas."""
        if id_materia not in self._canonicos:
            d = self.info.get(id_materia)
            if not d:
                self._canonicos[id_materia] = id_materia
            else:
                usados = defaultdict(int)
                for n in d["carreras"].values():
                    usados[n] += 1
                if usados:
                    self._canonicos[id_materia] = sorted(usados, key=lambda n: (-usados[n], len(n), n))[0]
                else:
                    self._canonicos[id_materia] = d["nombre_principal"]
        return self._canonicos[id_materia]

    def materia_en_carrera(self, id_materia, id_carrera):
        d = self.info.get(id_materia)
        return d["carreras"].get(id_carrera, self.materia(id_materia)) if d else id_materia

    def alias(self, id_materia, maximo=8):
        """Otros nombres con los que se conoce la materia (sin repetir el canonico, sin importar
        mayusculas ni tildes)."""
        d = self.info.get(id_materia)
        if not d:
            return []
        canonico = self.materia(id_materia)
        # nombres propios + la variante con numeral cambiado ("Análisis II" <-> "Análisis 2"),
        # porque la gente escribe cualquiera de las dos y buscar "analisis 2" tiene que encontrar II.
        propios = sorted(d["nombres"], key=lambda n: (len(n), n))
        variantes = [v for v in (variante_numeral(n) for n in [canonico] + propios) if v]
        visto = {ha.norm(canonico)}
        salida = []
        for n in variantes[:1] + propios + variantes[1:]:   # primero la variante del canonico
            k = ha.norm(n)
            if n and k and k not in visto:
                visto.add(k)
                salida.append(n)
        return salida[:maximo]

    def carrera(self, id_carrera):
        c = self.carreras.get(id_carrera)
        return c[1] if c else id_carrera

    def docente(self, id_docente):
        d = self.docentes.get(id_docente)
        return nombre_docente(d[1], d[2]) if d else id_docente


# =============================================================================
# Comisiones
# =============================================================================

def gen_comisiones(c):
    cur = c.cur
    cur.execute("""SELECT id_comision, id_materia, id_cuatri, comision, turno
                   FROM comision ORDER BY id_cuatri, id_materia, comision""")
    comisiones = cur.fetchall()

    cur.execute("""SELECT id_comision, id_bloque, id_dia, hora_inicio, hora_fin, tipo
                   FROM bloque_horario ORDER BY id_comision, id_bloque""")
    bloques = defaultdict(list)
    for r in cur.fetchall():
        bloques[r[0]].append(r)
    cur.execute("SELECT id_bloque, id_aula FROM bloque_horario_aula ORDER BY id_bloque, id_aula")
    aulas_de = defaultdict(list)
    for id_bloque, id_aula in cur.fetchall():
        aulas_de[id_bloque].append(id_aula)
    cur.execute("""SELECT cd.id_comision, cd.rol, cd.id_docente
                   FROM comision_docente cd ORDER BY cd.id_comision""")
    docs_de = defaultdict(list)
    for id_comision, rol, id_docente in cur.fetchall():
        docs_de[id_comision].append((rol, id_docente))

    salida = []
    for id_comision, id_materia, id_cuatri, comision, turno in comisiones:
        nom = c.materia(id_materia)
        cuatri = c.cuatri(id_cuatri)
        alias = c.alias(id_materia)
        bl = sorted(bloques.get(id_comision, []),
                    key=lambda b: (ORDEN_DIAS.get(b[2], 9), b[3], b[1]))

        lineas = [f"Comisión «{comision}» de la materia {nom}, en el {cuatri}."]
        if alias:
            lineas.append(f"La materia también se conoce como: {', '.join(alias)}.")
        if turno:
            lineas.append(f"Turno según la cartelera: {turno}.")
        if bl:
            franjas = []
            for b in bl:
                f = franja(b[3])
                if f not in franjas:
                    franjas.append(f)
            lineas.append(f"Se cursa a la {unir(franjas, 'y')}." if len(franjas) == 1
                          else f"Se cursa en las franjas: {unir(franjas, 'y')}.")
            lineas.append("Horarios:")
            for b in bl:
                _, id_bloque, id_dia, h_ini, h_fin, tipo = b
                dia = c.dias.get(id_dia, id_dia)
                aulas = [texto_aula(*c.aulas[a]) for a in aulas_de.get(id_bloque, []) if a in c.aulas]
                donde = f"en {unir(aulas, 'y')}" if aulas else "aula todavía sin asignar"
                tipo_txt = f" ({tipo.strip()})" if tipo and tipo.strip() else ""
                lineas.append(f"- {dia} de {hhmm(h_ini)} a {hhmm(h_fin)}{tipo_txt}, {donde}.")
        else:
            lineas.append("Todavía no tiene horarios cargados.")

        docentes = sorted(docs_de.get(id_comision, []),
                          key=lambda x: (ORDEN_ROLES.get(x[0], 9), c.docente(x[1])))
        if docentes:
            nombres = []
            for rol, id_docente in docentes:
                nombre = c.docente(id_docente)
                nombres.append(nombre if rol in (None, "", "Sin rol") else f"{rol} {nombre}")
            lineas.append(f"Docentes: {'; '.join(nombres)}.")
        else:
            lineas.append("Docentes: todavía sin asignar.")

        titulo = f"{nom} — comisión {comision} ({cuatri})"
        salida.append(_doc(
            f"comision:{id_materia}:{comision}:{id_cuatri}", "comision", titulo, "\n".join(lineas),
            id_cuatri=id_cuatri, id_materia=id_materia,
            metadata={"id_comision": id_comision, "comision": comision, "turno": turno,
                      "docentes": sorted({d for _, d in docentes})}))
    return salida


# =============================================================================
# Materias
# =============================================================================

def gen_materias(c):
    cur = c.cur
    cur.execute("SELECT id, carga_horaria FROM materia ORDER BY id")
    materias = cur.fetchall()

    cur.execute("""SELECT id_materia, id_carrera, tipo_materia, anio, cuatrimestre
                   FROM carrera_materia WHERE id_materia IS NOT NULL""")
    en_carreras = defaultdict(list)
    for id_materia, id_carrera, tipo_materia, anio, cuat in cur.fetchall():
        en_carreras[id_materia].append((id_carrera, tipo_materia, anio, cuat))

    cur.execute("""SELECT gm.id_materia, g.nombre, g.cantidad_a_elegir, cm.id_carrera
                   FROM grupo_materia gm JOIN grupo_optativo g USING (id_grupo)
                   LEFT JOIN carrera_materia cm ON cm.id_grupo = g.id_grupo""")
    en_grupos = defaultdict(list)
    for id_materia, nombre, cant, id_carrera in cur.fetchall():
        en_grupos[id_materia].append((nombre, cant, id_carrera))

    cur.execute("SELECT id_materia, id_carrera, id_materia_correlativa FROM correlativa")
    correlativas = defaultdict(lambda: defaultdict(set))
    for id_materia, id_carrera, corr in cur.fetchall():
        correlativas[id_materia][id_carrera].add(corr)

    cur.execute("SELECT id_materia, id_cuatri, count(*) FROM comision GROUP BY 1, 2")
    dictado = defaultdict(list)
    for id_materia, id_cuatri, n in cur.fetchall():
        dictado[id_materia].append((id_cuatri, n))

    profes = defaultdict(set)
    if c.vigente:
        cur.execute("""SELECT DISTINCT co.id_materia, cd.id_docente
                       FROM comision co JOIN comision_docente cd USING (id_comision)
                       WHERE co.id_cuatri = %s AND cd.rol = 'Prof.'""", (c.vigente,))
        for id_materia, id_docente in cur.fetchall():
            profes[id_materia].add(id_docente)

    salida = []
    for id_materia, carga in materias:
        nom = c.materia(id_materia)
        alias = c.alias(id_materia)
        lineas = [f"{nom} (id interno: {id_materia})."]
        if alias:
            lineas.append(f"También se la conoce como: {', '.join(alias)}.")
        if carga:
            lineas.append(f"Carga horaria: {carga} horas.")

        carr = sorted(en_carreras.get(id_materia, []), key=lambda x: c.carrera(x[0]))
        if carr:
            lineas.append("Lugar en el plan de estudios de cada carrera:")
            for id_carrera, tipo_materia, anio, cuat in carr:
                en_esa = c.materia_en_carrera(id_materia, id_carrera)
                como = f" (en esa carrera se llama «{en_esa}»)" if en_esa != nom else ""
                tm = f", {tipo_materia}" if tipo_materia else ""
                lineas.append(f"- {c.carrera(id_carrera)}: {ubicacion_plan(anio, cuat)}{tm}{como}.")
        for nombre_g, cant, id_carrera in sorted(en_grupos.get(id_materia, []), key=lambda x: (x[0], x[2] or "")):
            donde = f" en {c.carrera(id_carrera)}" if id_carrera else ""
            eleg = f" (hay que elegir {cant})" if cant else ""
            lineas.append(f"Es una de las opciones del grupo «{nombre_g}»{eleg}{donde}.")

        corr = correlativas.get(id_materia)
        if corr:
            lineas.append("Correlativas (materias que hay que tener aprobadas antes de cursarla):")
            por_lista = defaultdict(list)
            for id_carrera, ids in corr.items():
                nombres = tuple(sorted(c.materia_en_carrera(m, id_carrera) for m in ids))
                por_lista[nombres].append(c.carrera(id_carrera))
            for nombres, carreras in sorted(por_lista.items(), key=lambda kv: kv[1]):
                lineas.append(f"- {unir(list(nombres), 'y')} (en {unir(sorted(carreras), 'y')}).")
        else:
            lineas.append("No figuran correlativas registradas.")

        dic = sorted(dictado.get(id_materia, []), key=lambda x: c.orden_cuatri(x[0]))
        if dic:
            partes = [f"{c.cuatri(i)} ({n} comisión)" if n == 1 else f"{c.cuatri(i)} ({n} comisiones)"
                      for i, n in dic]
            lineas.append(f"Se dicta en: {'; '.join(partes)}.")
        else:
            lineas.append("No tiene comisiones cargadas en ningún cuatrimestre.")
        if profes.get(id_materia):
            nombres = sorted(c.docente(d) for d in profes[id_materia])
            lineas.append(f"Profesores/as en el {c.cuatri(c.vigente)}: {', '.join(nombres)}.")

        salida.append(_doc(
            f"materia:{id_materia}", "materia", f"Materia: {nom}", "\n".join(lineas),
            id_materia=id_materia,
            metadata={"carreras": sorted({x[0] for x in carr}),
                      "cuatris": [i for i, _ in dic]}))
    return salida


# =============================================================================
# Eventos
# =============================================================================

def gen_eventos(c):
    cur = c.cur
    cur.execute("""SELECT titulo, descripcion, fecha_ini, fecha_fin, tipo, id_cuatri
                   FROM evento ORDER BY fecha_ini NULLS LAST, titulo, id_cuatri""")
    salida, vistos = [], defaultdict(int)
    for titulo, desc, ini, fin, tipo, id_cuatri in cur.fetchall():
        lineas = [f"{titulo.strip()}."]
        if tipo:
            lineas.append(f"Tipo de evento: {tipo}.")
        if id_cuatri:
            lineas.append(f"Corresponde al período: {c.cuatri(id_cuatri)}.")
        lineas.append(f"Fecha: {texto_fecha(ini, fin)}.")
        if desc and desc.strip():
            lineas.append(desc.strip())
        base = "evento:{}:{}:{}".format(
            id_cuatri or "-", ini.isoformat() if ini else "sin-fecha",
            hashlib.sha1(titulo.strip().encode("utf-8")).hexdigest()[:8])
        vistos[base] += 1
        id_doc = base if vistos[base] == 1 else f"{base}#{vistos[base]}"
        salida.append(_doc(
            id_doc, "evento", titulo.strip(), "\n".join(lineas), id_cuatri=id_cuatri,
            metadata={"fecha_ini": ini.isoformat() if ini else None,
                      "fecha_fin": fin.isoformat() if fin else None, "tipo_evento": tipo}))
    return salida


# =============================================================================
# Docentes
# =============================================================================

def gen_docentes(c):
    cur = c.cur
    cur.execute("SELECT id_docente, id_departamento FROM docente_departamento ORDER BY 1, 2")
    deptos = defaultdict(list)
    for id_docente, id_depto in cur.fetchall():
        deptos[id_docente].append(c.deptos.get(id_depto, id_depto))

    cur.execute("""SELECT cd.id_docente, co.id_cuatri, co.id_materia, co.comision, cd.rol
                   FROM comision_docente cd JOIN comision co USING (id_comision)
                   ORDER BY cd.id_docente, co.id_cuatri, co.id_materia, co.comision""")
    dicta = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for id_docente, id_cuatri, id_materia, comision, rol in cur.fetchall():
        dicta[id_docente][id_cuatri][id_materia].append((comision, rol))

    salida = []
    for id_docente, (_, nombre, apellido, mail) in c.docentes.items():
        nom = nombre_docente(nombre, apellido)
        lineas = [f"{nom}, docente de la Facultad."]
        if deptos.get(id_docente):
            lineas.append(f"Departamento: {unir(deptos[id_docente], 'y')}.")
        if mail:
            lineas.append(f"Correo electrónico: {mail}.")
        materias_ids = set()
        por_cuatri = dicta.get(id_docente, {})
        if por_cuatri:
            for id_cuatri in sorted(por_cuatri, key=c.orden_cuatri):
                partes = []
                for id_materia, lista in por_cuatri[id_cuatri].items():
                    materias_ids.add(id_materia)
                    roles = sorted({r for _, r in lista if r and r != "Sin rol"},
                                   key=lambda r: ORDEN_ROLES.get(r, 9))
                    rol_txt = f", como {unir(roles, 'y')}" if roles else ""
                    coms = ", ".join(com for com, _ in lista)
                    partes.append(f"{c.materia(id_materia)}{rol_txt} (comisión {coms})")
                lineas.append(f"En el {c.cuatri(id_cuatri)} da: {'; '.join(partes)}.")
        else:
            lineas.append("No figura dando clases en ninguna comisión cargada.")
        salida.append(_doc(
            f"docente:{id_docente}", "docente", f"Docente: {nom}", "\n".join(lineas),
            id_docente=id_docente,
            metadata={"materias": sorted(materias_ids), "cuatris": sorted(por_cuatri),
                      "departamentos": deptos.get(id_docente, [])}))
    return salida


# =============================================================================
# Carreras y plan de estudios
# =============================================================================

def gen_carreras(c):
    salida = []
    for id_carrera, (_, nombre, dur, titulo_ob, n_ob, modalidad, n_opt) in c.carreras.items():
        lineas = [f"{nombre} (id interno: {id_carrera})."]
        if titulo_ob:
            lineas.append(f"Título que otorga: {titulo_ob}.")
        if dur is not None:
            lineas.append(f"Duración estimada: {dur} años.")
        if n_ob is not None:
            lineas.append(f"Cantidad de materias obligatorias: {n_ob}.")
        if n_opt:
            unidad = f" {modalidad}" if modalidad else ""
            lineas.append(f"Materias optativas: hay que juntar {n_opt}{unidad} en total.")
        salida.append(_doc(f"carrera:{id_carrera}", "carrera", f"Carrera: {nombre}",
                           "\n".join(lineas), id_carrera=id_carrera))
    return salida


def gen_planes(c):
    cur = c.cur
    cur.execute("""SELECT cm.id_carrera, cm.id_materia, cm.id_grupo, cm.tipo, cm.tipo_materia,
                          cm.anio, cm.cuatrimestre, g.nombre, g.cantidad_a_elegir
                   FROM carrera_materia cm LEFT JOIN grupo_optativo g ON g.id_grupo = cm.id_grupo
                   ORDER BY cm.id_carrera, cm.anio NULLS LAST, cm.cuatrimestre NULLS LAST, cm.id_carrera_materia""")
    filas = defaultdict(lambda: defaultdict(list))
    for fila in cur.fetchall():
        filas[fila[0]][fila[5]].append(fila)

    cur.execute("SELECT id_grupo, id_materia FROM grupo_materia ORDER BY 1, 2")
    opciones = defaultdict(list)
    for id_grupo, id_materia in cur.fetchall():
        opciones[id_grupo].append(id_materia)

    salida = []
    for id_carrera, por_anio in filas.items():
        nombre_car = c.carrera(id_carrera)
        for anio, items in por_anio.items():
            clave = anio if anio is not None else 0
            if anio is None:
                etiqueta = "materias sin año asignado"
            else:   # "2º año (segundo año)": la gente escribe cualquiera de las dos formas
                etiqueta = f"{anio}º año ({ANIO_PALABRA[anio]} año)" if anio in ANIO_PALABRA else f"{anio}º año"
            lineas = [f"Plan de estudios de {nombre_car}: {etiqueta}."]
            por_cuat = defaultdict(list)
            for it in items:
                por_cuat[it[6]].append(it)
            for cuat in sorted(por_cuat, key=lambda x: (x is None, x)):
                if cuat:
                    lineas.append(f"{ORDINAL.get(str(cuat), str(cuat) + 'º')} cuatrimestre:")
                for (_, id_materia, id_grupo, tipo, tipo_materia, _a, _c, g_nombre, g_cant) in por_cuat[cuat]:
                    if id_materia:
                        extra = []
                        if tipo and tipo != "materia":
                            extra.append(tipo)
                        if tipo_materia:
                            extra.append(tipo_materia)
                        suf = f" ({', '.join(extra)})" if extra else ""
                        lineas.append(f"- {c.materia_en_carrera(id_materia, id_carrera)}{suf}")
                    else:
                        opts = [c.materia_en_carrera(m, id_carrera) for m in opciones.get(id_grupo, [])]
                        eleg = f"elegir {g_cant} de: " if g_cant else "opciones: "
                        lineas.append(f"- {g_nombre} ({eleg}{', '.join(opts)})")
            salida.append(_doc(
                f"plan:{id_carrera}:{clave}", "plan", f"Plan de estudios de {nombre_car} — {etiqueta}",
                "\n".join(lineas), id_carrera=id_carrera,
                metadata={"anio": anio,
                          "materias": sorted({i[1] for i in items if i[1]})}))
    return salida


GENERADORES = {
    "comision": gen_comisiones, "materia": gen_materias, "evento": gen_eventos,
    "docente": gen_docentes, "carrera": gen_carreras, "plan": gen_planes,
}


def generar_documentos(cur, tipos=None, data_dir=None):
    """Lista de documentos (dicts) para los `tipos` pedidos (default: todos)."""
    desconocidos = set(tipos or []) - set(TIPOS)
    if desconocidos:
        raise ValueError(f"Tipos desconocidos: {sorted(desconocidos)}. Validos: {TIPOS}")
    c = Contexto(cur, data_dir)
    docs = []
    for tipo in TIPOS:
        if tipos is None or tipo in tipos:
            docs.extend(GENERADORES[tipo](c))
    ids = [d["id_doc"] for d in docs]
    if len(ids) != len(set(ids)):
        repetidos = sorted({i for i in ids if ids.count(i) > 1})[:5]
        raise RuntimeError(f"Hay id_doc repetidos (ej. {repetidos}); revisar las claves naturales")
    return docs
