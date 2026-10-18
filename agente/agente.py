#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agente de eXACTai (PydanticAI): arma el agente con las tools SQL exactas + la busqueda semantica (RAG).

Modelo (variable EXACTAI_LLM, formato "proveedor:modelo"):
    groq:openai/gpt-oss-120b     (nube; necesita GROQ_API_KEY)      <- default si hay GROQ_API_KEY
    ollama:llama3.1:8b               (local; necesita Ollama corriendo)  <- default si no hay key
Conexion a la base: EXACTAI_DSN (igual que el resto del proyecto; default de herramientas_agente).
"""
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "agente"))
sys.path.insert(0, str(RAIZ / "rag"))

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")   # sin el cartel de publicidad de pydantic-ai
from pydantic_ai import Agent  # noqa: E402

import buscar as _rag  # noqa: E402
import herramientas_agente as ha  # noqa: E402

SYSTEM_PROMPT = """\
Sos eXACTai, el asistente de estudiantes de la Facultad de Ciencias Exactas y Naturales (FCEN-UBA).
Respondés en español rioplatense, claro y breve, con datos concretos (día, hora, aula, docentes, fechas).
Te consultan sobre materias, comisiones, horarios, aulas, docentes, fechas importantes (inscripciones a
materias y a finales, publicación de materias, exámenes), correlativas y armado de horarios.

Tenés dos tipos de herramientas:
- Herramientas SQL (buscar_materia, info_comision, proximas_fechas, chequear_solapamiento, armar_horario,
  correlativas_faltantes): para datos EXACTOS (horarios, aulas, docentes de una comisión, fechas,
  solapamientos, correlativas). Siempre que la pregunta se pueda contestar con una de ellas, usala.
- buscar_info (búsqueda semántica): para preguntas abiertas, ambiguas o mal escritas, o cuando todavía no
  sabés de qué materia/comisión/docente habla el usuario. Después de identificarlo con buscar_info,
  confirmá los datos exactos con las herramientas SQL antes de responder horarios o aulas.
- Cuando la intención es clara, pasá `tipos` a buscar_info: horario/aula/quién da una materia -> ["comision"];
  fechas -> ["evento"]; mail o "quién es" -> ["docente"]; materias de un año de una carrera -> ["plan"].
- Para horarios/aulas de una materia, llamá info_comision(materia=...) una vez: trae todas sus comisiones. Listá
  TODAS las que devuelva (no elijas una ni omitas ninguna) salvo que el usuario pida un turno.
- Si el usuario nombra una materia concreta, resolvela con buscar_materia (SQL, exacta). buscar_info es
  para cuando todavía no sabés cuál es la materia o la pregunta es vaga.
- Antes de responder, fijate que el título del resultado corresponda a lo que se preguntó. Si no, decí que
  no hay datos (p. ej. si piden la inscripción a materias y solo aparecen inscripciones a finales).
  Para fechas usá además proximas_fechas.
- Eventos: "Inscripción al ... Llamado del Turno de ..." es la inscripción a EXÁMENES FINALES, no a materias.
  La inscripción a MATERIAS (cursadas) tiene "materias" o "cursada" en el título. Si piden la inscripción a
  materias y no hay un evento así, decí claramente que todavía no está publicada; no la reemplaces por la de
  finales. Si el usuario quiere saber cuándo se inscribe a finales, ahí sí usá esos eventos.
- armar_horario: las preferencias (evitar días, turno, horarios) PENALIZAN, no excluyen. Si la mejor opción
  tiene penalidad mayor a 0, mostrá igual esa opción y decí qué preferencia no se pudo cumplir y por qué
  (cada comisión trae su `horario`), en vez de decir que no hay solución.
- "¿Se pisan X e Y?" / "¿qué combinaciones sirven?": usá armar_horario([X, Y]), que devuelve las combinaciones
  de comisiones que NO se pisan, con su horario. chequear_solapamiento es para comisiones ya elegidas.
  Nunca afirmes que una combinación no se pisa, ni inventes horarios, sin verlo en los resultados.
