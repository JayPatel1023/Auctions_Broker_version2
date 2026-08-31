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
    # PC y FS se mostraban las dos como "Concluida", pero son estados
    # distintos en el formulario real de BOE (confirmado contra el
    # formulario de busqueda avanzada) - el cliente lo señalo como filtro
    # incompleto porque no podia elegir uno sin el otro.
    "PC": "Concluida en Portal de Subastas",
    "FS": "Finalizada por Autoridad Gestora",
}
ESTADOS_ACTIVOS = ["PU", "EJ", "SU", "CA"]
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
# Confirmado en vivo (dos veces, en sesiones separadas): tras suficientes
# pedidos seguidos, BOE responde con esta pagina de verificacion en vez de
# resultados - status 200, sin "es excesivo", sin ningun li.resultado-busqueda,
# indistinguible de "0 resultados reales" si no se chequea explicitamente.
# Sin este chequeo, una sincronizacion que se cruza con esto queda con
# datos truncados a la mitad y ningun aviso de que algo salio mal.
# OJO: BOE manda los acentos como entidades HTML (ej. "Verificaci&#xF3;n"),
# asi que buscar el texto "Verificación" tal cual (con la o-con-tilde en
# UTF-8) nunca matchea - confirmado con el response real. "captcha" en
# minuscula aparece varias veces en la pagina (ids/clases del formulario)
# y no tiene ningun caracter especial que se pueda codificar distinto.
CAPTCHA_MSG = "captcha"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("boe")


def _clean(txt):
    return re.sub(r"\s+", " ", txt or "").strip()


