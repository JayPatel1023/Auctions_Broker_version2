"""
BOE Subastas scraper
Sitio: https://subastas.boe.es/

Una busqueda sin filtros (o con muy pocos) tira "El numero de resultados
obtenidos... es excesivo" (limite propio del sitio, no un bloqueo anti-bot,
confirmado a mano contra el sitio real). Por eso se fragmenta por
provincia x estado.

Cada lote necesita 3 pedidos para tener los 24 campos del formato viejo
(Auctions.Web):
  1. subastas_ava.php (POST)              -> listado: Id, Estado, Nombre,
     Expediente, Fecha conclusion (resumen), Descripcion (resumen)
  2. detalleSubasta.php?idSub=X            -> Tipo de subasta, Fecha inicio,
     Fecha conclusion (exacta), Cantidad reclamada, Valor subasta, Tasacion,
     Puja minima, Tramos entre pujas, Importe del deposito
  3. detalleSubasta.php?idSub=X&ver=3      -> Tipo de bien, Descripcion,
     Referencia catastral / Marca / Modelo / Matricula (segun tipo),
     Direccion, Localidad, Provincia
"""

import logging
import random
import re
import time

import requests
from bs4 import BeautifulSoup

BASE = "https://subastas.boe.es"
SEARCH_URL = f"{BASE}/subastas_ava.php"
DETALLE_URL = f"{BASE}/detalleSubasta.php"

ESTADOS = {
    "PU": "Próxima apertura",
    "EJ": "Celebrándose",
    "SU": "Suspendida",
    "CA": "Cancelada",
    "PC": "Concluida",
    "FS": "Concluida",
}
ESTADOS_ACTIVOS = ["PU", "EJ"]
ESTADOS_HISTORICOS = ["PC", "FS"]