- Correlativas ("qué necesito para cursar X", "qué materias pide X"): usá SIEMPRE correlativas_faltantes (es la
  fuente exacta, por carrera); si el usuario no dijo qué aprobó, pasá materias_aprobadas vacío y listá las
  correlativas que devuelve, por carrera. No las deduzcas de buscar_info ni de memoria.
- Si una herramienta no encuentra la materia y trae `sugerencias`, mostráselas al usuario y preguntá cuál quiso
  decir (puede haber un error de tipeo o otra forma de nombrarla). Con "estadística de datos" suele ser
  "Intr. a la Estadística y Ciencia de Datos". Nunca respondas "no existe" sin haber ofrecido las sugerencias.
- Respondé SOLO con materias, fechas, horarios y nombres que figuren en los resultados de las herramientas
  que llamaste en este turno. Si una herramienta devuelve un error, algo vacío o pide algo que falta, decilo
  o pedíselo al usuario; nunca completes la respuesta de memoria ni digas "según la fuente oficial" sin haberla
  consultado. Si no podés responder con certeza, preferí decir que no lo sabés.
- Estilo: español rioplatense correcto y breve. No agregues frases de relleno ni reglas que no estén en los
  resultados (por ejemplo, explicaciones sobre cómo se cursa o se aprueba). Sobre correlativas decí solo cuáles
  son y, si el usuario dijo qué aprobó, cuáles le faltan.
- Si una comisión tiene bloques sin aula (lista de aulas vacía), decí que el aula todavía no está asignada/publicada;
  no digas que no encontraste la materia.
