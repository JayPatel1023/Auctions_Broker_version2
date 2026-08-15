"""
Auctions Broker - orquestacion: scraper -> base de datos.

BOE Subastas + Seguridad Social, solo estados activos (Proxima apertura /
Celebrandose). La descarga automatica diaria y el barrido del historico
completo son el siguiente paso, todavia no estan acá.
"""

import logging
import re
from datetime import datetime

from scraper.boe import BOEScraper, PROVINCIAS, ESTADOS_ACTIVOS
from scraper.seg_social import SegSocialScraper, TIPOS_BIEN as SS_TIPOS_BIEN, _inferir_estado
import db

log = logging.getLogger("ingest")

# Provincias con mas volumen de subastas, para que el boton "Actualizar" de
# la Fase 1 termine en unos minutos en vez de recorrer las 52 provincias con
# detalle completo por lote (eso queda para el barrido historico de Fase 2).
PRINCIPALES_PROVINCIAS = [
    "28",  # Madrid
    "08",  # Barcelona
    "46",  # Valencia
    "41",  # Sevilla
    "29",  # Málaga
    "03",  # Alicante
    "30",  # Murcia
    "50",  # Zaragoza
    "15",  # A Coruña
    "48",  # Bizkaia
]
LIMITE_POR_COMBO_DEFECTO = 12


def _lotes_a_entero(valor):
    if not valor:
        return 1
    v = str(valor).lower()
    if "sin lotes" in v:
        return 1
    m = re.search(r"\d+", v)
    return int(m.group(0)) if m else 1


def _lote_a_fila_db(lote: dict) -> dict:
    return {
        "id": lote["id"],
        "fuente": "BOE Subastas",
        "estado": lote.get("estado", ""),
        "tipo_subasta": lote.get("tipo_subasta", ""),
        "tipo_bien": lote.get("tipo_bien", ""),
        "lotes": _lotes_a_entero(lote.get("lotes")),
        "provincia": lote.get("provincia") or lote.get("provincia_busqueda", ""),
        "localidad": lote.get("localidad", ""),
        "direccion": lote.get("direccion", ""),
        "descripcion": lote.get("descripcion") or lote.get("descripcion_resumen", ""),
        "referencia_catastral": lote.get("referencia_catastral", ""),
        "marca": lote.get("marca", ""),
        "modelo": lote.get("modelo", ""),
        "matricula": lote.get("matricula", ""),
        "cantidad_reclamada": lote.get("cantidad_reclamada"),
        "valor_tasacion": lote.get("valor_tasacion"),
        "valor_subasta": lote.get("valor_subasta"),
        "tramos_entre_pujas": lote.get("tramos_entre_pujas"),
        "puja_minima": lote.get("puja_minima"),
        "importe_deposito": lote.get("importe_deposito"),
        "nombre": lote.get("nombre", ""),
        "fecha_inicio": lote.get("fecha_inicio", ""),
        "fecha_conclusion": lote.get("fecha_conclusion") or lote.get("fecha_fin_resumen", ""),
    }


# Seguridad Social usa su propia taxonomia de tipo de bien (Finca Rustica,
# Finca Urbana, Vehiculo, Embarcacion, Resto de Bienes Muebles). La
# normalizamos a las 3 categorias que ya usa BOE para que el filtro
# "Tipo de bien" del buscador no duplique conceptos entre las 2 fuentes.
SS_TIPO_BIEN_A_BOE = {
    "Finca Rústica": "Inmueble",
    "Finca Urbana": "Inmueble",
    "Vehículo": "Vehículo",
    "Embarcación": "Bien mueble",
    "Resto de Bienes Muebles": "Bien mueble",
}