PROVINCIAS = {
    "01": "Araba/Álava", "02": "Albacete", "03": "Alicante/Alacant", "04": "Almería",
    "05": "Ávila", "06": "Badajoz", "07": "Illes Balears", "08": "Barcelona",
    "09": "Burgos", "10": "Cáceres", "11": "Cádiz", "12": "Castellón/Castelló",
    "13": "Ciudad Real", "14": "Córdoba", "15": "A Coruña", "16": "Cuenca",
    "17": "Girona", "18": "Granada", "19": "Guadalajara", "20": "Gipuzkoa",
    "21": "Huelva", "22": "Huesca", "23": "Jaén", "24": "León", "25": "Lleida",
    "26": "La Rioja", "27": "Lugo", "28": "Madrid", "29": "Málaga", "30": "Murcia",
    "31": "Navarra", "32": "Ourense", "33": "Asturias", "34": "Palencia",
    "35": "Las Palmas", "36": "Pontevedra", "37": "Salamanca",
    "38": "Santa Cruz de Tenerife", "39": "Cantabria", "40": "Segovia",
    "41": "Sevilla", "42": "Soria", "43": "Tarragona", "44": "Teruel",
    "45": "Toledo", "46": "Valencia/València", "47": "Valladolid", "48": "Bizkaia",
    "49": "Zamora", "50": "Zaragoza", "51": "Ceuta", "52": "Melilla",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

EXCESO_MSG = "es excesivo"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("boe")


def _clean(txt):
    return re.sub(r"\s+", " ", txt or "").strip()


def _money_to_float(txt):
    """'662.067,00 €' -> 662067.00 ; 'Sin puja mínima' -> None"""
    if not txt:
        return None
    txt = _clean(txt)
    if "sin" in txt.lower():
        return None
    m = re.search(r"[\d.]+,\d{2}", txt)
    if not m:
        return None
    return float(m.group(0).replace(".", "").replace(",", "."))


class BOEScraper:
    def __init__(self, delay_min=1.0, delay_max=2.5):
        self.session = requests.Session()
        self.delay_min = delay_min
        self.delay_max = delay_max
        self._rotate_headers()

    def _rotate_headers(self):
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9",
        })

    def _wait(self):
        time.sleep(random.uniform(self.delay_min, self.delay_max))

    def _base_fields(self):
        return {
            "campo[0]": "SUBASTA.ORIGEN", "dato[0]": "",
            "campo[1]": "SUBASTA.AUTORIDAD", "dato[1]": "",
            "campo[2]": "SUBASTA.ESTADO.CODIGO", "dato[2]": "",
            "campo[3]": "BIEN.TIPO", "dato[3]": "",
            "campo[5]": "BIEN.DIRECCION", "dato[5]": "",
            "campo[6]": "BIEN.CODPOSTAL", "dato[6]": "",
            "campo[7]": "BIEN.LOCALIDAD", "dato[7]": "",
            "campo[8]": "BIEN.COD_PROVINCIA", "dato[8]": "",
            "accion": "Buscar",
        }

    # ---------- listado ----------

    def buscar(self, provincia_cod: str, estado_cod: str):
        """Busca lotes para una combinacion provincia x estado.
        Devuelve (lista_de_lotes_resumidos, hay_mas_paginas)."""
        fields = self._base_fields()
        fields["dato[8]"] = provincia_cod
        fields["dato[2]"] = estado_cod

        try:
            resp = self.session.post(SEARCH_URL, data=fields, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error(f"Error consultando provincia={provincia_cod} estado={estado_cod}: {e}")
            return [], False

        if EXCESO_MSG in resp.text:
            log.warning(
                f"provincia={PROVINCIAS.get(provincia_cod, provincia_cod)} "
                f"estado={ESTADOS.get(estado_cod, estado_cod)}: demasiados resultados"
            )
            return [], False

        lotes, hay_mas = self._parse_listado(resp.text, provincia_cod, estado_cod)
        self._wait()
        return lotes, hay_mas

    def _parse_listado(self, html: str, provincia_cod: str, estado_cod: str):
        soup = BeautifulSoup(html, "html.parser")
        lotes = []

        for li in soup.select("li.resultado-busqueda"):
            h3 = li.find("h3")
            if not h3:
                continue
            id_match = re.search(r"SUBASTA\s+([A-Z0-9\-]+)", h3.get_text())
            if not id_match:
                continue
            ref_id = id_match.group(1)

            h4 = li.find("h4")
            nombre = _clean(h4.get_text()) if h4 else ""

            ps = li.find_all("p")
            expediente = ""
            estado_txt = ESTADOS.get(estado_cod, "")
            fecha_fin_resumen = ""
            descripcion = ""
            for p in ps:
                t = _clean(p.get_text())
                if t.startswith("Expediente:"):
                    expediente = t.replace("Expediente:", "").strip()
                elif t.startswith("Estado:"):
                    estado_txt = t.replace("Estado:", "").split("-")[0].strip() or estado_txt
                    fm = re.search(r"Conclusi[oó]n prevista:\s*([0-9/]+\s+a\s+las\s+[0-9:]+)", t)
                    if fm:
                        fecha_fin_resumen = fm.group(1)
                elif t.startswith("Descripci"):
                    descripcion = t.split(":", 1)[-1].strip()

            lotes.append({
                "id": ref_id,
                "estado": estado_txt,
                "provincia_busqueda": PROVINCIAS.get(provincia_cod, provincia_cod),
                "expediente": expediente,
                "fecha_fin_resumen": fecha_fin_resumen,
                "nombre": nombre,
                "descripcion_resumen": descripcion,
            })

        hay_mas = bool(soup.find("a", string=re.compile("siguiente", re.I)))
        return lotes, hay_mas

    # ---------- detalle ----------

    def detalle(self, id_sub: str):
        """Trae los campos financieros + el/los bien(es) de una subasta.
        Devuelve un dict listo para combinar con la fila del listado."""
        datos = {}
        datos.update(self._detalle_financiero(id_sub))
        datos.update(self._detalle_bien(id_sub))
        self._wait()
        return datos

    def _tabla_a_dict(self, soup):
        out = {}
        for tr in soup.select("table tr"):
            th = tr.find("th")
            td = tr.find("td")
            if th and td:
                out[_clean(th.get_text())] = _clean(td.get_text())
        return out

    def _detalle_financiero(self, id_sub: str):
        try:
            resp = self.session.get(DETALLE_URL, params={"idSub": id_sub}, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning(f"No se pudo obtener detalle financiero de {id_sub}: {e}")
            return {}

        soup = BeautifulSoup(resp.text, "html.parser")
        campos = self._tabla_a_dict(soup)

        return {
            "tipo_subasta": campos.get("Tipo de subasta", ""),
            "fecha_inicio": campos.get("Fecha de inicio", ""),
            "fecha_conclusion": campos.get("Fecha de conclusión", ""),
            "cantidad_reclamada": _money_to_float(campos.get("Cantidad reclamada")),
            "valor_subasta": _money_to_float(campos.get("Valor subasta")),
            "valor_tasacion": _money_to_float(campos.get("Tasación")),
            "puja_minima": _money_to_float(campos.get("Puja mínima")),
            "tramos_entre_pujas": _money_to_float(campos.get("Tramos entre pujas")),
            "importe_deposito": _money_to_float(campos.get("Importe del depósito")),
            "lotes": campos.get("Lotes", ""),
        }

    def _detalle_bien(self, id_sub: str):
        try:
            resp = self.session.get(DETALLE_URL, params={"idSub": id_sub, "ver": 3}, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning(f"No se pudo obtener detalle de bien de {id_sub}: {e}")
            return {}

        soup = BeautifulSoup(resp.text, "html.parser")

        h4 = soup.find("h4", string=re.compile(r"^Bien 1"))
        tipo_bien = ""
        if h4:
            m = re.search(r"Bien 1 - ([^(]+)", h4.get_text())
            if m:
                tipo_bien = m.group(1).strip()

        campos = {}
        if h4:
            tabla = h4.find_next("table")
            if tabla:
                campos = self._tabla_a_dict(BeautifulSoup(str(tabla), "html.parser"))

        return {
            "tipo_bien": tipo_bien,
            "descripcion": campos.get("Descripción", "").replace("Descripción:", "").strip(),
            "referencia_catastral": campos.get("Referencia catastral", ""),
            "direccion": campos.get("Dirección", ""),
            "localidad": campos.get("Localidad", ""),
            "provincia": campos.get("Provincia", ""),
            "marca": campos.get("Marca", ""),
            "modelo": campos.get("Modelo", ""),
            "matricula": campos.get("Matrícula", campos.get("Matricula", "")),
        }

    # ---------- orquestacion ----------

    def sync_estado(self, estado_cod: str, provincias=None, con_detalle=True, limite_por_combo=None):
        """Recorre todas las provincias para un estado dado. Uso diario: PU/EJ."""
        provincias = provincias or list(PROVINCIAS.keys())
        todos = []
        for prov_cod in provincias:
            lotes, _ = self.buscar(prov_cod, estado_cod)
            if limite_por_combo:
                lotes = lotes[:limite_por_combo]
            if lotes:
                log.info(f"{PROVINCIAS[prov_cod]} / {ESTADOS[estado_cod]}: {len(lotes)} lotes")
            if con_detalle:
                for lote in lotes:
                    extra = self.detalle(lote["id"])
                    lote.update(extra)
            todos.extend(lotes)
        return todos


if __name__ == "__main__":
    scraper = BOEScraper()
    lotes, hay_mas = scraper.buscar("28", "EJ")
    print(f"{len(lotes)} lotes encontrados en el listado (hay mas paginas: {hay_mas})")
    if lotes:
        primero = lotes[0]
        print("Primero (resumen):", primero)
        extra = scraper.detalle(primero["id"])
        print("Detalle completo:", extra)
