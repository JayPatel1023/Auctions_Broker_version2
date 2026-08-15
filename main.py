"""
Auctions Broker - punto de entrada de escritorio.

Levanta el servidor Flask local (app.py) en un hilo y lo muestra en una
ventana nativa con pywebview (sin barra de direcciones, como una app de
verdad). Esto es lo que se empaqueta con PyInstaller en un .exe.
"""

import logging
import os
import sys
import threading

# Con --windowed (sin consola), Windows deja sys.stdout/sys.stderr en None.
# Si algo intenta escribir ahi (logging, un print suelto, hasta una libreria
# de terceros) la app se cierra sola sin avisar nada. Hay que resolver esto
# ANTES de importar db/app, porque esos modulos configuran logging al cargarse.
if getattr(sys, "frozen", False):
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    log_dir = os.path.join(os.path.expanduser("~"), "AuctionsBroker")
    os.makedirs(log_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        filename=os.path.join(log_dir, "app.log"),
    )
else:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

import webview

import db
from app import app, iniciar_sync_si_hace_falta

log = logging.getLogger("main")

HOST = "127.0.0.1"
PORT = 5057


def _run_flask():
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


def main():
    db.init_db()
    threading.Thread(target=_run_flask, daemon=True).start()

    # Descarga automatica diaria: si la ultima sincronizacion tiene mas de
    # un dia (o nunca se hizo), arranca sola en segundo plano al abrir la
    # app, sin que el usuario tenga que tocar "Actualizar". Solo corre
    # mientras la app este abierta, como quedo hablado con el cliente.
    iniciar_sync_si_hace_falta()

    # pywebview bloquea descargas por defecto (ALLOW_DOWNLOADS=False). Sin esto,
    # el boton "Exportar a Excel" no hace nada visible: pywebview cancela la
    # descarga en silencio.
    webview.settings['ALLOW_DOWNLOADS'] = True

    webview.create_window(
        "Auctions Broker",
        f"http://{HOST}:{PORT}/",
        width=1150,
        height=760,
        min_size=(900, 600),
    )
    webview.start()


if __name__ == "__main__":
    main()
