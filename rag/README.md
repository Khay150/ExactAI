# eXACTai — RAG (búsqueda semántica en pgvector)

Para preguntas abiertas o mal formuladas ("¿quién da álgebra a la noche?", "¿cuándo me anoto?", "mate 1").
Los datos exactos (horarios, aulas, solapamientos, correlativas) siguen yendo por las tools SQL de
`agente/herramientas_agente.py`.

```
Postgres (comision, materia, evento, docente, carrera...)
        │  documentos.py   → un texto en lenguaje natural por comisión / materia / evento / docente / carrera / plan
        ▼
  indexar.py  → hash del texto → solo se re-embebe lo que cambió  → tabla rag_documento (pgvector)
        ▼
  buscar.py   → buscar_info(): embedding de la pregunta + búsqueda por palabras, fusionadas (RRF)
```

| Archivo | Qué hace |
|---|---|
| `documentos.py` | Arma los textos desde la base (solo lee). Texto determinista: sin "hoy", sin ids autoincrementales. |
| `embeddings.py` | Modelo de embeddings (fastembed, local). Se cambia con `EXACTAI_EMB_MODEL`. |
| `indexar.py` | Sincroniza el índice. Incremental. `--dry-run`, `--exportar`, `--recrear`. |
| `buscar.py` | La tool `buscar_info` para el agente. También sirve por consola. |
| `probar_rag.py` | Prueba de punta a punta para revisar a ojo. |
| `schema_rag.sql` | Tabla `rag_documento` (lo aplica `indexar.py` solo). |

## Puesta en marcha (una sola vez)

### 1. Postgres con pgvector

La imagen oficial de Postgres no trae pgvector. Hay que pasar a `pgvector/pgvector:pg16` (misma versión mayor que la tuya, 16).
Como la imagen nueva está sobre otra versión de Debian, la forma segura es **backup + restore** en un contenedor nuevo
(así los índices de texto se reconstruyen con la librería del sistema nueva) y dejar el viejo guardado por si algo sale mal.
Antes mirá con qué opciones creaste el actual: `docker inspect mi-postgres` (volumen, `restart`, usuario/clave).

```bat
docker exec mi-postgres pg_dump -U postgres -d postgres -Fc -f /tmp/exactai.dump
docker cp mi-postgres:/tmp/exactai.dump D:\FRAN\backup_exactai.dump

docker stop mi-postgres
docker rename mi-postgres mi-postgres-viejo

docker run -d --name mi-postgres --restart unless-stopped -e POSTGRES_PASSWORD=admin -p 5432:5432 -v exactai_pgdata:/var/lib/postgresql/data pgvector/pgvector:pg16

docker cp D:\FRAN\backup_exactai.dump mi-postgres:/tmp/exactai.dump
docker exec mi-postgres pg_restore -U postgres -d postgres --no-owner /tmp/exactai.dump
```

Comprobá que quedó igual (por ejemplo `select count(*) from comision`, `docente`, `evento`) y recién ahí borrá `mi-postgres-viejo`.

### 2. Runner de Docker con fastembed

`pipeline/docker/Dockerfile.runner` ya instala `fastembed`. Reconstruir:

```bat
cd /d D:\FRAN\proyectos\ExactAI\pipeline\docker
docker compose up -d --build
```

### 3. Primer indexado (a mano, porque baja el modelo)

```bat
docker exec exactai-runner python /data/exactai/rag/indexar.py
```

La primera vez descarga el modelo (~0,5 GB) a `rag/modelos/` (queda en tu disco; no se vuelve a bajar) y calcula
~1400 embeddings. Las siguientes corridas solo calculan lo que cambió. Para ver antes qué haría: `--dry-run`.
Si querés leer los textos que se indexan: `--exportar rag/export` (un `.md` por tipo).

### 4. Probar

Con Python local (`pip install -r rag/requirements.txt`, Postgres en `localhost:5432`):

```bat
python rag\probar_rag.py
```

Corre el índice, verifica que una segunda corrida no recalcula nada, y hace ~11 preguntas de ejemplo mostrando
los 3 mejores resultados de cada una. Revisalas a ojo.

### 5. n8n: reindexar después de cada carga

