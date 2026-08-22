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

COD_POR_NOMBRE_PROVINCIA = {nombre: cod for cod, nombre in PROVINCIAS_BOE.items()}

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


def _historico_en_segundo_plano():
    def progreso(msg):
        historico_status["mensaje"] = msg

    try:
        total_boe = sync_boe_historico(progreso=progreso)
        total_ss = sync_seg_social_historico(progreso=progreso)
        total = total_boe + total_ss
        historico_status["lotes_procesados"] = total
        historico_status["mensaje"] = f"Pasada completa - {total} lotes nuevos"
    except Exception as e:
        log.exception("Error durante el barrido histórico")
        historico_status["mensaje"] = f"Error: {e}"
    finally:
        historico_status["en_progreso"] = False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/lotes")
def api_lotes():
    filas = db.query_lotes(
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
    return jsonify(filas)


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
    despues, retoma justo donde quedó (ver sync_state en db.py)."""
    with historico_lock:
        if historico_status["en_progreso"]:
            return jsonify({"status": "ya_en_progreso"}), 409
        historico_status["en_progreso"] = True
        historico_status["mensaje"] = "Arrancando barrido histórico..."
        historico_status["lotes_procesados"] = 0
        threading.Thread(target=_historico_en_segundo_plano, daemon=True).start()
    return jsonify({"status": "iniciado"})


@app.route("/api/estado")
def api_estado():
    boe = db.get_sync_state("BOE Subastas")
    ss = db.get_sync_state("Seguridad Social")
    boe_hist = db.get_sync_state("BOE Subastas Historico")
    ss_hist = db.get_sync_state("Seguridad Social Historico")
    total = len(db.query_lotes())
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
