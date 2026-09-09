import json

def generar_cuatrimestres(anio):
    cuatris = []

    # =========================
    # ORDEN GLOBAL
    # =========================
    ORDEN = {
        "verano": 1,
        "1c": 2,
        "1b": 3,
        "2b": 4,
        "inv": 5,
        "2c": 6,
        "3b": 7,
        "4b": 8
    }

    # =========================
    # VERANO
    # =========================
    cuatris.append({
        "id_cuatri": f"{anio}_verano",
        "anio": anio,
        "periodo": "verano",
        "tipo": "intensivo",
        "orden": ORDEN["verano"]
    })

    # =========================
    # CUATRIMESTRES
    # =========================
    for i in [1, 2]:
        cuatris.append({
            "id_cuatri": f"{anio}_{i}c",
            "anio": anio,
            "periodo": i,
            "tipo": "cuatrimestre",
            "orden": ORDEN[f"{i}c"]
        })

    # =========================
    # BIMESTRES 
    # =========================
    for i in [1, 2, 3, 4]:
        cuatris.append({
            "id_cuatri": f"{anio}_{i}b",
            "anio": anio,
            "periodo": i,
            "tipo": "bimestre",
            "orden": ORDEN[f"{i}b"]
        })

    # =========================
    # INVIERNO
    # =========================
    cuatris.append({
        "id_cuatri": f"{anio}_inv",
        "anio": anio,
        "periodo": "invierno",
        "tipo": "intensivo",
        "orden": ORDEN["inv"]
    })

  
    # ordenar por si acaso
    cuatris.sort(key=lambda x: x["orden"])

    return cuatris


def guardar_json(data, filename="cuatrimestres_2026.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"cuatrimestres": data}, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    anio = 2026
    cuatris = generar_cuatrimestres(anio)

    print(f"Generados: {len(cuatris)} cuatrimestres")

    guardar_json(cuatris)