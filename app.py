"""
Auctions Broker - servidor local (Flask).

No es un sitio publico: corre en 127.0.0.1 y lo abre pywebview como
ventana nativa (ver main.py). BOE Subastas + Seguridad Social.
Dos sincronizaciones separadas, cada una con su propio estado:
- rapida (boton "Actualizar", acotada a la provincia/fuente tildada en el filtro si hay alguna elegida)
- historica (boton "Descargar histórico completo", resumible, corre en
  segundo plano mientras la app este abierta)
"""

import logging
import threading

from flask import Flask, jsonify, request, render_template

import db
from ingest import (
    sync_boe, sync_seg_social, sync_boe_historico, sync_seg_social_historico,
    PRINCIPALES_PROVINCIAS, LIMITE_POR_COMBO_DEFECTO,
)
from scraper.boe import ESTADOS_ACTIVOS as ESTADO_ACTIVOS_COD, PROVINCIAS as PROVINCIAS_BOE
from scraper.seg_social import SegSocialScraper, BASE as SS_BASE
import requests

COD_POR_NOMBRE_PROVINCIA = {nombre: cod for cod, nombre in PROVINCIAS_BOE.items()}

# Se muestra en el pie de la pantalla (ver templates/index.html) - subir
# a mano en cada tanda de cambios que se le manda al cliente. Sin esto,
# tanto nosotros como el cliente terminabamos adivinando que version
# estaba corriendo en base a pistas indirectas (formato de los mensajes
# de log, si aparecia tal boton o no) - confirmado en vivo: mas de una
# vez hizo falta reconstruir a partir de esas pistas si el cliente estaba
# probando la version mas nueva o una intermedia.
APP_VERSION = "2026-09-01.1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("app")

app = Flask(__name__)

sync_lock = threading.Lock()
sync_status = {"en_progreso": False, "mensaje": "", "lotes_procesados": 0}

historico_lock = threading.Lock()
historico_status = {"en_progreso": False, "mensaje": "", "lotes_procesados": 0}


def _sync_en_segundo_plano(provincias=None, fuentes=None):
    """provincias: codigos de provincia a sincronizar, o None para las 10
    principales de siempre. fuentes: subconjunto de {"BOE Subastas",
    "Seguridad Social"}, o None/vacio para las dos.

    Cuando el usuario acota a provincias puntuales (tildando el filtro de
    Provincia antes de tocar "Actualizar"), se sincroniza sin el limite de
    LIMITE_POR_COMBO_DEFECTO lotes por combinacion: ese limite existe para
    que barrer las 10 provincias principales no tarde de mas, pero si son
    pocas provincias puntuales no hace falta cortar resultados."""
    def progreso(msg):
        sync_status["mensaje"] = msg

    try:
        # Sacar el limite de lotes por combinacion solo vale la pena si son
        # pocas provincias puntuales - con muchas tildadas a la vez (nada
        # impide tildar 20+ en el filtro), sin limite podria tardar mas de
        # una hora y no hay forma de cancelar una sync en curso.
        acotado = bool(provincias) and len(provincias) <= 3
        provincias_sync = provincias or PRINCIPALES_PROVINCIAS
        limite = None if acotado else LIMITE_POR_COMBO_DEFECTO
        total = 0
        if not fuentes or "BOE Subastas" in fuentes:
            total += sync_boe(
                provincias=provincias_sync,
                estados=ESTADO_ACTIVOS_COD,
                con_detalle=True,
                limite_por_combo=limite,
                progreso=progreso,
            )
        if not fuentes or "Seguridad Social" in fuentes:
            total += sync_seg_social(
                provincias=provincias_sync,
                con_detalle=True,
                limite_por_combo=limite,
                progreso=progreso,
            )
        sync_status["lotes_procesados"] = total
        sync_status["mensaje"] = f"Listo - {total} lotes procesados"
    except Exception as e:
        log.exception("Error durante la sincronizacion")
        sync_status["mensaje"] = f"Error: {e}"
    finally:
        sync_status["en_progreso"] = False


