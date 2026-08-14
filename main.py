"""
Auctions Broker - punto de entrada de escritorio.

Levanta el servidor Flask local (app.py) en un hilo y lo muestra en una
ventana nativa con pywebview (sin barra de direcciones, como una app de
verdad). Esto es lo que se empaqueta con PyInstaller en un .exe.
"""

import logging
import threading

import webview

import db
from app import app

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("main")

HOST = "127.0.0.1"
PORT = 5057


def _run_flask():
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


def main():
    db.init_db()
    threading.Thread(target=_run_flask, daemon=True).start()

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
