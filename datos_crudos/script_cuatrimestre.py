import json
import os

# Orden cronologico dentro de un año. El verano se parte en dos:
#   intensivo  (enero-marzo)  -> <anio>_verano_int, va ANTES del 1er cuatrimestre
#   vacaciones (diciembre)    -> <anio>_verano_vac, va al FINAL del año (prepara el verano que viene)
PERIODOS = [
    # (sufijo del id, periodo, tipo)
    ("verano_int", "verano", "intensivo"),
    ("1c", 1, "cuatrimestre"),
    ("1b", 1, "bimestre"),
    ("2b", 2, "bimestre"),
    ("inv", "invierno", "intensivo"),
    ("2c", 2, "cuatrimestre"),
    ("3b", 3, "bimestre"),
    ("4b", 4, "bimestre"),
    ("verano_vac", "verano", "vacaciones"),
]

ANIO_BASE = 2026   # el "orden" arranca de este año; con esta base el 2026 queda con ordenes 2..10


def generar_cuatrimestres(anio):
    """Cuatrimestres del año `anio`, mas el verano de vacaciones (diciembre) del año
    anterior: los eventos de diciembre del año anterior (publicacion/inscripcion al
    curso de verano) pertenecen a ese periodo.

    "orden" es una secuencia cronologica unica entre años (la misma formula para todos,
    asi un cuatrimestre tiene siempre el mismo orden sin importar en que archivo salga):
    el 2025_verano_vac es 1, el 2026 va de 2 a 10, el 2027 de 11 a 19, etc."""

    def orden(a, k):  # k = 1..9, posicion dentro de PERIODOS
        return (a - ANIO_BASE) * len(PERIODOS) + k + 1

    sufijo, periodo, tipo = PERIODOS[-1]
    cuatris = [{
        "id_cuatri": f"{anio - 1}_{sufijo}",
        "anio": anio - 1,
        "periodo": periodo,
        "tipo": tipo,
        "orden": orden(anio - 1, len(PERIODOS)),
    }]
    for k, (sufijo, periodo, tipo) in enumerate(PERIODOS, start=1):
        cuatris.append({
            "id_cuatri": f"{anio}_{sufijo}",
            "anio": anio,
            "periodo": periodo,
            "tipo": tipo,
            "orden": orden(anio, k),
        })
    return cuatris


def guardar_json(data, filename):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"cuatrimestres": data}, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    anio = int(os.environ.get("EXACTAI_ANIO", 2026))
    cuatris = generar_cuatrimestres(anio)

    print(f"Generados: {len(cuatris)} cuatrimestres")

    guardar_json(cuatris, f"cuatrimestres_{anio}.json")
