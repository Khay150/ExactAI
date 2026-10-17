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
Reglas:
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

def buscar_materia(nombre_o_alias: str, id_cuatri: str | None = None) -> dict:
    """Busca una materia por nombre o alias (ej. "algebra 1", "analisis 2", "mate 1") y devuelve su carga
    horaria y las comisiones (id_comision, comision, turno) del cuatrimestre. Usala apenas el usuario
    nombra una materia. id_cuatri es opcional (ej. "2026_2c"); por defecto, el cuatrimestre vigente."""
    r = ha.buscar_materia(nombre_o_alias, id_cuatri=id_cuatri)
    for m in r.get("materias", []):           # campos que el modelo no necesita (y gastan tokens)
        m.pop("aliases_externos", None)
        m.pop("score", None)
    return r


def info_comision(id_comision: int | None = None, materia: str | None = None,
                  turno: str | None = None, id_cuatri: str | None = None) -> dict:
    """Horario completo (dia, hora, tipo de clase, aula) y docentes. Para "cuando/donde se cursa una materia"
    llamala UNA sola vez con `materia` (nombre): devuelve TODAS las comisiones de esa materia (agregá
    `turno` solo si el usuario lo pidio: "manana", "tarde", "noche"). Usa `id_comision` solo para una
    comision puntual que ya conoces. Informa todas las comisiones que devuelva, sin omitir ninguna.
    Si la materia es ambigua devuelve las opciones: preguntale al usuario."""
    return ha.info_comision(id_comision=id_comision, materia=materia, turno=turno, id_cuatri=id_cuatri)


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
    return ha.armar_horario(materias, id_cuatri=id_cuatri, preferencias=preferencias, max_resultados=max_resultados)


def correlativas_faltantes(materia: str, materias_aprobadas: list[str] | None = None,
                           id_carrera: str | None = None) -> dict:
    """Que correlativas le faltan al usuario para cursar `materia`, dadas las materias que ya aprobo
    (nombres). Las correlativas pueden variar segun la carrera. Si no sabes que aprobo, preguntaselo."""
    def resolver(nombre):
        cands = ha.resolver_materia(nombre)
        if not cands:
            return None, {"error": f"no encontre ninguna materia para '{nombre}'"}
        fuertes = [c for c in cands if c["score"] == cands[0]["score"]]
        if len(fuertes) > 1:
            return None, {"ambiguo": True, "materia_pedida": nombre, "opciones": fuertes}
        return fuertes[0]["id_materia"], None

    mid, err = resolver(materia)
    if err:
        return err
    aprobadas = []
    for n in materias_aprobadas or []:
        aid, err = resolver(n)
        if err:
            return err
        aprobadas.append(aid)
    return ha.correlativas_faltantes(mid, materias_aprobadas=aprobadas, id_carrera=id_carrera)


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


def consultar(bot, pregunta, historial=None, intentos=5):
    """bot.run_sync con reintentos cuando Groq limita la velocidad (error 429: tokens por minuto)."""
    import re
    import time
    for n in range(intentos):
        try:
            return bot.run_sync(pregunta, message_history=historial)
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
