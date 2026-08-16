"""
Auctions Broker - servidor local (Flask).

No es un sitio publico: corre en 127.0.0.1 y lo abre pywebview como
ventana nativa (ver main.py). BOE Subastas + Seguridad Social.
Dos sincronizaciones separadas, cada una con su propio estado:
- rapida (boton "Actualizar", y automatica una vez por dia al abrir la app)
- historica (boton "Descargar histórico completo", resumible, corre en
  segundo plano mientras la app este abierta)
"""

import io
import logging
import threading
from datetime import datetime, timedelta

from flask import Flask, jsonify, request, send_file, render_template

import db
import export
from ingest import (
    sync_boe, sync_seg_social, sync_boe_historico, sync_seg_social_historico,
    PRINCIPALES_PROVINCIAS, LIMITE_POR_COMBO_DEFECTO,
)
from scraper.boe import ESTADOS_ACTIVOS as ESTADO_ACTIVOS_COD

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("app")

app = Flask(__name__)

sync_lock = threading.Lock()
sync_status = {"en_progreso": False, "mensaje": "", "lotes_procesados": 0}

historico_lock = threading.Lock()
historico_status = {"en_progreso": False, "mensaje": "", "lotes_procesados": 0}


def _sync_en_segundo_plano():
    def progreso(msg):
        sync_status["mensaje"] = msg

    try:
        total_boe = sync_boe(
            provincias=PRINCIPALES_PROVINCIAS,
            estados=ESTADO_ACTIVOS_COD,
            con_detalle=True,
            limite_por_combo=LIMITE_POR_COMBO_DEFECTO,
            progreso=progreso,
        )
        total_ss = sync_seg_social(
            provincias=PRINCIPALES_PROVINCIAS,
            con_detalle=True,
            limite_por_combo=LIMITE_POR_COMBO_DEFECTO,
            progreso=progreso,
        )
        total = total_boe + total_ss
        sync_status["lotes_procesados"] = total
        sync_status["mensaje"] = f"Listo - {total} lotes procesados"
    except Exception as e:
        log.exception("Error durante la sincronizacion")
        sync_status["mensaje"] = f"Error: {e}"
    finally:
        sync_status["en_progreso"] = False


def iniciar_sync_si_hace_falta():
    """Se llama al abrir la app (ver main.py). Si la ultima sincronizacion
    rapida tiene mas de un dia (o nunca se hizo), la arranca sola en
    segundo plano, sin que el usuario tenga que tocar "Actualizar"."""
    boe = db.get_sync_state("BOE Subastas")
    if boe and boe["last_full_sync"]:
        ultima = datetime.fromisoformat(boe["last_full_sync"])
        if datetime.now() - ultima < timedelta(days=1):
            return
    with sync_lock:
        if sync_status["en_progreso"]:
            return
        sync_status["en_progreso"] = True
        sync_status["mensaje"] = "Arrancando sincronización automática del día..."
        sync_status["lotes_procesados"] = 0
        threading.Thread(target=_sync_en_segundo_plano, daemon=True).start()


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
        fuente=request.args.get("fuente") or None,
        estado=request.args.get("estado") or None,
        tipo_subasta=request.args.get("tipo_subasta") or None,
        tipo_bien=request.args.get("tipo_bien") or None,
        provincia=request.args.get("provincia") or None,
        texto=request.args.get("texto") or None,
    )
    return jsonify(filas)


@app.route("/api/opciones")
def api_opciones():
    """Valores distintos ya presentes en la base, para llenar los filtros."""
    filas = db.query_lotes()
    return jsonify({
        "fuentes": sorted({r["fuente"] for r in filas if r["fuente"]}),
        "tipos_subasta": sorted({r["tipo_subasta"] for r in filas if r["tipo_subasta"]}),
        "tipos_bien": sorted({r["tipo_bien"] for r in filas if r["tipo_bien"]}),
        "provincias": sorted({r["provincia"] for r in filas if r["provincia"]}),
    })


@app.route("/api/export")
def api_export():
    filas = db.query_lotes(
        fuente=request.args.get("fuente") or None,
        estado=request.args.get("estado") or None,
        tipo_subasta=request.args.get("tipo_subasta") or None,
        tipo_bien=request.args.get("tipo_bien") or None,
        provincia=request.args.get("provincia") or None,
        texto=request.args.get("texto") or None,
    )
    # Se arma en memoria (BytesIO) en vez de pasar por un archivo temporal
    # en disco: en el .exe de Windows un antivirus o el propio SO puede
    # tener el archivo brevemente bloqueado justo cuando send_file intenta
    # leerlo de vuelta, y el navegador termina descargando algo vacio o
    # incompleto con extension .xlsx pero que no es un Excel valido.
    buffer = io.BytesIO()
    export.exportar(filas, buffer)
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name="subastas-filtradas.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/sync", methods=["POST"])
def api_sync():
    """Arranca la sincronizacion en un hilo aparte y devuelve al toque.
    El frontend consulta el avance con /api/estado."""
    with sync_lock:
        if sync_status["en_progreso"]:
            return jsonify({"status": "ya_en_progreso"}), 409
        sync_status["en_progreso"] = True
        sync_status["mensaje"] = "Arrancando..."
        sync_status["lotes_procesados"] = 0
        threading.Thread(target=_sync_en_segundo_plano, daemon=True).start()
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
    })


if __name__ == "__main__":
    db.init_db()
    app.run(host="127.0.0.1", port=5057, debug=True)
