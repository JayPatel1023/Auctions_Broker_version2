"""
Auctions Broker - almacenamiento local (SQLite)

Columnas de `lotes` calcadas del Excel real del cliente
(wter/AuctionsWebFiltrosBOE171121.xlsx, hoja "PROXIMA APERTURA" fila 2),
para que el export a Excel reproduzca exactamente su formato de siempre.
"""

import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "auctions_broker.db"


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
    conn = sqlite3.connect(DB_PATH)
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


def query_lotes(fuente=None, estado=None, tipo_subasta=None, categoria_subasta=None, tipo_bien=None, provincia=None, texto=None,
                 fecha_inicio_desde=None, fecha_inicio_hasta=None, fecha_conclusion_desde=None, fecha_conclusion_hasta=None):
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
    Subasta / Fecha fin Subasta)."""
    conn = get_conn()
    c = conn.cursor()
    sql = "SELECT * FROM lotes WHERE 1=1"
    params = []

    def in_clause(columna, valores):
        nonlocal sql
        if valores:
            sql += f" AND {columna} IN ({','.join('?' for _ in valores)})"
            params.extend(valores)

    in_clause("fuente", fuente)
    in_clause("estado", estado)
    in_clause("tipo_subasta", tipo_subasta)
    in_clause("categoria_subasta", categoria_subasta)
    in_clause("tipo_bien", tipo_bien)
    in_clause("provincia", provincia)
    if texto:
        sql += " AND (descripcion LIKE ? OR id LIKE ?)"
        like = f"%{texto}%"
        params += [like, like]

    def rango_fecha(columna_iso, desde, hasta):
        nonlocal sql
        if desde:
            sql += f" AND {columna_iso} >= ?"
            params.append(desde)
        if hasta:
            sql += f" AND {columna_iso} <= ?"
            params.append(hasta)

    rango_fecha("fecha_inicio_iso", fecha_inicio_desde, fecha_inicio_hasta)
    rango_fecha("fecha_conclusion_iso", fecha_conclusion_desde, fecha_conclusion_hasta)

    # NULLS LAST explicito: sin esto, SQLite pone los NULL primero en ASC
    # -una fila con fecha sin parsear (fallo de red al traer el detalle, o
    # una fecha en un formato no reconocido) apareceria arriba de todo antes
    # que las fechas reales, en vez de al final donde tiene sentido.
    sql += " ORDER BY fecha_conclusion_iso IS NULL, fecha_conclusion_iso"
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