- Si info_comision devuelve turnos_disponibles, el turno pedido no existe: mostrá los que sí hay.
Reglas:
- Nunca menciones nombres de herramientas, ni cómo funcionan, al usuario.
- Nunca inventes datos que no estén en los resultados de las herramientas. Si no encontrás algo, decilo.
- Si una materia es ambigua (varias materias con el mismo nombre), mostrá las opciones y preguntá cuál.
- Si faltan datos para una herramienta (p. ej. qué materias aprobó el usuario), preguntáselos.
- Las fechas de los eventos pueden estar sin definir ("a definir"): decilo tal cual.
- No des información que no tenga que ver con la facultad.
"""


# ---------------------------------------------------------------------------
# Tools: wrappers finos. Al modelo solo le mostramos los parametros utiles (nada de dsn/data_dir)
# y le pedimos nombres de materia en vez de ids internos cuando se puede.
# ---------------------------------------------------------------------------


def _sugerencias(nombre, k=4):
    """Cuando no hay coincidencia exacta de nombre (error de tipeo, otra forma de decirlo), busca en el RAG
    las materias mas parecidas para que el agente se las proponga al usuario."""
    try:
        r = _rag.buscar_info(nombre, tipos=["materia"], k=k)
    except Exception:
        return []
    return [{"id_materia": x["id_materia"], "titulo": x["titulo"].replace("Materia: ", ""),
             "tambien_conocida_como": _alias_de(x["contenido"])}
            for x in r["resultados"] if x.get("id_materia")]


def _alias_de(contenido):
    """La linea 'También se la conoce como: ...' del documento de la materia (si tiene), recortada."""
    for linea in contenido.splitlines():
        if linea.startswith("También se la conoce como"):
            return linea.split(":", 1)[1].strip()[:200]
    return None


def _con_sugerencias(resultado, nombre):
    """Si la herramienta no encontro la materia, agrega sugerencias del RAG (no las elige por el usuario)."""
    no_encontro = (resultado.get("encontrado") is False or
                   "no encontre ninguna materia" in str(resultado.get("error", "")))
    if no_encontro:
        sug = _sugerencias(nombre)
        if sug:
            resultado["sugerencias"] = sug
            resultado["nota"] = ("No hay una materia con ese nombre exacto. Estas son las mas parecidas: "
                                 "preguntale al usuario cual quiso decir (no elijas por tu cuenta).")
    return resultado


def buscar_materia(nombre_o_alias: str, id_cuatri: str | None = None) -> dict:
    """Busca una materia por nombre o alias (ej. "algebra 1", "analisis 2", "mate 1") y devuelve su carga
    horaria y las comisiones (id_comision, comision, turno) del cuatrimestre. Usala apenas el usuario
    nombra una materia. id_cuatri es opcional (ej. "2026_2c"); por defecto, el cuatrimestre vigente."""
    r = ha.buscar_materia(nombre_o_alias, id_cuatri=id_cuatri)
    for m in r.get("materias", []):           # campos que el modelo no necesita (y gastan tokens)
        m.pop("aliases_externos", None)
        m.pop("score", None)
    return _con_sugerencias(r, nombre_o_alias)


def info_comision(id_comision: int | None = None, materia: str | None = None,
                  turno: str | None = None, id_cuatri: str | None = None) -> dict:
    """Horario completo (dia, hora, tipo de clase, aula) y docentes. Para "cuando/donde se cursa una materia"
    llamala UNA sola vez con `materia` (nombre): devuelve TODAS las comisiones de esa materia (agregá
    `turno` solo si el usuario lo pidio: "manana", "tarde", "noche"). Usa `id_comision` solo para una
    comision puntual que ya conoces. Informa todas las comisiones que devuelva, sin omitir ninguna.
    Si la materia es ambigua devuelve las opciones: preguntale al usuario."""
    r = ha.info_comision(id_comision=id_comision, materia=materia, turno=turno, id_cuatri=id_cuatri)
    return _con_sugerencias(r, materia) if materia else r


TIPOS_EVENTO = ["Inscripción", "Exámenes", "Publicación", "Clases", "Receso", "Encuestas", "Semana de las Ciencias"]


def _tipo_evento(tipo):
    """Acepta 'inscripcion', 'finales', 'examen'... y lo lleva al valor exacto de la base (o None)."""
    if not tipo:
        return None
    t = ha.norm(tipo)
    if t.startswith(("final", "exam", "mesa", "llamado")):
        return "Exámenes"
    for v in TIPOS_EVENTO:
        if ha.norm(v).startswith(t[:6]) or t.startswith(ha.norm(v)[:6]):
            return v
    return None


def proximas_fechas(tipo: str | None = None, id_cuatri: str | None = None, desde: str | None = None,
                    incluir_pasadas: bool = False, limite: int = 20) -> dict:
    """Eventos del calendario academico ordenados por fecha: inscripciones (a materias o a finales),
    examenes, publicaciones, clases, receso. `tipo` es OPCIONAL y solo puede ser uno de:
    "Inscripción", "Exámenes", "Publicación", "Clases", "Receso", "Encuestas", "Semana de las Ciencias";
    si no estas seguro, no lo pases y filtra vos por el titulo (distingue inscripcion a MATERIAS de
    inscripcion a EXAMENES/finales). `desde` en formato AAAA-MM-DD (por defecto hoy)."""
    from datetime import date
    d = date.fromisoformat(desde) if desde else None
    r = ha.proximas_fechas(tipo=_tipo_evento(tipo), id_cuatri=id_cuatri, desde=d,
                           incluir_pasadas=incluir_pasadas, limite=limite)
    return r if isinstance(r, dict) else {"eventos": r}


def chequear_solapamiento(ids_comision: list[int]) -> dict:
    """Dadas varias comisiones (ids de buscar_materia) dice que bloques horarios se pisan entre si."""
    return ha.chequear_solapamiento(ids_comision)


def armar_horario(materias: list[str], id_cuatri: str | None = None, preferencias: dict | None = None,
                  max_resultados: int = 3) -> dict:
    """Arma combinaciones de comisiones (una por materia) sin solapamientos. `materias`: nombres.
    `preferencias` opcional: {"turno": "noche", "evitar_dias": ["viernes"], "no_antes_de": "10:00",
    "no_despues_de": "20:00"}. Las preferencias penalizan (no excluyen): devuelve las mejores opciones con
    su penalidad (0 = cumple todo) y el `horario` de cada comision."""
    r = ha.armar_horario(materias, id_cuatri=id_cuatri, preferencias=preferencias, max_resultados=max_resultados)
    if "no encontre ninguna materia" in str(r.get("error", "")):
        r = _con_sugerencias(r, str(r["error"]).split("'")[1] if "'" in str(r["error"]) else " ".join(materias))
    return r


def _como_lista(x):
    """El modelo a veces manda una lista como texto ('[]', '["Analisis I"]', 'Analisis I, Algebra I')."""
    import json
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return [str(i).strip() for i in x if str(i).strip()]
    t = str(x).strip()
    if not t or t in ("[]", "null", "None"):
        return []
    try:
        v = json.loads(t)
        if isinstance(v, list):
            return [str(i).strip() for i in v if str(i).strip()]
    except ValueError:
        pass
    return [p.strip() for p in t.replace(";", ",").split(",") if p.strip()]


def _id_carrera(texto):
    """'lcd', 'datos', 'Ciencias de Datos'... -> id de carrera ('lcd'); None si no hay una coincidencia clara."""
    import json
    if not texto:
        return None
    t = ha.norm(texto)
    try:
        carreras = json.load(open(ha.DATA_DIR / "carreras_materias.json", encoding="utf-8"))["carreras"]
    except Exception:
        return None
    for c in carreras:
        if t == c["id"]:
            return c["id"]
    hallo = [c["id"] for c in carreras if t and (t in ha.norm(c["nombre"]) or ha.norm(c["nombre"]) in t)]
    return hallo[0] if len(hallo) == 1 else None


def correlativas_faltantes(materia: str, materias_aprobadas: list[str] | str | None = None,
                           carrera: str | None = None) -> dict:
    """Que correlativas necesita `materia` y cuales le faltan al usuario, dadas las materias que ya aprobo
    (nombres; vacio si no dijo ninguna). `carrera` es opcional ("Ciencias de Datos", "lcd"...): solo
    pasala si el usuario la nombro; si no, devuelve el resultado de cada carrera, porque las correlativas
    pueden variar. Si la materia no se encuentra, trae `sugerencias`: mostraselas al usuario."""
    def resolver(nombre):
        cands = ha.resolver_materia(nombre)
        if not cands:
            return None, _con_sugerencias({"error": f"no encontre ninguna materia para '{nombre}'"}, nombre)
        fuertes = [c for c in cands if c["score"] == cands[0]["score"]]
        if len(fuertes) > 1:
            return None, {"ambiguo": True, "materia_pedida": nombre, "opciones": fuertes}
        return fuertes[0]["id_materia"], None

    mid, err = resolver(materia)
    if err:
        return err
    aprobadas = []
    for n in _como_lista(materias_aprobadas):
        aid, err = resolver(n)
        if err:
            return err
        aprobadas.append(aid)
    id_carrera = _id_carrera(carrera)
    r = ha.correlativas_faltantes(mid, materias_aprobadas=aprobadas, id_carrera=id_carrera)
    if id_carrera and not r.get("resultado"):          # la carrera pedida no tiene esa materia: mostrar todas
        r = ha.correlativas_faltantes(mid, materias_aprobadas=aprobadas)
        r["nota"] = "La carrera pedida no tiene correlativas registradas para esa materia; se muestran todas las carreras."
    return r


def buscar_info(consulta: str, tipos: list[str] | None = None, id_cuatri: str | None = None,
                id_materia: str | None = None, incluir_pasados: bool = False, k: int = 3) -> dict:
    """Busqueda semantica abierta sobre la informacion de la facultad. Usala para preguntas ambiguas o mal
    escritas, o cuando todavia no sabes de que materia/docente se habla ("quien da algebra a la noche",
    "cuando me anoto", "que materias hay en 2do anio de Datos", "mail de un profe").
    `tipos` acota: "comision", "materia", "evento", "docente", "carrera", "plan".
    NO la uses para horarios exactos de una materia conocida (eso es info_comision).
    Devuelve resultados con titulo, contenido y ids para seguir con las otras herramientas."""
    r = _rag.buscar_info(consulta, tipos=tipos, id_cuatri=id_cuatri, id_materia=id_materia,
                         incluir_pasados=incluir_pasados, k=k)
    for x in r["resultados"]:           # menos ruido y menos tokens para el modelo
        for campo in ("score", "distancia", "encontrado_por", "id_carrera", "id_docente"):
            x.pop(campo, None)
        if len(x["contenido"]) > MAX_CONTENIDO:
            x["contenido"] = x["contenido"][:MAX_CONTENIDO].rsplit("\n", 1)[0] + "\n[...]"
    return r


MAX_CONTENIDO = 1200  # caracteres por resultado de buscar_info (el plan gratis de Groq limita los tokens por minuto)

TOOLS = [buscar_materia, info_comision, proximas_fechas, chequear_solapamiento,
         armar_horario, correlativas_faltantes, buscar_info]


def construir_modelo(spec: str | None = None):
    """'groq:openai/gpt-oss-120b' o 'ollama:llama3.1:8b' (o cualquier string de PydanticAI)."""
    spec = spec or os.environ.get("EXACTAI_LLM") or (
        "groq:openai/gpt-oss-120b" if os.environ.get("GROQ_API_KEY") else "ollama:llama3.1:8b")
    proveedor, _, nombre = spec.partition(":")
    if proveedor == "ollama":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        base = os.environ.get("OLLAMA_URL", "http://localhost:11434") + "/v1"
        return OpenAIChatModel(nombre, provider=OpenAIProvider(base_url=base, api_key="ollama"))
    return spec


def crear_agente(modelo=None) -> Agent:
    return Agent(modelo or construir_modelo(), instructions=SYSTEM_PROMPT, tools=TOOLS, retries=2)


MAX_PASOS = 12        # pedidos al modelo por pregunta (cada tool call + su respuesta cuenta como uno)
TIMEOUT_LLM = 60      # segundos de espera por cada pedido a la API


def consultar(bot, pregunta, historial=None, intentos=5):
    """bot.run_sync con reintentos cuando Groq limita la velocidad (error 429: tokens por minuto)."""
    import re
    import time
    from pydantic_ai.usage import UsageLimits
    for n in range(intentos):
        try:
            return bot.run_sync(pregunta, message_history=historial,
                                usage_limits=UsageLimits(request_limit=MAX_PASOS),   # corta si el modelo da vueltas
                                model_settings={"timeout": TIMEOUT_LLM})          # y si Groq no contesta
        except Exception as e:
            msg = str(e)
            if "429" not in msg and "rate limit" not in msg.lower():
                raise
            m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([0-9.]+)s)?", msg)
            espera = 10
            if m and any(m.groups()):
                h, mi, se = (float(x) if x else 0 for x in m.groups())
                espera = h * 3600 + mi * 60 + se + 1
            if "per day" in msg or "(TPD)" in msg or espera > 90:
                raise RuntimeError(
                    f"Se acabó el cupo diario de tokens de Groq para este modelo (reintentar en ~{espera / 60:.0f} min). "
                    "Podés esperar, o probar otro modelo: --modelo groq:openai/gpt-oss-20b "
                    "(cada modelo tiene su propio cupo).") from e
            if n == intentos - 1:
                raise
            print(f"   (límite de uso de Groq; reintento en {espera:.0f}s)", flush=True)
            time.sleep(espera)


def compactar_historial(mensajes):
    """Para los turnos siguientes de una charla, conserva solo lo que dijo el usuario y la respuesta final del
    agente; descarta las llamadas a herramientas y sus resultados (son lo que mas tokens gasta y el plan
    gratis de Groq limita a 8000 tokens por pedido). Si el usuario necesita un dato de nuevo, el agente
    vuelve a consultarlo."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
    compacto = []
    for m in mensajes:
        if isinstance(m, ModelRequest):
            textos = [p.content for p in m.parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)]
            if textos:
                compacto.append(ModelRequest(parts=[UserPromptPart(content="\n".join(textos))]))
        elif isinstance(m, ModelResponse):
            textos = [p.content for p in m.parts if isinstance(p, TextPart) and p.content.strip()]
            if textos:
                compacto.append(ModelResponse(parts=[TextPart(content="\n".join(textos))]))
    return compacto[-12:]     # y solo los ultimos turnos
