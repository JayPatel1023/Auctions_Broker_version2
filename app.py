"""
Auctions Broker - servidor local (Flask).

No es un sitio publico: corre en 127.0.0.1 y lo abre pywebview como
ventana nativa (ver main.py). BOE Subastas + Seguridad Social, sincronizacion
manual con el boton "Actualizar" (la sincronizacion automatica diaria
es el siguiente paso).
"""

import logging
import os
import tempfile
import threading

from flask import Flask, jsonify, request, send_file, render_template

import db
import export
from ingest import sync_boe, sync_seg_social, PRINCIPALES_PROVINCIAS, LIMITE_POR_COMBO_DEFECTO

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("app")

app = Flask(__name__)

ESTADO_ACTIVOS_COD = ["PU", "EJ"]

sync_lock = threading.Lock()
sync_status = {"en_progreso": False, "mensaje": "", "lotes_procesados": 0}


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
    # nombre unico por pedido: si se hacen varios exports seguidos (o en
    # paralelo) un nombre fijo puede pisarse a si mismo a mitad de escritura
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", prefix="subastas-")
    os.close(fd)
    export.exportar(filas, tmp)
    return send_file(
        tmp,
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


@app.route("/api/estado")
def api_estado():
    boe = db.get_sync_state("BOE Subastas")
    ss = db.get_sync_state("Seguridad Social")
    total = len(db.query_lotes())
    return jsonify({
        "total_lotes": total,
        "boe_ultima_sync": boe["last_full_sync"] if boe else None,
        "seg_social_ultima_sync": ss["last_full_sync"] if ss else None,
        "sincronizando": sync_status["en_progreso"],
        "mensaje_sync": sync_status["mensaje"],
    })


if __name__ == "__main__":
    db.init_db()
    app.run(host="127.0.0.1", port=5057, debug=True)
