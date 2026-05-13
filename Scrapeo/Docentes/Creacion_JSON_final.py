import json
import unicodedata
import re
from collections import defaultdict


# =========================
# ARCHIVOS A UNIR
# =========================

FILES = [
    "docentes_dc.json",
    "docentes_dm.json",
    "docentes_df.json",
    "docentes_dfbmc.json",
    "docentes_dqo.json",
    "docentes_dqi.json",
    "docentes_dg.json",
    "docentes_dqb.json",
    "docentes_dao.json",
]


# =========================
# NORMALIZACIÓN
# =========================

def normalizar(texto):
    if not texto:
        return ""

    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))

    return texto.lower().strip()

# =========================
# LIMPIEZA DE NOMBRES
# =========================

def limpiar_titulos(nombre):
    return re.sub(r"\b(Dr\.?|Dra\.?|Lic\.?|Prof\.?)\s*", "", nombre).strip()


def score_nombre(nombre):
    score = 0

    # penalizar títulos
    if re.search(r"\b(Dr\.?|Dra\.?|Lic\.?|Prof\.?)\b", nombre):
        score -= 10

    # penalizar iniciales tipo "M."
    if re.search(r"\b[A-Z]\.", nombre):
        score -= 2

    # premiar longitud
    score += len(nombre)

    return score


def elegir_mejor_nombre(nuevo, existente):
    nuevo_limpio = limpiar_titulos(nuevo)
    existente_limpio = limpiar_titulos(existente)

    if score_nombre(nuevo_limpio) > score_nombre(existente_limpio):
        return nuevo_limpio
    else:
        return existente_limpio


# =========================
# CARGA
# =========================

def cargar_todos():
    todos = []

    for file in FILES:
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)

                if "docentes" in data:
                    todos.extend(data["docentes"])
                else:
                    print(f"⚠️ Formato inesperado en {file}")

        except Exception as e:
            print(f"❌ Error leyendo {file}: {e}")

    return todos


# =========================
# DEDUPLICACIÓN
# =========================


def deduplicar(docentes):
    unicos = {}

    for d in docentes:
        mail = d.get("mail")

        if mail:
            key = normalizar(mail)
        else:
            key = normalizar(d["nombre"] + d["apellido"])

        if key not in unicos:
            nuevo = d.copy()

            nuevo["departamentos"] = [d["departamento"]]
            del nuevo["departamento"]

            unicos[key] = nuevo

        else:
            existente = unicos[key]

            # agregar departamento
            if d["departamento"] not in existente["departamentos"]:
                existente["departamentos"].append(d["departamento"])

            # completar mail
            if not existente.get("mail") and d.get("mail"):
                existente["mail"] = d["mail"]

            # elegir mejor nombre
            existente["nombre_completo"] = elegir_mejor_nombre(
                d["nombre_completo"],
                existente["nombre_completo"]
            )

    return list(unicos.values())

def auditoria_duplicados(docentes):
    grupos = defaultdict(list)

    for d in docentes:
        key = normalizar(d.get("mail")) if d.get("mail") else normalizar(d["nombre_completo"])
        grupos[key].append(d)

    total_grupos = 0
    total_eliminados = 0

    for k, lista in grupos.items():
        if len(lista) > 1:
            total_grupos += 1
            eliminados = len(lista) - 1
            total_eliminados += eliminados

            print(f"\nKEY: {k}")
            print(f"Apariciones: {len(lista)} (elimina {eliminados})")

            for d in lista:
                print(f"  - {d['nombre_completo']} | {d.get('mail')} | {d.get('departamento')}")

    print("\n====================")
    print(f"Grupos duplicados: {total_grupos}")
    print(f"Registros a eliminar: {total_eliminados}")
    
# =========================
# GUARDADO
# =========================

def save_json(data, filename="docentes_total.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"docentes": data}, f, indent=2, ensure_ascii=False)


def verificar_departamento(docentes):
    errores = 0

    for i, d in enumerate(docentes):
        if "departamento" not in d:
            errores += 1

            nombre = d.get("nombre_completo", "SIN NOMBRE")
            mail = d.get("mail", "SIN MAIL")

            print(f"\n❌ Docente sin departamento:")
            print(f"   Nombre: {nombre}")
            print(f"   Mail: {mail}")
            print(f"   Índice: {i}")
            print(f"   Registro completo: {d}")

    if errores == 0:
        print("✅ Todos los docentes tienen departamento")
    else:
        print(f"\n⚠️ Total docentes sin departamento: {errores}")
    
# =========================
# MAIN
# =========================

def main():
    print("📂 Cargando archivos...")
    todos = cargar_todos()

    print(f"📊 Total sin limpiar: {len(todos)}")
    
    verificar_departamento(todos)
    
    auditoria_duplicados(todos)

    print("🔄 Deduplicando...")
    final = deduplicar(todos)

    print(f"✅ Total final: {len(final)}")

    save_json(final)

    print("💾 Guardado en docentes_total.json")


if __name__ == "__main__":
    main()