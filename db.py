"""
Auctions Broker - almacenamiento local (SQLite)

Columnas de `lotes` calcadas del Excel real del cliente
(wter/AuctionsWebFiltrosBOE171121.xlsx, hoja "PROXIMA APERTURA" fila 2),
para que el export a Excel reproduzca exactamente su formato de siempre.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "auctions_broker.db"

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
    "last_synced",
]


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
    conn.close()


def upsert_lote(row: dict):
    """row debe traer las claves de COLUMNS (menos last_synced, se pone solo)."""
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


def query_lotes(fuente=None, estado=None, tipo_subasta=None, tipo_bien=None, provincia=None, texto=None):
    conn = get_conn()
    c = conn.cursor()
    sql = "SELECT * FROM lotes WHERE 1=1"
    params = []
    if fuente:
        sql += " AND fuente = ?"
        params.append(fuente)
    if estado:
        sql += " AND estado = ?"
        params.append(estado)
    if tipo_subasta:
        sql += " AND tipo_subasta = ?"
        params.append(tipo_subasta)
    if tipo_bien:
        sql += " AND tipo_bien = ?"
        params.append(tipo_bien)
    if provincia:
        sql += " AND provincia = ?"
        params.append(provincia)
    if texto:
        sql += " AND (descripcion LIKE ? OR id LIKE ?)"
        like = f"%{texto}%"
        params += [like, like]
    sql += " ORDER BY fecha_conclusion"
    c.execute(sql, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_sync_state(fuente):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM sync_state WHERE fuente = ?", (fuente,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def set_sync_state(fuente, last_full_sync=None, last_combo=None):
    conn = get_conn()
    c = conn.cursor()
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
