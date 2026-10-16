import json
import os
import sys
from pathlib import Path

import pandas as pd

# Los CSV (lunes.csv ... sabado.csv) los baja `pipeline.py descargar-csv` a esta
# carpeta. Con EXACTAI_CSV_DIR se puede leer de otro lado.
carpeta = Path(os.environ.get("EXACTAI_CSV_DIR", Path(__file__).resolve().parent))

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado"]
archivos = [carpeta / f"{dia}.csv" for dia in DIAS]

faltan = [a.name for a in archivos if not a.exists()]
if faltan:
    sys.exit(f"Faltan CSV en {carpeta}: {', '.join(faltan)} (corre primero: pipeline.py descargar-csv)")

dfs = []

for archivo in archivos:

    # dtype=str: Aula/Pab. se leen siempre como texto ("1102", no 1102.0 si hay vacios).
    df = pd.read_csv(
        archivo,
        header=1,
        encoding="utf-8",
        dtype=str
    )

    dfs.append(df)

df = pd.concat(dfs, ignore_index=True)

df = df[df["Fecha"].isna()]

print(df)

# Las salidas se escriben en el directorio actual (el pipeline corre este script
# dentro de su carpeta de trabajo).
df.to_csv(
    "cartelera_filtrada.csv",
    index=False,
    encoding="utf-8"
)

# CSV de entrada (el que se acaba de escribir)
csv_file = Path("cartelera_filtrada.csv")

# JSON de salida
json_file = "comisiones.json"

# Leer CSV
df = pd.read_csv(csv_file, dtype=str)

# Renombrar columnas
df_json = pd.DataFrame({
    "materia": df["Actividades"],
    "turno": df["Turno"],
    "tipo": df["Clase"],
    "dia": df["Día"],
    "hora_inicio": df["Inicio"],
    "hora_fin": df["Fin"],
    "pabellon": df["Pab."],
    "aula": df["Aula"]
})

# Reemplazar NaN por None (a mano: df.where(..., None) deja NaN en las versiones nuevas de pandas
# con columnas de texto, y NaN no es JSON valido)
registros = [
    {k: (None if pd.isna(v) else v) for k, v in fila.items()}
    for fila in df_json.to_dict(orient="records")
]

# Guardar JSON
with open(json_file, "w", encoding="utf-8") as f:
    json.dump(registros, f, ensure_ascii=False, indent=2)

print(f"Se guardaron {len(registros)} registros en {json_file}")