def _historico_en_segundo_plano(fuentes=None, provincias=None):
    """fuentes: subconjunto de {"BOE Subastas", "Seguridad Social"}, o
    None/vacio para las dos - mismo criterio que _sync_en_segundo_plano.
    Antes este botón siempre bajaba las dos fuentes sin importar que
    filtro de Fuente tuviera tildado el usuario en pantalla (ese filtro
    solo afecta que se muestra en la tabla, no que se descarga) -
    confirmado con el cliente, que tenia solo BOE Subastas tildado en el
    filtro y de todos modos le salio un error de Seguridad Social.

    provincias: igual que en _sync_en_segundo_plano, si el usuario tildo
    provincias puntuales en el filtro se acota el barrido historico a
    esas nada mas - pedido del cliente para poder probar con una sola
    provincia (ej. Malaga) en vez de esperar las 52 completas, que ahora
    con el rango desde 2014 y la paginacion real de BOE puede tardar
    mucho mas.

    Cada fuente corre en su propio try/except: antes, si Seguridad Social
    fallaba (ej. un corte de red momentaneo resolviendo su dominio), la
    excepcion cortaba TODO el barrido y el avance de BOE de esa misma
    pasada se perdia sin guardar mensaje de progreso - confirmado en vivo
    con el cliente (age fallo de DNS en w6.seg-social.es corto el barrido
    completo). Ahora una fuente fallando no le impide a la otra terminar."""
    def progreso(msg):
        historico_status["mensaje"] = msg

    total = 0
    errores = []
    if not fuentes or "BOE Subastas" in fuentes:
        try:
            total += sync_boe_historico(provincias=provincias, progreso=progreso)
        except Exception as e:
            log.exception("Error durante el barrido histórico de BOE")
            errores.append(f"BOE: {e}")
    if not fuentes or "Seguridad Social" in fuentes:
        try:
            total += sync_seg_social_historico(provincias=provincias, progreso=progreso)
        except Exception as e:
            log.exception("Error durante el barrido histórico de Seguridad Social")
            errores.append(f"Seguridad Social: {e}")

    historico_status["lotes_procesados"] = total
    if errores:
        historico_status["mensaje"] = f"Pasada con errores ({total} lotes nuevos) - " + " | ".join(errores)
    else:
        historico_status["mensaje"] = f"Pasada completa - {total} lotes nuevos"
    historico_status["en_progreso"] = False


@app.route("/")
def index():
    return render_template("index.html", version=APP_VERSION)


@app.route("/ss/detalle/<emb_id>")
def ss_detalle(emb_id):
    """Proxy al detalle real de un lote de Seguridad Social.

    El sitio exige haber pasado por una busqueda de verdad en la MISMA
    sesion antes de mostrar el detalle puntual de un lote - ni siquiera
    alcanza con visitar la pagina de busqueda sin buscar nada (confirmado
    en vivo). Por eso el Id de esta fuente no podia enlazar directo desde
    el navegador del usuario (una sesion nueva sin ese paso previo). Este
    endpoint hace esa busqueda de "calentamiento" del lado del servidor
    -donde si podemos armar la sesion correcta, es la misma logica que ya
    usa el scraper- y devuelve la pagina de detalle real tal cual la manda
    seg-social.es, para que el link de la tabla lleve a la subasta
    original de verdad (pedido explicito del cliente), no a la busqueda
    generica que se usaba antes como alternativa."""
    scraper = SegSocialScraper()
    try:
        # Cualquier busqueda alcanza para habilitar la sesion - no importan
        # los resultados, solo se usa para dejar la sesion en el estado que
        # el sitio espera antes de mostrar un detalle.
        scraper.buscar(["28"])
        resp = scraper.session.get(
            SS_BASE, params={"opcion": 13, "EMB_ID": emb_id, "opcion2": 1, "tipoOperacion": 0}, timeout=15
        )
        resp.encoding = "iso-8859-1"
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"No se pudo cargar el detalle de Seguridad Social EMB_ID={emb_id}: {e}")
        return "No se pudo cargar el detalle - intentá de nuevo en un momento.", 502

    # `resp.text` ya viene decodificado como texto Python (unicode) gracias
    # al encoding de arriba - Flask lo va a mandar como UTF-8 por defecto,
    # asi que el <meta charset=iso-8859-1> que trae la pagina original
    # quedaria mintiendo sobre la codificacion real de la respuesta.
    # <base href> resuelve las rutas relativas de CSS/imagenes de la
    # pagina (ej. "/subastas/suba/subasrc/css/NML.css") contra el dominio
    # real en vez del nuestro (127.0.0.1), para que se vea igual que en
    # seg-social.es.
    html = resp.text.replace("charset=iso-8859-1", "charset=utf-8", 1)
    html = html.replace("<head>", '<head><base href="https://w6.seg-social.es/">', 1)
    return html


TAMANIO_PAGINA = 100


