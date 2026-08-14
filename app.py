"""
Auctions Broker - servidor local (Flask).

No es un sitio publico: corre en 127.0.0.1 y lo abre pywebview como
ventana nativa (ver main.py). Fase 1: solo BOE Subastas, sincronizacion
manual con el boton "Actualizar" (la sincronizacion automatica diaria
es Fase 2).
"""

import logging
import os
import tempfile

from flask import Flask, jsonify, request, send_file, render_template

import db
import export
from ingest import sync_boe
from scraper.boe import PROVINCIAS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("app")

app = Flask(__name__)

ESTADO_ACTIVOS_COD = ["PU", "EJ"]


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
    tmp = os.path.join(tempfile.gettempdir(), "subastas-filtradas.xlsx")
    export.exportar(filas, tmp)
    return send_file(
        tmp,
        as_attachment=True,
        download_name="subastas-filtradas.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/sync", methods=["POST"])
def api_sync():
    """Sincronizacion manual (Fase 1). Body opcional: {"provincias": ["28", ...]}."""
    body = request.get_json(silent=True) or {}
    provincias = body.get("provincias") or list(PROVINCIAS.keys())
    total = sync_boe(provincias=provincias, estados=ESTADO_ACTIVOS_COD, con_detalle=True)
    estado = db.get_sync_state("BOE Subastas")
    return jsonify({"lotes_procesados": total, "ultima_sincronizacion": estado["last_full_sync"] if estado else None})


@app.route("/api/estado")
def api_estado():
    boe = db.get_sync_state("BOE Subastas")
    total = len(db.query_lotes())
    return jsonify({
        "total_lotes": total,
        "boe_ultima_sync": boe["last_full_sync"] if boe else None,
    })


if __name__ == "__main__":
    db.init_db()
    app.run(host="127.0.0.1", port=5057, debug=True)
