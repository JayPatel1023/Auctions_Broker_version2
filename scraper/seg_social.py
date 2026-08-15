"""
Seguridad Social - Subastas de Bienes Embargados scraper
Sitio: https://w6.seg-social.es/subastas/SubaSeControladorInter

Es una app Java vieja que depende de sesion (cookie JSESSIONID) y de
formularios con checkboxes (provincia, tipo de bien). La pagina viene en
ISO-8859-1, no UTF-8. Sin sesion valida (o sin pasar por la pagina de
busqueda antes de postear) tira un "NullPointerException" generico.

Cada lote necesita 2 pedidos:
  1. SubaSeControladorInter?opcion=7 (POST)          -> listado agrupado en
     tablas por tipo de bien x provincia: nombre corto, tasacion, cargas,
     lote, valor de enajenacion, fecha de subasta, y el EMB_ID (en el link)
  2. SubaSeControladorInter?opcion=13&EMB_ID=X        -> resto de datos:
     descripcion, localizacion, marca/modelo/matricula (si es vehiculo),
     expediente, unidad de recaudacion ejecutiva

El sitio no tiene el limite de resultados que tiene BOE Subastas (pagina
en vez de bloquear), pero para la sincronizacion diaria igual conviene
fragmentar por provincia para no traer de mas.
"""

import logging
import random
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE = "https://w6.seg-social.es/subastas/SubaSeControladorInter"

TIPOS_BIEN = {
    "0101": "Finca Rústica",
    "0102": "Finca Urbana",
    "0211": "Vehículo",
    "0212": "Embarcación",
    "0215": "Resto de Bienes Muebles",
}