def _lote_seg_social_a_fila_db(lote: dict) -> dict:
    fecha = lote.get("fecha_subasta", "")
    return {
        "id": lote["id"],
        "fuente": "Seguridad Social",
        "estado": _inferir_estado(fecha),
        "tipo_subasta": "RECAUDACIÓN SEGURIDAD SOCIAL",
        "tipo_bien": SS_TIPO_BIEN_A_BOE.get(lote.get("tipo_bien", ""), lote.get("tipo_bien", "")),
        "lotes": _lotes_a_entero(lote.get("lotes")),
        "provincia": lote.get("provincia") or lote.get("provincia_busqueda", ""),
        "localidad": lote.get("localidad", ""),
        "direccion": lote.get("direccion", ""),
        "descripcion": lote.get("descripcion", ""),
        "referencia_catastral": lote.get("referencia_catastral", ""),
        "marca": lote.get("marca", ""),
        "modelo": lote.get("modelo", ""),
        "matricula": lote.get("matricula", ""),
        "cantidad_reclamada": lote.get("cantidad_reclamada"),
        "valor_tasacion": lote.get("valor_tasacion"),
        "valor_subasta": lote.get("valor_subasta"),
        "tramos_entre_pujas": None,
        "puja_minima": None,
        "importe_deposito": None,
        "nombre": lote.get("nombre", ""),
        "fecha_inicio": fecha,
        "fecha_conclusion": fecha,
    }


def sync_seg_social(provincias=None, con_detalle=True, limite_por_combo=None, progreso=None):
    """Descarga lotes de Seguridad Social (bienes embargados) y los guarda
    en SQLite. Mismo patron que sync_boe: primera pagina por provincia,
    pensado para el boton "Actualizar" (no el barrido historico)."""
    db.init_db()
    scraper = SegSocialScraper()
    provincias = provincias or PRINCIPALES_PROVINCIAS

    total = 0
    for prov_cod in provincias:
        lotes = scraper.buscar([prov_cod], tipos_bien=list(SS_TIPOS_BIEN.keys()))
        if limite_por_combo:
            lotes = lotes[:limite_por_combo]
        if not lotes:
            continue
        msg = f"Seguridad Social - provincia {prov_cod}: {len(lotes)} lotes"
        log.info(msg)
        if progreso:
            progreso(msg)
        for lote in lotes:
            if con_detalle:
                lote.update(scraper.detalle(lote["emb_id"]))
            db.upsert_lote(_lote_seg_social_a_fila_db(lote))
            total += 1

    db.set_sync_state("Seguridad Social", last_full_sync=datetime.now().isoformat(timespec="seconds"))
    return total


def sync_boe(provincias=None, estados=None, con_detalle=True, limite_por_combo=None, progreso=None):
    """Descarga lotes de BOE Subastas y los guarda en SQLite.
    progreso: callback opcional(mensaje:str) para reportar avance a la UI."""
    db.init_db()
    scraper = BOEScraper()
    provincias = provincias or list(PROVINCIAS.keys())
    estados = estados or ESTADOS_ACTIVOS

    total = 0
    for estado_cod in estados:
        for prov_cod in provincias:
            lotes, _ = scraper.buscar(prov_cod, estado_cod)
            if limite_por_combo:
                lotes = lotes[:limite_por_combo]
            if not lotes:
                continue
            msg = f"{PROVINCIAS[prov_cod]}: {len(lotes)} lotes"
            log.info(msg)
            if progreso:
                progreso(msg)
            for lote in lotes:
                if con_detalle:
                    lote.update(scraper.detalle(lote["id"]))
                db.upsert_lote(_lote_a_fila_db(lote))
                total += 1

    db.set_sync_state("BOE Subastas", last_full_sync=datetime.now().isoformat(timespec="seconds"))
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    # prueba chica: 2 provincias, ambos estados activos, tope de 3 lotes por combinacion
    n = sync_boe(provincias=["28", "08"], estados=["PU", "EJ"], limite_por_combo=3)
    print(f"\n{n} lotes guardados en la base de datos.")
    rows = db.query_lotes()
    print(f"Total en DB: {len(rows)}")
    for r in rows[:5]:
        print(r["id"], "-", r["tipo_bien"], "-", r["provincia"], "-", r["valor_tasacion"])
