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

import io
import time
import urllib.request

import webview
from webview import FileDialog

import db
import export
from app import app

log = logging.getLogger("main")

HOST = "127.0.0.1"
PORT = 5057


def _run_flask():
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)


def _esperar_flask_listo(timeout=10.0):
    """Flask arranca en un hilo aparte (ver main()); si la ventana intenta
    cargar la url antes de que el servidor este realmente escuchando, la
    carga inicial falla o queda en un estado raro. Se espera de forma
    activa a que responda antes de crear la ventana."""
    limite = time.time() + timeout
    while time.time() < limite:
        try:
            urllib.request.urlopen(f"http://{HOST}:{PORT}/", timeout=0.5)
            return True
        except Exception:
            time.sleep(0.1)
    log.warning("Flask no respondio dentro de %ss, se abre la ventana igual", timeout)
    return False


class Api:
    """Expuesta al frontend como window.pywebview.api (ver static/app.js).

    El export a Excel no se resuelve como una descarga HTTP (window.location.href
    a /api/export): un .xlsx es en realidad un .zip con XML adentro, y el
    manejo de descargas de WebView2 en Windows lo guardaba con extension .zip
    en vez de .xlsx (confirmado con el archivo real que mando el cliente: el
    contenido era un Excel perfectamente valido, el unico problema era la
    extension con la que quedaba guardado). Pidiendo la ruta con el dialogo
    nativo "Guardar como" y escribiendo el archivo nosotros mismos, la
    extension queda siempre bajo nuestro control, sin pasar por esa
    conversion de WebView2.

    La referencia a la ventana nativa se guarda en _window (con guion bajo)
    a proposito: pywebview expone TODOS los atributos publicos de este objeto
    al puente de JavaScript, asi que guardarla como self.window hacia que
    pywebview intentara serializar la ventana entera (incluyendo objetos COM
    internos de Windows como AccessibilityObject) para exponerla a JS. Eso
    dispara una recursion infinita del lado de Windows apenas la pagina carga
    o se hace click en cualquier lado - confirmado con el mismo error
    reportado por otros usuarios de pywebview con el mismo patron
    (self.window publico en la clase de la API).
    """

    def export_excel(self, filtros):
        filas = db.query_lotes(
            fuente=filtros.get("fuente") or None,
            estado=filtros.get("estado") or None,
            categoria_subasta=filtros.get("categoria_subasta") or None,
            tipo_bien=filtros.get("tipo_bien") or None,
            provincia=filtros.get("provincia") or None,
            texto=filtros.get("texto") or None,
            fecha_inicio_desde=filtros.get("fecha_inicio_desde") or None,
            fecha_inicio_hasta=filtros.get("fecha_inicio_hasta") or None,
            fecha_conclusion_desde=filtros.get("fecha_conclusion_desde") or None,
            fecha_conclusion_hasta=filtros.get("fecha_conclusion_hasta") or None,
        )
        buffer = io.BytesIO()
        export.exportar(filas, buffer)

        try:
            ruta = self._window.create_file_dialog(
                FileDialog.SAVE,
                save_filename="subastas-filtradas.xlsx",
                file_types=("Archivos Excel (*.xlsx)",),
            )
            if not ruta:
                # Cancelado por el usuario en el dialogo, no es un error.
                return {"ok": False, "cancelado": True}
            destino = ruta[0] if isinstance(ruta, (list, tuple)) else ruta
            with open(destino, "wb") as f:
                f.write(buffer.getvalue())
            return {"ok": True, "ruta": destino, "lotes": len(filas)}
        except Exception as e:
            # Sin este try/except, cualquier fallo al escribir el archivo
            # (permisos, carpeta sincronizada con OneDrive bloqueada, ruta
            # invalida) tiraba una excepcion sin capturar del lado de
            # Python - el puente de pywebview no la reenviaba de forma
            # clara al JS, asi que el usuario se quedaba con el mensaje
            # "Elegí dónde guardar el archivo..." para siempre, sin
            # confirmacion ni error, como si nada hubiera pasado.
            log.exception("Error exportando a Excel")
            return {"ok": False, "error": str(e)}


def main():
    db.init_db()
    threading.Thread(target=_run_flask, daemon=True).start()
    _esperar_flask_listo()

    # pywebview bloquea descargas por defecto (ALLOW_DOWNLOADS=False). Ya no
    # dependemos de esto para el export a Excel (ver Api.export_excel), pero
    # se deja habilitado por si a futuro hace falta bajar algo mas.
    webview.settings['ALLOW_DOWNLOADS'] = True

    api = Api()
    window = webview.create_window(
        "Auctions Broker",
        f"http://{HOST}:{PORT}/",
        width=1150,
        height=760,
        min_size=(900, 600),
        js_api=api,
    )
    api._window = window
    webview.start()


if __name__ == "__main__":
    main()