CAMPOS_RANGO = [
    "CAMPO_TASACION_DESDE", "CAMPO_TASACION_HASTA",
    "CAMPO_CARGAS_DESDE", "CAMPO_CARGAS_HASTA",
    "CAMPO_FSUBASTA_DESDE", "CAMPO_FSUBASTA_HASTA",
    "CAMPO_LICITACION_DESDE", "CAMPO_LICITACION_HASTA",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("seg_social")


def _clean(txt):
    return re.sub(r"\s+", " ", txt or "").strip()


def _money_to_float(txt):
    if not txt:
        return None
    txt = _clean(txt)
    if "sin" in txt.lower():
        return None
    m = re.search(r"[\d.]+,\d{2}", txt)
    if not m:
        return None
    return float(m.group(0).replace(".", "").replace(",", "."))


def _inferir_estado(fecha_txt):
    """El sitio no da un estado explicito, solo la fecha/hora de la subasta."""
    try:
        dt = datetime.strptime(fecha_txt.strip(), "%d/%m/%Y %H:%M")
    except (ValueError, AttributeError):
        return "Próxima apertura"
    ahora = datetime.now()
    if dt.date() == ahora.date():
        return "Celebrándose"
    if dt > ahora:
        return "Próxima apertura"
    return "Concluida"


class SegSocialScraper:
    def __init__(self, delay_min=1.0, delay_max=2.5):
        self.session = requests.Session()
        self.delay_min = delay_min
        self.delay_max = delay_max
        self._rotate_headers()
        self._sesion_lista = False

    def _rotate_headers(self):
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9",
        })

    def _wait(self):
        time.sleep(random.uniform(self.delay_min, self.delay_max))

    def _asegurar_sesion(self):
        """El formulario de busqueda (opcion=7) tira NullPointerException si
        no se paso antes por la pagina de busqueda avanzada, que es la que
        arma la sesion del lado del servidor."""
        if self._sesion_lista:
            return
        resp = self.session.get(BASE, params={"opcion": 6, "avanzada": 1}, timeout=30)
        resp.encoding = "iso-8859-1"
        resp.raise_for_status()
        self._sesion_lista = True

    # ---------- listado ----------

    def buscar(self, provincias, tipos_bien=None):
        """provincias: lista de codigos INE de 2 digitos (mismos que BOE).
        Devuelve lista de lotes resumidos de la pagina 1 de resultados."""
        self._asegurar_sesion()
        tipos_bien = tipos_bien or list(TIPOS_BIEN.keys())

        fields = [("opcion", "7")]
        for t in tipos_bien:
            fields.append(("EMB_TIPOBIEN", t))
        for campo in CAMPOS_RANGO:
            fields.append((campo, ""))
        for prov in provincias:
            fields.append(("provincia", prov))
        fields.append(("Aceptar", "Comenzar Busqueda"))

        try:
            resp = self.session.post(BASE, data=fields, timeout=30)
            resp.encoding = "iso-8859-1"
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error(f"Error consultando provincias={provincias}: {e}")
            return []

        if "ERROR en la Aplicaci" in resp.text:
            log.warning(f"provincias={provincias}: la app devolvio una pagina de error, reintento con sesion nueva")
            self._sesion_lista = False
            self._asegurar_sesion()
            return []

        lotes = self._parse_listado(resp.text)
        self._wait()
        return lotes

    def buscar_todas_paginas(self, provincias, tipos_bien=None, max_paginas=100):
        """Igual que buscar(), pero recorre todas las paginas de resultados
        en vez de quedarse solo con la primera (para el barrido historico).
        El sitio no tiene limite de resultados como BOE, solo pagina de a 20."""
        primera = self.buscar(provincias, tipos_bien)
        todos = list(primera)
        if not primera:
            return todos

        pagina = 2
        while pagina <= max_paginas:
            try:
                resp = self.session.get(BASE, params={"pagina": pagina, "opcion": 7}, timeout=30)
                resp.encoding = "iso-8859-1"
                resp.raise_for_status()
            except requests.RequestException as e:
                log.warning(f"Error en pagina {pagina}: {e}")
                break
            nuevos = self._parse_listado(resp.text)
            if not nuevos:
                break
            todos.extend(nuevos)
            self._wait()
            pagina += 1
        return todos

    def _parse_listado(self, html):
        soup = BeautifulSoup(html, "html.parser")
        lotes = []

        for tabla in soup.select("div.tablas-resultados table"):
            caption = tabla.find("caption")
            tipo_bien = provincia = ""
            if caption:
                partes = _clean(caption.get_text()).split(" - ")
                if len(partes) >= 2:
                    tipo_bien, provincia = partes[0], partes[1]

            for tr in tabla.select("tbody tr"):
                tds = tr.find_all("td")
                if len(tds) < 7:
                    continue
                link = tds[0].find("a")
                if not link:
                    continue
                m = re.search(r"EMB_ID=(\d+)", link.get("href", ""))
                if not m:
                    continue
                emb_id = m.group(1)
                lote_m = re.search(r"\d+", _clean(tds[4].get_text()))

                lotes.append({
                    "id": f"SS-{emb_id}",
                    "emb_id": emb_id,
                    "tipo_bien": tipo_bien,
                    "provincia_busqueda": provincia,
                    "nombre": _clean(link.get_text()),
                    "valor_tasacion": _money_to_float(tds[2].get_text()),
                    "cantidad_reclamada": _money_to_float(tds[3].get_text()),
                    "lotes": lote_m.group(0) if lote_m else "1",
                    "valor_subasta": _money_to_float(tds[5].get_text()),
                    "fecha_subasta": _clean(tds[6].get_text()),
                })

        return lotes

    # ---------- detalle ----------

    def detalle(self, emb_id):
        self._asegurar_sesion()
        try:
            resp = self.session.get(
                BASE, params={"opcion": 13, "EMB_ID": emb_id, "opcion2": 1, "tipoOperacion": 0}, timeout=30
            )
            resp.encoding = "iso-8859-1"
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning(f"No se pudo obtener detalle de EMB_ID={emb_id}: {e}")
            return {}

        soup = BeautifulSoup(resp.text, "html.parser")
        campos = self._dl_a_dict(soup)
        self._wait()

        direccion = localidad = provincia = ""
        localizacion = campos.get("Localización", [])
        if localizacion:
            direccion = localizacion[0]
        if len(localizacion) > 1:
            # Finca Rustica: 2 dd -> "termino" + "municipio (provincia)"
            m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", localizacion[1])
            if m:
                localidad, provincia = m.group(1).strip(), m.group(2).strip()
        elif localizacion:
            # Finca Urbana: 1 dd -> direccion completa con "(cod. postal) ciudad" al final
            m = re.search(r"\(\d{4,5}\)\s*(.+)$", localizacion[0])
            if m:
                localidad = m.group(1).strip()

        descripcion = (campos.get("Descripción Detallada") or [""])[0]

        ref_cat = ""
        rm = re.search(r"REFERENCIA CATASTRAL\s+([A-Z0-9]+)", descripcion.upper())
        if rm:
            ref_cat = rm.group(1)

        return {
            "descripcion": descripcion,
            "direccion": direccion,
            "localidad": localidad,
            "provincia": provincia,
            "referencia_catastral": ref_cat,
            "marca": (campos.get("Marca") or [""])[0],
            "modelo": (campos.get("Modelo") or [""])[0],
            "matricula": (campos.get("Matrícula") or [""])[0],
            "fecha_subasta": (campos.get("Fecha") or [""])[0],
            "nombre_expediente": (campos.get("Expediente") or [""])[0],
        }

    def _dl_a_dict(self, soup):
        """Junta todos los pares dt/dd del detalle en un dict de listas
        (algunos dt como 'Localizacion' traen mas de un dd seguido)."""
        out = {}
        clave = None
        for el in soup.select("dl.notas *, dl.datos *"):
            if el.name == "dt":
                clave = _clean(el.get_text()).rstrip(":")
                out.setdefault(clave, [])
            elif el.name == "dd" and clave:
                out[clave].append(_clean(el.get_text()))
        return out

    # ---------- orquestacion ----------

    def sync_provincias(self, provincias, con_detalle=True, limite_por_combo=None):
        """Recorre provincias una por una (primera pagina de resultados de
        cada una). Uso diario: trae lo que hay ahora, no el historico."""
        todos = []
        for prov_cod in provincias:
            lotes = self.buscar([prov_cod])
            if limite_por_combo:
                lotes = lotes[:limite_por_combo]
            if lotes:
                log.info(f"provincia {prov_cod}: {len(lotes)} lotes")
            if con_detalle:
                for lote in lotes:
                    extra = self.detalle(lote["emb_id"])
                    lote.update(extra)
            todos.extend(lotes)
        return todos


if __name__ == "__main__":
    scraper = SegSocialScraper()
    lotes = scraper.buscar(["28", "08"])
    print(f"{len(lotes)} lotes encontrados en el listado")
    if lotes:
        primero = lotes[0]
        print("Primero (resumen):", primero)
        extra = scraper.detalle(primero["emb_id"])
        print("Detalle completo:", extra)
