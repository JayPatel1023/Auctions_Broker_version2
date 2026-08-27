"""
Auctions Broker - almacenamiento local (SQLite)

Columnas de `lotes` calcadas del Excel real del cliente
(wter/AuctionsWebFiltrosBOE171121.xlsx, hoja "PROXIMA APERTURA" fila 2),
para que el export a Excel reproduzca exactamente su formato de siempre.
"""

import os
import re
import sqlite3
import sys
from pathlib import Path

# Con --onefile de PyInstaller, __file__ de un modulo empaquetado apunta
# adentro de la carpeta temporal donde el .exe se auto-extrae en CADA
# arranque (sys._MEIPASS, distinta cada vez) - no a la carpeta del .exe.
# Path(__file__).parent / "auctions_broker.db" hacia que la base de datos
# se creara ahi adentro, y esa carpeta temporal se borra sola cuando la
# app se cierra: se perdia TODO (sync, historico, filtros guardados) cada
# vez que se cerraba y volvia a abrir la app empaquetada, sin ningun error
# visible (confirmado en vivo: 333 lotes -> 0 lotes tras cerrar y abrir de
# nuevo el mismo .exe). Se usa la misma carpeta estable que ya usa main.py
# para el log (~/AuctionsBroker) para que la base persista entre arranques.
if getattr(sys, "frozen", False):
    _DATA_DIR = Path(os.path.expanduser("~")) / "AuctionsBroker"
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
else:
    _DATA_DIR = Path(__file__).parent

DB_PATH = _DATA_DIR / "auctions_broker.db"


def _fecha_iso(texto):
    """'17-08-2026 18:00:00 CET' o '17/08/2026 a las 18:00' -> '2026-08-17'.
    BOE/Seg Social siempre mandan la fecha en dia-mes-año, con '-' o '/' y
    texto variable atras (hora, zona horaria, "a las...") - se guarda en
    formato ISO ademas del texto original para poder ordenar y filtrar por
    rango de fecha de verdad (un texto DD-MM-YYYY ordenado como texto da
    resultados incorrectos apenas cruza de mes)."""
    if not texto:
        return None
    m = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", texto)
    if not m:
        return None
    dia, mes, anio = m.groups()
    return f"{anio}-{mes}-{dia}"

HEADERS = [
    "Cantidad Reclamada / Valor Subasta",
    "Puja Mínima / Valor Subasta",
    "Estado",
    "Tipo De Subasta",
    "Tipo Bien",
    "Id",
    "Lotes",
    "Provincia",
    "Localidad",
    "Dirección",
    "Descripción",
    "Referencia Catastral",
    "Marca",
    "Modelo",
    "Matricula",
    "Cantidad Reclamada",
    "Valor De Tasacion",
    "Valor Subasta",
    "Tramos Entre Pujas",
    "Puja Mínima",
    "Importe Del Deposito",
    "Nombre",
    "Fecha De Inicio",
    "Fecha De Conclusión",
]

# Columnas del Excel -> columnas de la tabla lotes (snake_case, mismo orden)
COLUMNS = [
    "id",
    "fuente",
    "estado",
    "tipo_subasta",
    "categoria_subasta",
    "tipo_bien",
    "lotes",
    "provincia",
    "localidad",
    "direccion",
    "descripcion",
    "referencia_catastral",
    "marca",
    "modelo",
    "matricula",
    "cantidad_reclamada",
    "valor_tasacion",
    "valor_subasta",
    "tramos_entre_pujas",
    "puja_minima",
    "importe_deposito",
    "nombre",
    "fecha_inicio",
    "fecha_conclusion",
    "fecha_inicio_iso",
    "fecha_conclusion_iso",
    "last_synced",
]


