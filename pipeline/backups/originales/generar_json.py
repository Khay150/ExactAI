import pandas as pd
from pathlib import Path
import json

carpeta = Path(__file__).parent

archivos = [
    carpeta / "Cartelera - Lunes.csv",
    carpeta / "Cartelera - Martes.csv",
    carpeta / "Cartelera - Miércoles.csv",
    carpeta / "Cartelera - Jueves.csv",
    carpeta / "Cartelera - Viernes.csv",
    carpeta / "Cartelera - Sábado.csv"
]

# for archivo in archivos:
#     print(archivo, archivo.exists())

dfs = []

for archivo in archivos:

    df = pd.read_csv(
        archivo,
        header=1,
        encoding="utf-8"
    )
    
    dfs.append(df)

df = pd.concat(dfs, ignore_index=True)

df = df[df["Fecha"].isna()]

print(df)

df.to_csv(
    "cartelera_filtrada.csv",
    index=False,
    encoding="utf-8"
)

# CSV de entrada
csv_file = carpeta / "cartelera_filtrada.csv"

# JSON de salida
json_file = "comisiones.json"

# Leer CSV
df = pd.read_csv(csv_file)

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

# Reemplazar NaN por None
registros = df_json.where(pd.notnull(df_json), None).to_dict(orient="records")

# Guardar JSON
with open(json_file, "w", encoding="utf-8") as f:
    json.dump(registros, f, ensure_ascii=False, indent=2)

print(f"Se guardaron {len(registros)} registros en {json_file}")
