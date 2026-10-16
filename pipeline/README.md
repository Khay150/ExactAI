# eXACTai — pipeline de datos (scraping → auditoría → aprobación → base)

Corre los scripts que ya tenías, en orden, y carga la base **sin borrar lo anterior**.
n8n solo llama a un paso por nodo; todo se puede correr también a mano.

```
pip install -r pipeline/requirements.txt
python pipeline/pipeline.py todo                # hasta el preview (no toca la base)
python pipeline/pipeline.py cargar              # carga real, solo si el preview fue de los mismos datos
```

## Pasos

| Paso | Qué hace | Archivos |
|---|---|---|
| `descargar-csv` | Baja las 6 planillas (link `pubhtml` → CSV de esa hoja) y las valida | `datos_crudos/lunes.csv … sabado.csv` |
| `scrapear-horarios` | `scrapeo_horarios_DM.py` + `scrapeo_horarios_DC.py` | `work/<cuatri>/raw/` |
| `scrapear-eventos` | `scrapeoEvento.py` | `work/<cuatri>/raw/eventos.json` |
| `transformar` | `generar_json` → `combinar_horarios` → `generar_aulas` → `script_cuatrimestre` | `work/<cuatri>/build/JSON/` |
| `auditar` | `auditoria_final.py`; si hay problemas → exit 1 y no sigue | `build/auditoria.json` |
| `preview` | `cargar_datos.py --dry-run`: qué comisiones/eventos se agregan, cambian o se eliminan | no guarda nada |
| `cargar` | Carga real. Si salió bien, copia los JSON nuevos a `JSON/` (backup en `pipeline/backups/`) | base + `JSON/` |

* `transformar` siempre arranca de cero desde `raw/` (porque `combinar_horarios.py` reescribe sus entradas).
* `cargar` se niega si los datos no son exactamente los del último `preview` (y ese preview vale para una sola carga).
* `JSON/` y la base **no se tocan** hasta que `cargar` termina bien.

## Cada cuatrimestre

1. Cuando el Depto. de Computación publique su nota, agregá el link en `config.json` → `dc_urls` → `"2026_2c"` (cambia cada vez).
2. `config.json` → `cuatri` (o `--cuatri 2027_1c`).
3. Correr `todo`, mirar el resumen del preview, `cargar`.

## `cargar_datos.py` ahora es incremental

* Upsert de catálogos, materias, carreras y docentes (reutiliza los docentes que ya creó en cargas anteriores).
* Solo toca las **comisiones de los cuatrimestres que traiga `--horarios`**; las de otros cuatrimestres quedan intactas.
* Si el archivo no trae >30 % de las comisiones ya cargadas de ese cuatrimestre, aborta (posible scraping incompleto). `--forzar` lo permite.
* Eventos: reemplaza los de los cuatrimestres presentes en `eventos.json` (y los sin cuatrimestre); el resto queda.
* `--dry-run` simula todo y deshace. `--reset` vuelve al comportamiento viejo (vaciar y recargar).
* Ejemplo manual: `python "Modelo BD/cargar_datos.py" --horarios horarios_2026_2c.json`

Conexión: `EXACTAI_DSN=postgresql://usuario:clave@localhost:5432/postgres` (o `PG*`, o los `DB_*` de `cargar_datos.py`).

## n8n

Importar `pipeline/n8n/eXACTai_pipeline.workflow.json` (Workflows → Import from file) y:

1. Nodo **Parámetros**: revisar `pipeline` (ruta a `pipeline.py` y comando `python`) y poner tu `chat_id` de Telegram.
2. Elegir la credencial de Telegram en los 4 nodos de aviso (o cambiarlos por Gmail/Slack).
3. Los nodos **Execute Command** deben estar habilitados (en n8n 2.x vienen desactivados: variable `NODES_EXCLUDE=[]`).
4. Si n8n corre en Docker, necesita ver la carpeta del proyecto y tener Python con `pipeline/requirements.txt`.

Flujo: disparador (10/feb y 10/jul, o a mano) → 6 pasos → mensaje con el preview y links **Aprobar / Rechazar** → carga → aviso.
Cualquier fallo (incluida una auditoría con problemas) corta el flujo y avisa. Los links de aprobación apuntan al n8n
donde corre el workflow (si está en tu PC, hay que abrirlos desde esa red).

## Límites conocidos

* Los eventos ya salen listos de `scrapeoEvento.py` (eventos multicuatri separados y verano en vacaciones/intensivo, para cualquier año). `separar_eventos_multicuatri.py` y `corregir_verano.py` quedaron sin uso (se pueden borrar).
* `script_cuatrimestre.py` genera el catálogo final (verano intensivo/vacaciones incluido) para el año que le pases (`EXACTAI_ANIO`, el pipeline lo saca del cuatrimestre). Los eventos de un año que todavía no esté en el catálogo hacen fallar la auditoría.
* La URL de la nota del DC no se descubre sola.
* Las URLs CSV de las planillas se arman a partir de tus links `pubhtml`; la validación de columnas avisa si la planilla cambia de formato.