def get_conn():
    # timeout=15 (default es 5) + WAL: confirmado en vivo por el cliente,
    # "Error: database is locked" - paso con el histórico/actualizar
    # escribiendo de fondo mientras el export a Excel (que hace su propia
    # lectura aparte) se disparo muchas veces seguidas (broto clickeando
    # el boton, ya que el dialogo nativo de guardar quedaba tapado detras
    # de la ventana sin que se notara que ya habia aparecido). Con el modo
    # de journal por default (rollback journal), un escritor bloquea a
    # TODOS los lectores hasta que termina - con WAL, los lectores pueden
    # seguir leyendo la version anterior mientras un escritor esta activo,
    # sin bloquearse entre si. WAL se activa una vez por archivo (queda
    # asi permanentemente), por eso conviene pedirlo en cada conexion en
    # vez de solo al crear la base - no cuesta nada pedirlo de nuevo si
    # ya esta activo.
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _migrar_columnas_faltantes(conn):
    """CREATE TABLE IF NOT EXISTS no agrega columnas a una tabla que ya
    existe - una base de un usuario que ya venia usando la app (con datos
    reales sincronizados) se queda con el esquema viejo para siempre sin
    esto, y cada query rompe con 'no such column' apenas se agrega una
    columna nueva en una version posterior."""
    columnas_nuevas = {
        "categoria_subasta": "TEXT",
        "fecha_inicio_iso": "TEXT",
        "fecha_conclusion_iso": "TEXT",
    }
    c = conn.cursor()
    c.execute("PRAGMA table_info(lotes)")
    existentes = {fila[1] for fila in c.fetchall()}
    for columna, tipo in columnas_nuevas.items():
        if columna not in existentes:
            c.execute(f"ALTER TABLE lotes ADD COLUMN {columna} {tipo}")
    conn.commit()


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS lotes (
            id TEXT PRIMARY KEY,
            fuente TEXT,
            estado TEXT,
            tipo_subasta TEXT,
            categoria_subasta TEXT,
            tipo_bien TEXT,
            lotes INTEGER,
            provincia TEXT,
            localidad TEXT,
            direccion TEXT,
            descripcion TEXT,
            referencia_catastral TEXT,
            marca TEXT,
            modelo TEXT,
            matricula TEXT,
            cantidad_reclamada REAL,
            valor_tasacion REAL,
            valor_subasta REAL,
            tramos_entre_pujas REAL,
            puja_minima REAL,
            importe_deposito REAL,
            nombre TEXT,
            fecha_inicio TEXT,
            fecha_conclusion TEXT,
            fecha_inicio_iso TEXT,
            fecha_conclusion_iso TEXT,
            last_synced TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_state (
            fuente TEXT PRIMARY KEY,
            last_full_sync TEXT,
            last_combo TEXT
        )
        """
    )
    conn.commit()
    _migrar_columnas_faltantes(conn)
    conn.close()


def upsert_lote(row: dict):
    """row debe traer las claves de COLUMNS (menos last_synced y las
    *_iso, que se calculan solas a partir de fecha_inicio/fecha_conclusion)."""
    row = dict(row)
    row["fecha_inicio_iso"] = _fecha_iso(row.get("fecha_inicio"))
    row["fecha_conclusion_iso"] = _fecha_iso(row.get("fecha_conclusion"))

    conn = get_conn()
    c = conn.cursor()
    placeholders = ",".join("?" for _ in COLUMNS)
    cols = ",".join(COLUMNS)
    values = [row.get(col) for col in COLUMNS if col != "last_synced"]
    from datetime import datetime

    values.append(datetime.now().isoformat(timespec="seconds"))
    c.execute(
        f"INSERT INTO lotes ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET "
        + ",".join(f"{col}=excluded.{col}" for col in COLUMNS if col != "id"),
        values,
    )
    conn.commit()
    conn.close()


def limpiar_lotes_huerfanos(id_subasta, ids_validos):
    """upsert_lote nunca borra, solo inserta/actualiza - una subasta multi
    lote cuya cantidad de lotes cambia entre una sincronizacion y otra (ej.
    tenia 3 lotes, ahora 2; o antes tenia 1 y ahora tiene varios, o al
    reves) deja filas viejas huerfanas con datos obsoletos para siempre si
    nada las borra. Se llama con el id de la subasta sin sufijo y el set de
    ids que la sincronizacion actual considera validos para ella (que puede
    ser {id_subasta} sola, o {id_subasta}-L1, -L2, etc)."""
    conn = get_conn()
    c = conn.cursor()
    ids_validos = list(ids_validos) or [""]
    placeholders = ",".join("?" for _ in ids_validos)
    c.execute(
        f"DELETE FROM lotes WHERE (id = ? OR id LIKE ?) AND id NOT IN ({placeholders})",
        [id_subasta, f"{id_subasta}-L%"] + ids_validos,
    )
    conn.commit()
    conn.close()


def _clausula_where(fuente, estado, tipo_subasta, categoria_subasta, tipo_bien, provincia, texto,
                     fecha_inicio_desde, fecha_inicio_hasta, fecha_conclusion_desde, fecha_conclusion_hasta):
    """fuente/estado/tipo_subasta/categoria_subasta/tipo_bien/provincia:
    None o una lista de valores a combinar con OR (IN), para poder tildar
    varias opciones a la vez por filtro -como en el buscador real de BOE
    Subastas- en vez de forzar una sola opcion por categoria.

    tipo_subasta es el texto libre que trae cada subasta (ej. "JUDICIAL EN
    VIA DE APREMIO"), solo tiene los valores que ya se hayan sincronizado.
    categoria_subasta son las 5 categorias fijas reales de BOE (Judicial/
    Notarial/AEAT/Otras administraciones tributarias/Subastas
    administrativas generales) - el filtro "Tipo de subasta" del buscador
    usa esta, no tipo_subasta.

    fecha_*_desde/hasta: strings 'YYYY-MM-DD' (lo que devuelve un <input
    type=date>), se comparan contra fecha_inicio_iso/fecha_conclusion_iso
    -las mismas 2 fechas que tiene el formulario real de BOE (Fecha inicio
    Subasta / Fecha fin Subasta).

    Devuelve (where_sql, params) compartido por query_lotes y count_lotes,
    para no duplicar la logica de filtros entre traer las filas y contarlas."""
    where = "WHERE 1=1"
    params = []

    def in_clause(columna, valores):
        nonlocal where
        if valores:
            where += f" AND {columna} IN ({','.join('?' for _ in valores)})"
            params.extend(valores)

    in_clause("fuente", fuente)
    in_clause("estado", estado)
    in_clause("tipo_subasta", tipo_subasta)
    in_clause("categoria_subasta", categoria_subasta)
    in_clause("tipo_bien", tipo_bien)
    in_clause("provincia", provincia)
    if texto:
        where += " AND (descripcion LIKE ? OR id LIKE ?)"
        like = f"%{texto}%"
        params += [like, like]

    def rango_fecha(columna_iso, desde, hasta):
        nonlocal where
        if desde:
            where += f" AND {columna_iso} >= ?"
            params.append(desde)
        if hasta:
            where += f" AND {columna_iso} <= ?"
            params.append(hasta)

    rango_fecha("fecha_inicio_iso", fecha_inicio_desde, fecha_inicio_hasta)
    rango_fecha("fecha_conclusion_iso", fecha_conclusion_desde, fecha_conclusion_hasta)
    return where, params


def query_lotes(fuente=None, estado=None, tipo_subasta=None, categoria_subasta=None, tipo_bien=None, provincia=None, texto=None,
                 fecha_inicio_desde=None, fecha_inicio_hasta=None, fecha_conclusion_desde=None, fecha_conclusion_hasta=None,
                 limite=None, offset=None):
    """Ver _clausula_where para el significado de los filtros.

    limite: None (default) trae TODAS las filas que matcheen - lo que
    necesitan el export a Excel y el conteo interno, que tienen que ver
    el conjunto completo. Pasarlo explicito desde /api/lotes (el buscador
    de la pantalla) para no mandarle al navegador miles de filas de una:
    con el historico completo pasando los 10 mil lotes, sin limite el
    frontend terminaba re-renderizando la tabla entera en cada tick del
    poll (cada 2-3 segundos mientras corre una sync) hasta quedarse sin
    memoria - confirmado en vivo por el cliente (WebView2 tiraba
    "Codigo de error: Out of Memory").

    offset: solo tiene efecto si limite tambien esta puesto (paginado de
    /api/lotes, ver app.py) - sin limite no hay pagina de la que hablar."""
    where, params = _clausula_where(fuente, estado, tipo_subasta, categoria_subasta, tipo_bien, provincia, texto,
                                     fecha_inicio_desde, fecha_inicio_hasta, fecha_conclusion_desde, fecha_conclusion_hasta)
    conn = get_conn()
    c = conn.cursor()
    # NULLS LAST explicito: sin esto, SQLite pone los NULL primero en ASC
    # -una fila con fecha sin parsear (fallo de red al traer el detalle, o
    # una fecha en un formato no reconocido) apareceria arriba de todo antes
    # que las fechas reales, en vez de al final donde tiene sentido.
    sql = f"SELECT * FROM lotes {where} ORDER BY fecha_conclusion_iso IS NULL, fecha_conclusion_iso"
    if limite:
        sql += " LIMIT ?"
        params = params + [limite]
        if offset:
            sql += " OFFSET ?"
            params = params + [offset]
    c.execute(sql, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def count_lotes(fuente=None, estado=None, tipo_subasta=None, categoria_subasta=None, tipo_bien=None, provincia=None, texto=None,
                 fecha_inicio_desde=None, fecha_inicio_hasta=None, fecha_conclusion_desde=None, fecha_conclusion_hasta=None):
    """Total real de filas que matchean los filtros, sin traerlas todas a
    Python solo para hacer len() - eso es lo que hacia antes /api/estado
    (le pegaba a query_lotes() sin filtros ni limite en cada poll, osea
    traia la tabla ENTERA a memoria cada 2-3 segundos nada mas para
    contarla)."""
    where, params = _clausula_where(fuente, estado, tipo_subasta, categoria_subasta, tipo_bien, provincia, texto,
                                     fecha_inicio_desde, fecha_inicio_hasta, fecha_conclusion_desde, fecha_conclusion_hasta)
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"SELECT COUNT(*) FROM lotes {where}", params)
    total = c.fetchone()[0]
    conn.close()
    return total


def resumen_lotes(fuente=None, estado=None, tipo_subasta=None, categoria_subasta=None, tipo_bien=None, provincia=None, texto=None,
                   fecha_inicio_desde=None, fecha_inicio_hasta=None, fecha_conclusion_desde=None, fecha_conclusion_hasta=None):
    """Los numeros de las tarjetas KPI (total, proxima apertura,
    celebrandose, tasacion promedio) sobre el conjunto COMPLETO que
    matchea los filtros, no solo sobre las filas que se le mandan al
    navegador para la tabla (query_lotes con limite). Antes esos numeros
    se calculaban en el frontend a partir de rows.length/rows.filter(...)
    sobre esas mismas filas limitadas - iba a quedar mostrando "500"
    como total en vez de "10813" apenas se agregara el limite a la tabla."""
    where, params = _clausula_where(fuente, estado, tipo_subasta, categoria_subasta, tipo_bien, provincia, texto,
                                     fecha_inicio_desde, fecha_inicio_hasta, fecha_conclusion_desde, fecha_conclusion_hasta)
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        f"""SELECT COUNT(*),
                   SUM(CASE WHEN estado = 'Próxima apertura' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN estado = 'Celebrándose' THEN 1 ELSE 0 END),
                   AVG(CASE WHEN valor_tasacion IS NOT NULL AND valor_tasacion != 0 THEN valor_tasacion END)
            FROM lotes {where}""",
        params,
    )
    total, proxima, celebrando, avg_tasacion = c.fetchone()
    conn.close()
    return {
        "total": total or 0,
        "proxima_apertura": proxima or 0,
        "celebrandose": celebrando or 0,
        "tasacion_promedio": avg_tasacion,
    }


def get_sync_state(fuente):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM sync_state WHERE fuente = ?", (fuente,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def set_sync_state(fuente, last_full_sync=None, last_combo=None, reiniciar_combo=False):
    """last_combo=None normalmente NO borra el valor guardado (el COALESCE
    lo preserva a proposito, para que guardar solo last_full_sync al final
    de una pasada no pise por accidente el ultimo combo mientras todavia
    estaba a mitad de camino). reiniciar_combo=True es la unica forma de
    poner last_combo en NULL de verdad - se usa cuando una pasada termina
    COMPLETA: sin esto, el ultimo combo guardado quedaba apuntando al
    ULTIMO de la lista para siempre, asi que la proxima vez que arrancaba
    el barrido, el resumible se pasaba TODOS los combos de largo (creyendo
    que ya estaban hechos) sin bajar nada nuevo - confirmado en vivo: el
    boton mostraba "Descargando..." un instante y volvia solo a "Pasada
    completa - 0 lotes nuevos", porque la funcion terminaba casi al
    toque sin hacer ningun pedido real."""
    conn = get_conn()
    c = conn.cursor()
    if reiniciar_combo:
        c.execute(
            """
            INSERT INTO sync_state (fuente, last_full_sync, last_combo) VALUES (?, ?, NULL)
            ON CONFLICT(fuente) DO UPDATE SET
                last_full_sync = COALESCE(excluded.last_full_sync, sync_state.last_full_sync),
                last_combo = NULL
            """,
            (fuente, last_full_sync),
        )
    else:
        c.execute(
            """
            INSERT INTO sync_state (fuente, last_full_sync, last_combo) VALUES (?, ?, ?)
            ON CONFLICT(fuente) DO UPDATE SET
                last_full_sync = COALESCE(excluded.last_full_sync, sync_state.last_full_sync),
                last_combo = COALESCE(excluded.last_combo, sync_state.last_combo)
            """,
            (fuente, last_full_sync, last_combo),
        )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"DB lista en {DB_PATH}")
