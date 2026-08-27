# Auctions Broker

App de escritorio (Windows) que centraliza subastas de bienes embargados de dos fuentes oficiales españolas en una sola tabla filtrable, exportable a Excel:

- **BOE Subastas** (boe.es)
- **Seguridad Social - Subastas de Bienes Embargados** (w6.seg-social.es)

## Qué hace

- Sincroniza lotes de ambas fuentes y los guarda en una base local (SQLite).
- Dos modos de sincronización, cada uno con su propio estado y progreso:
  - **Actualizar** — rápido, solo estados activos, acotado a las provincias con más volumen (o a las que el usuario tilde en el filtro).
  - **Descargar histórico completo** — barrido completo desde 2021, resumible, corre en segundo plano mientras la app esté abierta.
- Filtros por fuente, provincia, tipo de bien, estado, categoría de subasta, texto y rango de fechas.
- Exporta el resultado filtrado a `.xlsx`, respetando el formato de columnas que ya usaba el cliente antes de esta app.

## Estructura

| Archivo / carpeta | Contenido |
|---|---|
| `main.py` | Punto de entrada de escritorio: levanta `app.py` en un hilo y lo muestra en una ventana nativa con [pywebview](https://pywebview.flowrl.com/). Esto es lo que empaqueta PyInstaller en el `.exe`. |
| `app.py` | Servidor Flask local (`127.0.0.1`, no expuesto). Rutas de la UI y de sincronización. |
| `ingest.py` | Orquesta scraper → base de datos (sync rápida e histórica). |
| `db.py` | Acceso a la base SQLite. |
| `export.py` | Exportación a Excel. |
| `scraper/boe.py` | Scraper de BOE Subastas. |
| `scraper/seg_social.py` | Scraper de Seguridad Social. |
| `templates/`, `static/` | Frontend (HTML/JS de la ventana). |
| `.github/workflows/build-windows.yml` | Compila el `.exe` en cada push a `main`. |

## Desarrollo

```
pip install -r requirements.txt
python main.py
```

## Compilar el .exe

Automático vía GitHub Actions en cada push a `main` (ver `.github/workflows/build-windows.yml`), o local con:

```
pyinstaller --onefile --windowed --name AuctionsBroker --add-data "templates;templates" --add-data "static;static" main.py
```