En el workflow `ExactAI - pipeline`: duplicá el nodo **Cargar a la base** (Ctrl+C / Ctrl+V), renombralo
`Indexar RAG`, cambiá el body a `{"step":"indexar-rag","cuatri":"{{ $('Parámetros').first().json.cuatri }}"}`
y conectalo a continuación de **Cargar a la base** (rama "Aprobar"). Si falla, el workflow `avisar error`
te manda el aviso, y la base ya quedó cargada igual.

## Cómo decide el agente qué herramienta usar

La descripción de `buscar_info` ya le dice cuándo usarla. Conviene reforzarlo en el system prompt:

```
Tenés dos tipos de herramientas:
- Herramientas SQL (buscar_materia, info_comision, proximas_fechas, chequear_solapamiento, armar_horario,
  correlativas_faltantes): para datos EXACTOS (horarios, aulas, docentes de una comisión, fechas, solapamientos,
  correlativas). Siempre que la pregunta se pueda contestar con una de ellas, usala.
- buscar_info (búsqueda semántica): para preguntas abiertas, ambiguas o mal escritas, o cuando todavía no sabés
  de qué materia/comisión/docente habla el usuario. Después de identificarlo con buscar_info, confirmá los datos
  exactos con las herramientas SQL antes de responder horarios o aulas.
- Cuando la intención es clara, pasá `tipos` a buscar_info: horario/aula/quién da una materia → ["comision"];
  fechas → ["evento"]; mail o "quién es" → ["docente"]; materias de un año de una carrera → ["plan"].
- Si el usuario nombra una materia concreta, resolvela con buscar_materia (SQL, exacta). buscar_info es para
  cuando todavía no sabés cuál es la materia o la pregunta es vaga.
- Antes de responder, fijate que el título del resultado corresponda a lo que se preguntó. Si no, decí que no
  hay datos (p. ej. si piden la inscripción a materias y solo aparecen inscripciones a finales). Para fechas
  usá además proximas_fechas.
Nunca inventes datos que no estén en los resultados. Si no encontrás nada, decilo.
```

Registro (PydanticAI), mismo estilo que las otras tools:

```python
import sys
sys.path.insert(0, "rag")
from buscar import buscar_info
# agent = Agent(..., tools=[buscar_materia, info_comision, ..., buscar_info])
```

## Calidad de la búsqueda (primera prueba con el modelo liviano, 15/10/2026)

Con `potion-multilingual-128M` anduvo bien en nombres de docentes ("mail de merzbacher"), exámenes e inscripciones a finales
y planes de estudio, y regular en preguntas con muchas palabras de relleno ("cuántas horas tiene la materia de química general"
no puso "Química General" entre los 3 primeros: el modelo liviano la confundió con "Química de Materiales"). Por eso las
consultas con un nombre concreto van por SQL (`buscar_materia`) y el RAG queda para lo vago.

* `distancia_max` **no** sirve como filtro con este modelo: las coincidencias buenas dieron distancias de 0,43 a 0,87 y las malas
  de 0,83 a 0,92, así que se solapan. Dejalo en `None`.
* Si querés más precisión, probá un modelo más grande (necesita más RAM) y compará con `probar_rag.py`:
  `set EXACTAI_EMB_MODEL=intfloat/multilingual-e5-large` y después `python rag\indexar.py --recrear`
  (o `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`, ~1 GB, corta los textos a 128 tokens).
  Para volver al liviano: `set EXACTAI_EMB_MODEL=` y otro `--recrear`.

## Notas

* **Cambiar de modelo:** `EXACTAI_EMB_MODEL=intfloat/multilingual-e5-large` (mejor calidad, ~2 GB de RAM). Como cambia la
  dimensión, correr una vez `python rag/indexar.py --recrear`.
* **Salvaguarda:** si el índice perdería más del 30 % de los documentos de un tipo (p. ej. un scraping que trajo la mitad
  de las comisiones), aborta. `--forzar` lo permite.
* **Eventos pasados:** por defecto `buscar_info` no devuelve eventos que ya terminaron (`incluir_pasados=True` los incluye).
  Las comisiones salen del cuatrimestre vigente (el de mayor `orden` con comisiones cargadas).
* **Para sumar texto libre más adelante** (reglamentos, comunicados): es un generador más en `documentos.py` (un tipo nuevo
  que lea archivos de una carpeta, partidos por artículo); el resto (hash, indexado, búsqueda) no cambia.
* `rag/modelos/` y `rag/export/` no deberían ir a git (ya están en `.gitignore`).
