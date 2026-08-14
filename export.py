"""
Exportacion a Excel, mismo formato que el archivo viejo del cliente
(wter/AuctionsWebFiltrosBOE171121.xlsx):
- 3 hojas segun estado: PROXIMA APERTURA / CELEBRANDOSE / CONCLUIDAS
- fila 1: "Index - Auctions.Web" (aca: "Index - Auctions Broker")
- fila 2: encabezados (24 columnas)
- fila 3+: datos
"""

from openpyxl import Workbook

import db

HEADERS = db.HEADERS

SHEETS = {
    "PROXIMA APERTURA": "Próxima apertura",
    "CELEBRANDOSE": "Celebrándose",
    "CONCLUIDAS": "Concluida",
}


def _euro(valor):
    if valor is None:
        return ""
    return f"{valor:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(a, b):
    if not a or not b:
        return ""
    return f"{(a / b * 100):,.2f} %".replace(",", "X").replace(".", ",").replace("X", ".")


def _fila_a_excel_row(row: dict):
    return [
        _pct(row["cantidad_reclamada"], row["valor_subasta"]),
        _pct(row["puja_minima"], row["valor_subasta"]),
        row["estado"],
        row["tipo_subasta"],
        row["tipo_bien"],
        row["id"],
        row["lotes"],
        row["provincia"],
        row["localidad"],
        row["direccion"],
        row["descripcion"],
        row["referencia_catastral"],
        row["marca"],
        row["modelo"],
        row["matricula"],
        _euro(row["cantidad_reclamada"]),
        _euro(row["valor_tasacion"]),
        _euro(row["valor_subasta"]),
        _euro(row["tramos_entre_pujas"]),
        _euro(row["puja_minima"]) if row["puja_minima"] is not None else "Sin puja mínima",
        _euro(row["importe_deposito"]),
        row["nombre"],
        row["fecha_inicio"],
        row["fecha_conclusion"],
    ]


def exportar(filas: list, path: str):
    """filas: lista de dicts (mismo shape que db.query_lotes()). Escribe un .xlsx en path."""
    wb = Workbook()
    wb.remove(wb.active)

    for nombre_hoja, estado in SHEETS.items():
        ws = wb.create_sheet(nombre_hoja)
        ws.append(["Index - Auctions Broker"])
        ws.append(HEADERS)
        for row in filas:
            if row["estado"] == estado:
                ws.append(_fila_a_excel_row(row))

    wb.save(path)
    return path


if __name__ == "__main__":
    rows = db.query_lotes()
    out = exportar(rows, "prueba_export.xlsx")
    print(f"Exportado {len(rows)} filas a {out}")