@app.route("/api/lotes")
def api_lotes():
    """Devuelve una pagina de TAMANIO_PAGINA filas (parametro ?pagina=,
    1-indexado) en vez de mandar todo de una - antes mandaba hasta 500
    filas de un saque para pintar la tabla, y con el historico completo
    pasando los 10 mil lotes eso hacia que el navegador reconstruyera la
    tabla entera en cada tick del poll (cada 2-3 segundos mientras corre
    una sync) hasta quedarse sin memoria (confirmado en vivo por el
    cliente: WebView2 tiro "Codigo de error: Out of Memory"). Paginado de
    a 100 evita ese problema Y hace que scrollear miles de filas de una
    sola tirada deje de ser necesario (pedido del cliente mientras
    probaba el historico completo). El "resumen" (para las tarjetas KPI)
    se calcula aparte sobre el conjunto COMPLETO, no solo sobre esta
    pagina - ver db.resumen_lotes()."""
    filtros = dict(
        fuente=request.args.getlist("fuente") or None,
        estado=request.args.getlist("estado") or None,
        categoria_subasta=request.args.getlist("categoria_subasta") or None,
        tipo_bien=request.args.getlist("tipo_bien") or None,
        provincia=request.args.getlist("provincia") or None,
        texto=request.args.get("texto") or None,
        fecha_inicio_desde=request.args.get("fecha_inicio_desde") or None,
        fecha_inicio_hasta=request.args.get("fecha_inicio_hasta") or None,
        fecha_conclusion_desde=request.args.get("fecha_conclusion_desde") or None,
        fecha_conclusion_hasta=request.args.get("fecha_conclusion_hasta") or None,
    )
    pagina = max(1, request.args.get("pagina", 1, type=int))
    resumen = db.resumen_lotes(**filtros)
    total_paginas = max(1, (resumen["total"] + TAMANIO_PAGINA - 1) // TAMANIO_PAGINA)
    pagina = min(pagina, total_paginas)
    filas = db.query_lotes(limite=TAMANIO_PAGINA, offset=(pagina - 1) * TAMANIO_PAGINA, **filtros)
    return jsonify({
        "lotes": filas,
        "resumen": resumen,
        "pagina": pagina,
        "total_paginas": total_paginas,
        "tamanio_pagina": TAMANIO_PAGINA,
    })


@app.route("/api/sync", methods=["POST"])
def api_sync():
    """Arranca la sincronizacion en un hilo aparte y devuelve al toque.
    El frontend consulta el avance con /api/estado. Si el usuario tildo
    provincias puntuales en el filtro antes de tocar "Actualizar", solo
    sincroniza esas (y sin el limite de lotes por combinacion - ver
    _sync_en_segundo_plano)."""
    with sync_lock:
        if sync_status["en_progreso"]:
            return jsonify({"status": "ya_en_progreso"}), 409
        nombres_provincia = request.args.getlist("provincia")
        provincias = [COD_POR_NOMBRE_PROVINCIA[n] for n in nombres_provincia if n in COD_POR_NOMBRE_PROVINCIA] or None
        fuentes = request.args.getlist("fuente") or None
        sync_status["en_progreso"] = True
        sync_status["mensaje"] = "Arrancando..."
        sync_status["lotes_procesados"] = 0
        threading.Thread(
            target=_sync_en_segundo_plano,
            kwargs={"provincias": provincias, "fuentes": fuentes},
            daemon=True,
        ).start()
    return jsonify({"status": "iniciado"})


@app.route("/api/sync_historico", methods=["POST"])
def api_sync_historico():
    """Arranca (o retoma) el barrido histórico completo en un hilo aparte.
    Puede tardar horas: se puede cerrar la app y volver a tocar el botón
    despues, retoma justo donde quedó (ver sync_state en db.py).

    Igual que /api/sync, respeta el filtro de Fuente Y de Provincia
    tildados en pantalla (antes bajaba BOE y Seguridad Social siempre
    para las 52 provincias, sin importar ningun filtro - pedido del
    cliente: poder probar con una sola provincia en vez de esperar el
    barrido completo)."""
    with historico_lock:
        if historico_status["en_progreso"]:
            return jsonify({"status": "ya_en_progreso"}), 409
        fuentes = request.args.getlist("fuente") or None
        nombres_provincia = request.args.getlist("provincia")
        provincias = [COD_POR_NOMBRE_PROVINCIA[n] for n in nombres_provincia if n in COD_POR_NOMBRE_PROVINCIA] or None
        historico_status["en_progreso"] = True
        historico_status["mensaje"] = "Arrancando barrido histórico..."
        historico_status["lotes_procesados"] = 0
        threading.Thread(
            target=_historico_en_segundo_plano,
            kwargs={"fuentes": fuentes, "provincias": provincias},
            daemon=True,
        ).start()
    return jsonify({"status": "iniciado"})


@app.route("/api/estado")
def api_estado():
    boe = db.get_sync_state("BOE Subastas")
    ss = db.get_sync_state("Seguridad Social")
    boe_hist = db.get_sync_state("BOE Subastas Historico")
    ss_hist = db.get_sync_state("Seguridad Social Historico")
    total = db.count_lotes()
    return jsonify({
        "total_lotes": total,
        "boe_ultima_sync": boe["last_full_sync"] if boe else None,
        "seg_social_ultima_sync": ss["last_full_sync"] if ss else None,
        "sincronizando": sync_status["en_progreso"],
        "mensaje_sync": sync_status["mensaje"],
        "historico_en_progreso": historico_status["en_progreso"],
        "historico_mensaje": historico_status["mensaje"],
        "historico_boe_ultima_pasada": boe_hist["last_full_sync"] if boe_hist else None,
        "historico_seg_social_ultima_pasada": ss_hist["last_full_sync"] if ss_hist else None,
        # last_combo queda guardado apenas se termina la PRIMERA combinacion
        # provincia+estado, mucho antes que last_full_sync (que recien se
        # escribe al completar el barrido entero, algo que puede tardar
        # dias). Sin esto el frontend no tenia forma de saber que ya habia
        # avance real guardado de una sesion anterior.
        "historico_boe_con_avance": bool(boe_hist and boe_hist["last_combo"]),
        "historico_seg_social_con_avance": bool(ss_hist and ss_hist["last_combo"]),
    })


if __name__ == "__main__":
    db.init_db()
    app.run(host="127.0.0.1", port=5057, debug=True)