def _quitar_iso(txt):
    """BOE agrega '(ISO: 2026-08-17T18:00:00+02:00)' despues de la fecha
    en texto plano, redundante para mostrar en la tabla."""
    return re.sub(r"\s*\(ISO:[^)]*\)", "", txt or "").strip()


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
    # Antes 1.0-2.5s. Pedido del cliente tras ver bloqueos de verificacion
    # de seguridad reiterados durante barridos largos: bajar la
    # frecuencia de pedidos de entrada, no solo reintentar despues del
    # bloqueo (ver _reintentar_por_captcha) - mas lento pero con menos
    # chance de toparse con el bloqueo en primer lugar. El barrido
    # historico ya corre en segundo plano durante horas, este cambio no
    # lo hace notablemente mas lento en terminos relativos.
    # Idea del cliente (2026-08-31) tras ver que el barrido historico
    # seguia bloqueandose pese al delay entre pedidos: en vez de solo
    # reintentar DESPUES de que ya bloqueo (ver CAPTCHA_ESPERA_SEGUNDOS
    # mas abajo), descansar un rato mas largo cada tantos pedidos para
    # bajar la chance de llegar a bloquearse en primer lugar. Es
    # preventivo, complementa al reintento reactivo, no lo reemplaza.
    PEDIDOS_POR_DESCANSO = 150
    DESCANSO_SEGUNDOS = 300

    def __init__(self, delay_min=2.5, delay_max=5.0, progreso=None):
        self.session = requests.Session()
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.progreso = progreso
        self._pedidos_desde_descanso = 0
        self._rotate_headers()

    def _rotate_headers(self):
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9",
        })

    def _wait(self):
        time.sleep(random.uniform(self.delay_min, self.delay_max))
        self._pedidos_desde_descanso += 1
        if self._pedidos_desde_descanso >= self.PEDIDOS_POR_DESCANSO:
            self._pedidos_desde_descanso = 0
            msg = f"Pausa preventiva de {self.DESCANSO_SEGUNDOS}s cada {self.PEDIDOS_POR_DESCANSO} pedidos (para no llegar al bloqueo de BOE)"
            log.info(msg)
            if self.progreso:
                self.progreso(msg)
            time.sleep(self.DESCANSO_SEGUNDOS)

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

    # Tope de paginas seguidas por combinacion provincia+estado+fecha, para
    # no quedar en un loop sin fin ante algo inesperado del lado del sitio
    # - 20 paginas de 50 son 1000 lotes reales, mucho mas de lo que se
    # espera de una ventana ya fragmentada (ver VENTANA_DIAS_HISTORICO en
    # ingest.py).
    MAX_PAGINAS_SEGUIDAS = 20

    # Antes, el primer golpe de la pagina de verificacion de seguridad de
    # BOE cortaba de una todo el resto de la sincronizacion historica que
    # faltaba (para esa fuente) - confirmado en vivo (dos veces, en
    # sesiones separadas con el cliente) que este bloqueo suele ser
    # pasajero. Ahora se espera y se reintenta antes de rendirse de
    # verdad, para que un bloqueo corto no tire por la borda horas de
    # avance sin siquiera intentar de nuevo.
    #
    # Antes 2 reintentos x 120s (4 minutos en total). Probado en vivo
    # (2026-08-31, barrido de Malaga): con esos valores el bloqueo seguia
    # activo en los 4 minutos y la pasada terminaba rindiendose siempre -
    # el bloqueo real de BOE dura mas que eso. Subido a 5 reintentos x
    # 600s (hasta 50 minutos en total) para dar tiempo real a que se
    # destrabe solo antes de rendirse.
    CAPTCHA_MAX_REINTENTOS = 5
    CAPTCHA_ESPERA_SEGUNDOS = 600

    def _reintentar_por_captcha(self, intento, contexto):
        """Se llama al toparse con la pagina de verificacion de seguridad
        de BOE. True si hay que reintentar (ya espero de por medio), False
        si se agotaron los CAPTCHA_MAX_REINTENTOS y hay que rendirse de
        verdad esta vez."""
        if intento >= self.CAPTCHA_MAX_REINTENTOS:
            return False
        msg = (
            f"BOE pidió verificación de seguridad en {contexto} - esperando "
            f"{self.CAPTCHA_ESPERA_SEGUNDOS}s antes de reintentar "
            f"(intento {intento + 1}/{self.CAPTCHA_MAX_REINTENTOS})"
        )
        log.warning(msg)
        if self.progreso:
            self.progreso(msg)
        time.sleep(self.CAPTCHA_ESPERA_SEGUNDOS)
        return True

    def buscar(self, provincia_cod: str, estado_cod: str, fecha_desde: str = None, fecha_hasta: str = None):
        """Busca lotes para una combinacion provincia x estado, opcionalmente
        acotado a un rango de fecha de conclusion (formato YYYY-MM-DD, es
        lo que espera el sitio pese a mostrar DD/MM/YYYY en pantalla).

        Sigue automaticamente todas las paginas de resultados hasta
        agotarlas (BOE pagina de a 50) - antes esto se quedaba solo con la
        primera pagina y perdia en silencio cualquier resultado mas alla
        del 50 (confirmado en vivo: una ventana con 74 resultados reales
        quedaba truncada a 50, sin ningun aviso ni error - el chequeo de
        "hay mas paginas" de antes tampoco detectaba esto: buscaba el link
        "Pág. siguiente" por su .string, que da None apenas el texto tiene
        una etiqueta <abbr> adentro, como en este sitio).

        Devuelve (lista_de_lotes_resumidos, hay_mas_paginas, excesivo).
        hay_mas_paginas queda en la firma por compatibilidad, pero con
        esto ya no hay paginas pendientes de traer (siempre False),
        salvo que se corte por MAX_PAGINAS_SEGUIDAS."""
        fields = self._base_fields()
        fields["dato[8]"] = provincia_cod
        fields["dato[2]"] = estado_cod
        if fecha_desde or fecha_hasta:
            fields["campo[17]"] = "SUBASTA.FECHA_FIN"
            fields["dato[17][0]"] = fecha_desde or ""
            fields["dato[17][1]"] = fecha_hasta or ""

        contexto = (
            f"provincia={PROVINCIAS.get(provincia_cod, provincia_cod)} "
            f"estado={ESTADOS.get(estado_cod, estado_cod)} fecha={fecha_desde}..{fecha_hasta}"
        )
        intento = 0
        while True:
            try:
                resp = self.session.post(SEARCH_URL, data=fields, timeout=30)
                resp.raise_for_status()
            except requests.RequestException as e:
                log.error(f"Error consultando {contexto}: {e}")
                return [], False, False
            if CAPTCHA_MSG in resp.text and self._reintentar_por_captcha(intento, contexto):
                intento += 1
                continue
            break

        if EXCESO_MSG in resp.text:
            log.warning(f"{contexto}: demasiados resultados")
            return [], False, True

        if CAPTCHA_MSG in resp.text:
            raise RuntimeError(
                "BOE pidió una verificación de seguridad (probablemente por "
                "muchas consultas seguidas) y siguió pidiéndola después de "
                f"{self.CAPTCHA_MAX_REINTENTOS} reintentos con espera de por "
                "medio. Esperá más tiempo y volvé a intentar - si seguía "
                "sincronizando en silencio, los datos hubieran quedado "
                "incompletos sin ningún aviso."
            )

        lotes, siguiente_href = self._parse_listado(resp.text, provincia_cod, estado_cod)
        self._wait()

        paginas_seguidas = 0
        while siguiente_href and paginas_seguidas < self.MAX_PAGINAS_SEGUIDAS:
            intento = 0
            while True:
                try:
                    resp = self.session.get(f"{BASE}/{siguiente_href}", timeout=30)
                    resp.raise_for_status()
                except requests.RequestException as e:
                    log.warning(f"Error siguiendo paginacion de {contexto}: {e}")
                    resp = None
                    break
                if CAPTCHA_MSG in resp.text and self._reintentar_por_captcha(intento, contexto):
                    intento += 1
                    continue
                break
            if resp is None:
                break
            if CAPTCHA_MSG in resp.text:
                raise RuntimeError(
                    "BOE pidió una verificación de seguridad (probablemente por "
                    "muchas consultas seguidas) y siguió pidiéndola después de "
                    f"{self.CAPTCHA_MAX_REINTENTOS} reintentos con espera de por "
                    "medio. Esperá más tiempo y volvé a intentar - si seguía "
                    "sincronizando en silencio, los datos hubieran quedado "
                    "incompletos sin ningún aviso."
                )
            nuevos, siguiente_href = self._parse_listado(resp.text, provincia_cod, estado_cod)
            lotes.extend(nuevos)
            self._wait()
            paginas_seguidas += 1

        if siguiente_href:
            log.warning(
                f"provincia={PROVINCIAS.get(provincia_cod, provincia_cod)} "
                f"estado={ESTADOS.get(estado_cod, estado_cod)} fecha={fecha_desde}..{fecha_hasta}: "
                f"se corto la paginacion en {self.MAX_PAGINAS_SEGUIDAS} paginas seguidas, "
                f"pueden quedar resultados sin traer"
            )

        return lotes, False, False

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
                    # Para PC/FS, BOE muestra en este parrafo un sub-estado
                    # mas especifico por cada subasta (confirmado en vivo:
                    # "Pendiente de finalizacion y devolucion de depositos
                    # con reserva" para TODOS los resultados de PC, "Finalizada
                    # y depositos con reserva devueltos" para TODOS los de FS -
                    # nunca literalmente "Concluida en Portal de Subastas" ni
                    # "Finalizada por Autoridad Gestora"). Dejar que esto pise
                    # estado_txt hacia que ninguna fila quedara guardada con
                    # esos dos textos exactos, asi que el filtro Estado para
                    # esas dos categorias nunca traia resultados aunque los
                    # datos si estuvieran ahi. Para PC/FS se mantiene fijo el
                    # nombre de categoria (coincide con las opciones reales
                    # del filtro); para el resto de estados se sigue usando
                    # el texto real de la pagina, que ahi si coincide.
                    if estado_cod not in ESTADOS_HISTORICOS:
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

        # buscar por .string (que exige que el <a> tenga un solo hijo de
        # texto) nunca encontraba este link: el texto real es
        # '<abbr title="Página">Pág.</abbr> siguiente', con un hijo <abbr>
        # de por medio - .string da None ahi (confirmado en vivo). Por eso
        # se busca por el texto COMPLETO del link (incluye los hijos) en
        # vez de por .string.
        siguiente_href = None
        for a in soup.find_all("a"):
            if "siguiente" in a.get_text(strip=True).lower():
                siguiente_href = a.get("href")
                break
        return lotes, siguiente_href

    # ---------- detalle ----------

    def detalle(self, id_sub: str):
        """Trae los campos financieros + el bien de una subasta de un solo
        lote. Para subastas con varios lotes, cada uno se subasta por
        separado con su propio valor/tasacion/puja minima -usar
        detalle_lotes() en su lugar (ver ese metodo)."""
        datos = {}
        datos.update(self._detalle_financiero(id_sub))
        datos.update(self._detalle_bien(id_sub))
        self._wait()
        return datos

    def detalle_lotes(self, id_sub: str):
        """Devuelve una lista con un dict por cada lote de la subasta (la
        mayoria de subastas tienen un solo lote, esa lista tiene 1 elemento).

        Confirmado en vivo contra subastas.boe.es: cuando una subasta tiene
        varios lotes se subastan por separado, cada uno con su propio valor
        de subasta / tasacion / puja minima / tramos / deposito / descripcion
        / direccion, a los que solo se accede pasando idLote=1..N a
        detalleSubasta.php?ver=3 - sin ese parametro esos campos financieros
        quedan en texto generico ("Ver valor de subasta en cada lote...") en
        vez del numero real. Antes esto se resolvia consultando una sola vez
        sin idLote, lo que en subastas multi-lote traia el bien del lote 1
        nada mas y ningun valor financiero real."""
        comunes = self._detalle_financiero(id_sub)
        self._wait()

        if not comunes:
            # Fallo de red/timeout en _detalle_financiero (ya devuelve {} en
            # ese caso, ver el except mas abajo en ese metodo). Antes esto
            # caia al default n=1 y armaba una fila con el id SIN sufijo de
            # lote, que en una subasta multi-lote ya sincronizada antes
            # quedaba como una fila extra huerfana (ni pisa "-L1" ni "-L2",
            # los dos tienen id distinto) en vez de simplemente reintentar
            # en la proxima sincronizacion.
            log.warning(f"Sin datos financieros para {id_sub}, se salta esta vuelta (se reintenta en la proxima sync)")
            return []

        # El acreedor es de la subasta entera (no cambia por lote), asi que
        # se pide una vez sola aca en vez de repetirlo en cada vuelta del
        # for de abajo - evita pedidos de mas en subastas multi-lote. Va
        # DESPUES del chequeo de arriba: si _detalle_financiero ya fallo,
        # comunes es {} y hay que devolver [] tal cual, sin agregarle nada
        # que lo haga parecer no-vacio.
        comunes["acreedor"] = self._detalle_acreedor(id_sub)
        self._wait()

        # Igual que el acreedor: la pagina de pujas (ver=5) trae TODOS los
        # lotes de la subasta en un solo pedido (una tabla con un importe
        # por numero de lote si hay varios, o un unico importe suelto si
        # hay uno solo - ver _detalle_pujas), asi que se pide una sola vez
        # aca en vez de una vez por lote.
        pujas = self._detalle_pujas(id_sub)
        self._wait()

        valor_lotes = str(comunes.get("lotes", "")).lower()
        n = 1
        m = re.search(r"\d+", valor_lotes)
        if "sin lotes" not in valor_lotes and m:
            n = int(m.group(0))

        resultado = []
        for i in range(1, n + 1):
            bien = self._detalle_bien(id_sub, id_lote=i if n > 1 else None)
            self._wait()
            fila = dict(comunes)
            fila.update({
                "tipo_bien": bien.get("tipo_bien", ""),
                "descripcion": bien.get("descripcion", ""),
                "referencia_catastral": bien.get("referencia_catastral", ""),
                "direccion": bien.get("direccion", ""),
                "codigo_postal": bien.get("codigo_postal", ""),
                "localidad": bien.get("localidad", ""),
                "provincia": bien.get("provincia", ""),
                "marca": bien.get("marca", ""),
                "modelo": bien.get("modelo", ""),
                "matricula": bien.get("matricula", ""),
                # pedido explicito del cliente: importe de la puja ganadora
                # (la mas alta realizada) de cada subasta concluida/finalizada.
                # pujas.get(i) da None tanto si nadie oferto ("Sin puja") como
                # si la subasta ni siquiera tiene ese dato todavia disponible -
                # en los dos casos corresponde dejar la columna vacia.
                "importe_puja_ganadora": pujas.get(i),
            })
            if n > 1:
                for campo, campo_lote in [
                    ("valor_subasta", "valor_subasta_lote"),
                    ("valor_tasacion", "valor_tasacion_lote"),
                    ("puja_minima", "puja_minima_lote"),
                    ("tramos_entre_pujas", "tramos_entre_pujas_lote"),
                    ("importe_deposito", "importe_deposito_lote"),
                ]:
                    if bien.get(campo_lote) is not None:
                        fila[campo] = bien[campo_lote]
                fila["id"] = f"{id_sub}-L{i}"
                fila["numero_lote"] = i
            resultado.append(fila)
        return resultado

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
            "fecha_inicio": _quitar_iso(campos.get("Fecha de inicio", "")),
            "fecha_conclusion": _quitar_iso(campos.get("Fecha de conclusión", "")),
            "cantidad_reclamada": _money_to_float(campos.get("Cantidad reclamada")),
            "valor_subasta": _money_to_float(campos.get("Valor subasta")),
            "valor_tasacion": _money_to_float(campos.get("Tasación")),
            "puja_minima": _money_to_float(campos.get("Puja mínima")),
            "tramos_entre_pujas": _money_to_float(campos.get("Tramos entre pujas")),
            "importe_deposito": _money_to_float(campos.get("Importe del depósito")),
            "lotes": campos.get("Lotes", ""),
        }

    def _detalle_acreedor(self, id_sub: str):
        """El acreedor (quien reclama la deuda) esta en una pagina APARTE
        de la ficha (ver=4), con su propio bloque "Acreedor" - no aparece
        en ver=3 (bien) ni en la pagina sin ver (financiero). Confirmado
        en vivo contra subastas.boe.es: no todas las subastas lo tienen
        (BOE lo deja vacio en muchas), y cuando esta, el campo que importa
        para el cliente es el Nombre.

        Es un dato de la SUBASTA entera, no de cada lote (a diferencia de
        _detalle_bien), asi que se pide una sola vez por subasta - no hace
        falta repetirlo por cada idLote."""
        try:
            resp = self.session.get(DETALLE_URL, params={"idSub": id_sub, "ver": 4}, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning(f"No se pudo obtener el acreedor de {id_sub}: {e}")
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        bloque = soup.find("h3", string=re.compile(r"^Acreedor"))
        if not bloque:
            return ""
        tabla = bloque.find_next("table")
        if not tabla:
            return ""
        return self._tabla_a_dict(tabla).get("Nombre", "")

    def _detalle_pujas(self, id_sub: str):
        """Importe de la puja ganadora (la mas alta realizada) de cada
        lote de una subasta - pagina aparte (ver=5), pedido explicito del
        cliente para subastas concluidas/finalizadas. Confirmado en vivo
        contra subastas.boe.es que esta pagina tiene 2 formatos distintos
        segun la subasta tenga uno o varios lotes:
          - un solo lote: un unico importe suelto bajo el titulo "Puja
            maxima de la subasta" (o "Sin puja" si nadie oferto).
          - varios lotes: una tabla "Pujas maximas" con una fila por
            numero de lote.
        Devuelve {numero_lote: importe_o_None}; con un solo lote queda
        bajo la clave 1 (mismo criterio que id_lote=None en el resto del
        scraper). Es un dato de la SUBASTA entera, no hace falta pedirlo
        una vez por lote."""
        try:
            resp = self.session.get(DETALLE_URL, params={"idSub": id_sub, "ver": 5}, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning(f"No se pudo obtener las pujas de {id_sub}: {e}")
            return {}

        soup = BeautifulSoup(resp.text, "html.parser")
        pujas = {}

        tabla = soup.find("table")
        if tabla and tabla.find("th", id="lote"):
            for tr in tabla.select("tbody tr"):
                tds = tr.find_all("td")
                if len(tds) < 2:
                    continue
                num_m = re.search(r"\d+", _clean(tds[0].get_text()))
                if num_m:
                    pujas[int(num_m.group(0))] = _money_to_float(tds[1].get_text())
            return pujas

        h4 = soup.find("h4", string=re.compile(r"^Puja m.xima de la subasta"))
        if h4:
            valor = h4.find_next("strong")
            if valor:
                pujas[1] = _money_to_float(valor.get_text())
        return pujas

    def _detalle_bien(self, id_sub: str, id_lote=None):
        params = {"idSub": id_sub, "ver": 3}
        if id_lote is not None:
            params["idLote"] = id_lote
        try:
            resp = self.session.get(DETALLE_URL, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning(f"No se pudo obtener detalle de bien de {id_sub} (lote {id_lote}): {e}")
            return {}

        soup = BeautifulSoup(resp.text, "html.parser")

        # La numeracion del primer bien varia segun el tipo de subasta: la
        # mayoria arranca en "Bien 1", pero notarial hipotecaria y algunas
        # de recaudacion tributaria arrancan en "Bien 0" (confirmado en
        # vivo). Sin este fallback esos lotes quedaban sin tipo_bien ni
        # descripcion.
        h4 = soup.find("h4", string=re.compile(r"^Bien \d+"))
        tipo_bien = ""
        if h4:
            m = re.search(r"Bien \d+ - ([^(]+)", h4.get_text())
            if m:
                tipo_bien = m.group(1).strip()

        # La pagina ver=3 tiene 2 tablas separadas: una ANTES del titulo
        # "Bien N" con los campos financieros del lote (Valor Subasta, Puja
        # minima, etc.) y otra DESPUES con los datos del bien (Descripcion,
        # Referencia catastral, etc.) - confirmado en vivo. Parsear toda la
        # pagina de una junta ambas sin pisarse (no comparten nombres de
        # campo).
        campos = self._tabla_a_dict(soup)

        return {
            "tipo_bien": tipo_bien,
            "descripcion": campos.get("Descripción", "").replace("Descripción:", "").strip(),
            "referencia_catastral": campos.get("Referencia catastral", ""),
            "direccion": campos.get("Dirección", ""),
            "codigo_postal": campos.get("Código Postal", ""),
            "localidad": campos.get("Localidad", ""),
            "provincia": campos.get("Provincia", ""),
            "marca": campos.get("Marca", ""),
            "modelo": campos.get("Modelo", ""),
            "matricula": campos.get("Matrícula", campos.get("Matricula", "")),
            # Esta pagina (ver=3) trae el valor REAL por lote cuando la
            # subasta tiene varios lotes -en _detalle_financiero esos campos
            # quedan en texto tipo "Ver valor de subasta en cada lote" para
            # subastas asi. Con un solo lote coincide con lo que ya trae
            # _detalle_financiero, por eso detalle_lotes() solo los usa
            # cuando hay mas de un lote.
            "valor_subasta_lote": _money_to_float(campos.get("Valor Subasta")),
            "valor_tasacion_lote": _money_to_float(campos.get("Valor de tasación")),
            "puja_minima_lote": _money_to_float(campos.get("Puja mínima")),
            "tramos_entre_pujas_lote": _money_to_float(campos.get("Tramos entre pujas")),
            "importe_deposito_lote": _money_to_float(campos.get("Importe del depósito")),
        }

    # ---------- orquestacion ----------

    def sync_estado(self, estado_cod: str, provincias=None, con_detalle=True, limite_por_combo=None):
        """Recorre todas las provincias para un estado dado. Uso diario: PU/EJ."""
        provincias = provincias or list(PROVINCIAS.keys())
        todos = []
        for prov_cod in provincias:
            lotes, _, _ = self.buscar(prov_cod, estado_cod)
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
    lotes, hay_mas, excesivo = scraper.buscar("28", "EJ")
    print(f"{len(lotes)} lotes encontrados en el listado (hay mas paginas: {hay_mas})")
    if lotes:
        primero = lotes[0]
        print("Primero (resumen):", primero)
        extra = scraper.detalle(primero["id"])
        print("Detalle completo:", extra)
