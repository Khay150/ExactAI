import time
import json
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service


BASE_URL = "https://www.df.uba.ar"

URL_PROFESORES = "https://www.df.uba.ar/es/staff/profesores"
URL_JTPS = "https://www.df.uba.ar/es/staff/docentes-auxiliares/jefes-de-trabajos-practicos"


# =========================
# DRIVER (SELENIUM)
# =========================

def get_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    return driver


# =========================
# FETCH CON JS
# =========================

def fetch(driver, url):
    driver.get(url)
    time.sleep(1.5)  # ⬅️ importante para que cargue el JS
    return driver.page_source


# =========================
# UTILIDADES
# =========================

def separar_nombre(nombre_completo):
    if "," in nombre_completo:
        apellido, nombre = [p.strip() for p in nombre_completo.split(",", 1)]
    else:
        partes = nombre_completo.split()
        nombre = partes[-1]
        apellido = " ".join(partes[:-1])
    return nombre, apellido


def save_json(data, filename="docentes_df.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"docentes": data}, f, indent=2, ensure_ascii=False)


# =========================
# PROFESORES
# =========================

def get_profesores_info(html):
    soup = BeautifulSoup(html, "html.parser")
    profesores = []

    filas = soup.select("table tbody tr")

    for fila in filas:
        tds = fila.find_all("td")
        if len(tds) < 2:
            continue

        link_tag = fila.select_one("td.image-cell a")
        nombre_td = tds[1]

        if link_tag and link_tag.get("href"):
            link = urljoin(BASE_URL, link_tag["href"])
            nombre_completo = nombre_td.get_text(strip=True)

            profesores.append({
                "link": link,
                "nombre_completo": nombre_completo
            })

    return profesores


def parse_mail_profesor(html):
    soup = BeautifulSoup(html, "html.parser")

    cont = soup.select_one("td.personal_info")
    if not cont:
        return None

    spans = cont.find_all("span")

    for span in spans:
        texto = span.get_text(strip=True)

        if "@" in texto and "info@" not in texto:
            return texto

    return None


def scrape_profesores(driver):
    html = fetch(driver, URL_PROFESORES)
    profesores = get_profesores_info(html)

    print(f"🔗 Profesores encontrados: {len(profesores)}")

    docentes = []

    for i, prof in enumerate(profesores):
        try:
            print(f"➡️ ({i+1}/{len(profesores)}) {prof['link']}")

            html_doc = fetch(driver, prof["link"])
            mail = parse_mail_profesor(html_doc)

            if not mail:
                print("⚠️ Sin mail:", prof["nombre_completo"])

            nombre, apellido = separar_nombre(prof["nombre_completo"])

            docentes.append({
                "nombre_completo": prof["nombre_completo"],
                "nombre": nombre,
                "apellido": apellido,
                "mail": mail,
                "departamento": "DF"
            })

        except Exception as e:
            print("❌ Error:", prof["link"], e)

    return docentes


# =========================
# JTPs
# =========================

def extraer_mail_jtp(td):
    a = td.find("a", href=True)
    if a and "mailto:" in a["href"]:
        return a.get_text(strip=True)

    spans = td.find_all("span")
    for span in spans:
        texto = span.get_text(strip=True)
        if "@" in texto:
            return texto

    return None


def scrape_jtps(driver):
    html = fetch(driver, URL_JTPS)
    soup = BeautifulSoup(html, "html.parser")

    docentes = []

    filas = soup.select("table tbody tr")

    for fila in filas:
        tds = fila.find_all("td")
        if len(tds) < 2:
            continue

        nombre_completo = tds[0].get_text(strip=True)
        mail = extraer_mail_jtp(tds[1])

        if not mail:
            print("⚠️ JTP sin mail:", nombre_completo)

        nombre, apellido = separar_nombre(nombre_completo)

        docentes.append({
            "nombre_completo": nombre_completo,
            "nombre": nombre,
            "apellido": apellido,
            "mail": mail,
            "departamento": "DF"
        })

    return docentes


# =========================
# SCRAPER PRINCIPAL
# =========================

def scrape_df():
    driver = get_driver()

    try:
        print("🔍 Profesores DF...")
        profs = scrape_profesores(driver)

        print("\n🔍 JTPs DF...")
        jtps = scrape_jtps(driver)

        todos = profs + jtps

        # deduplicación
        unique = {}
        for d in todos:
            key = d["mail"] if d["mail"] else d["nombre_completo"]
            unique[key] = d

        return list(unique.values())

    finally:
        driver.quit()


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    docentes = scrape_df()

    save_json(docentes)

    print(f"\n✅ Total DF: {len(docentes)}")