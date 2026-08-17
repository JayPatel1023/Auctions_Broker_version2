"""
Auctions Broker - orquestacion: scraper -> base de datos.

BOE Subastas + Seguridad Social. Dos modos:
- sync_boe / sync_seg_social: rapido, solo estados activos, para el boton
  "Actualizar" (piensa en minutos).
- sync_boe_historico / sync_seg_social_historico: barrido completo,
  resumible via sync_state (piensa en horas, se retoma solo donde quedo
  la ultima vez que se ejecuto).
"""

import logging
import re
from datetime import date, datetime, timedelta

from scraper.boe import BOEScraper, PROVINCIAS, ESTADOS_ACTIVOS, ESTADOS_HISTORICOS, ESTADOS
from scraper.seg_social import SegSocialScraper, TIPOS_BIEN as SS_TIPOS_BIEN, _inferir_estado
import db

log = logging.getLogger("ingest")

# Desde cuando arranca el barrido historico de BOE por defecto. El sitio no
# tiene forma de preguntar "desde cuando hay datos", asi que se fija una
# fecha razonable (el archivo Excel viejo del cliente es de nov. 2021) en
# vez de ir año por año a ciegas hasta encontrar el principio.
HISTORICO_DESDE = "2021-01-01"

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
# Antes eran 12: con solo 2 estados activos (Proxima apertura/Celebrandose)
# entraba en "2 a 5 minutos". Al sumar Suspendida y Cancelada para completar
# el filtro de estados (pedido del cliente), el barrido paso a recorrer el
# doble de combinaciones provincia x estado - medido en vivo, con 12 el
# barrido completo de "Actualizar" pasaba los 10 minutos. Se baja a 6 para
# que el tiempo total quede parecido al de antes.
LIMITE_POR_COMBO_DEFECTO = 6


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
    for i, prov_cod in enumerate(provincias, start=1):
        lotes = scraper.buscar([prov_cod], tipos_bien=list(SS_TIPOS_BIEN.keys()))
        if limite_por_combo:
            lotes = lotes[:limite_por_combo]
        msg = f"Seguridad Social ({i}/{len(provincias)}) - {PROVINCIAS.get(prov_cod, prov_cod)}: {len(lotes)} lotes"
        log.info(msg)
        if progreso:
            progreso(msg)
        if not lotes:
            continue
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

    combos = [(e, p) for e in estados for p in provincias]
    total = 0
    for i, (estado_cod, prov_cod) in enumerate(combos, start=1):
        lotes, _, _ = scraper.buscar(prov_cod, estado_cod)
        if limite_por_combo:
            lotes = lotes[:limite_por_combo]
        msg = f"({i}/{len(combos)}) {PROVINCIAS[prov_cod]} - {ESTADOS[estado_cod]}: {len(lotes)} lotes"
        log.info(msg)
        if progreso:
            progreso(msg)
        if not lotes:
            continue
        for lote in lotes:
            if con_detalle:
                lote.update(scraper.detalle(lote["id"]))
            db.upsert_lote(_lote_a_fila_db(lote))
            total += 1

    db.set_sync_state("BOE Subastas", last_full_sync=datetime.now().isoformat(timespec="seconds"))
    return total


# ---------- barrido historico (resumible, corre en segundo plano mientras
# la app este abierta, y sigue donde quedo la proxima vez que se abra) ----------

def _mitad_fecha(desde: str, hasta: str) -> str:
    d1, d2 = date.fromisoformat(desde), date.fromisoformat(hasta)
    return (d1 + (d2 - d1) // 2).isoformat()


def _buscar_boe_fragmentado(scraper, provincia_cod, estado_cod, desde, hasta, profundidad=0, max_profundidad=6):
    """BOE tira 'excesivo' si una combinacion provincia+estado+rango de
    fecha trae de mas. Para el barrido historico (rangos de años) hace
    falta partir el rango a la mitad las veces que haga falta hasta que
    cada pedazo entre bajo el limite."""
    lotes, _, excesivo = scraper.buscar(provincia_cod, estado_cod, fecha_desde=desde, fecha_hasta=hasta)
    if not excesivo or profundidad >= max_profundidad or desde == hasta:
        return lotes
    mitad = _mitad_fecha(desde, hasta)
    siguiente = (date.fromisoformat(mitad) + timedelta(days=1)).isoformat()
    izquierda = _buscar_boe_fragmentado(scraper, provincia_cod, estado_cod, desde, mitad, profundidad + 1, max_profundidad)
    derecha = _buscar_boe_fragmentado(scraper, provincia_cod, estado_cod, siguiente, hasta, profundidad + 1, max_profundidad)
    return izquierda + derecha


def sync_boe_historico(desde=HISTORICO_DESDE, hasta=None, provincias=None, con_detalle=True, progreso=None, continuar=True):
    """Barrido historico completo de BOE (estados PC/FS = Concluida).
    Resumible: guarda en sync_state el ultimo combo provincia+estado
    terminado y la proxima vez arranca justo despues de ahi, en vez de
    repetir todo desde cero."""
    db.init_db()
    hasta = hasta or date.today().isoformat()
    scraper = BOEScraper()
    provincias = provincias or list(PROVINCIAS.keys())
    combos = [(p, e) for e in ESTADOS_HISTORICOS for p in provincias]

    ultimo = None
    if continuar:
        prev = db.get_sync_state("BOE Subastas Historico")
        ultimo = prev["last_combo"] if prev else None
    saltando = ultimo is not None

    total = 0
    for prov_cod, estado_cod in combos:
        clave = f"{prov_cod}:{estado_cod}"
        if saltando:
            if clave == ultimo:
                saltando = False
            continue

        lotes = _buscar_boe_fragmentado(scraper, prov_cod, estado_cod, desde, hasta)
        if lotes:
            msg = f"Histórico BOE - {PROVINCIAS[prov_cod]} / {ESTADOS[estado_cod]}: {len(lotes)} lotes"
            log.info(msg)
            if progreso:
                progreso(msg)
            for lote in lotes:
                if con_detalle:
                    lote.update(scraper.detalle(lote["id"]))
                db.upsert_lote(_lote_a_fila_db(lote))
                total += 1

        db.set_sync_state("BOE Subastas Historico", last_combo=clave)

    db.set_sync_state("BOE Subastas Historico", last_full_sync=datetime.now().isoformat(timespec="seconds"))
    return total


def sync_seg_social_historico(provincias=None, con_detalle=True, progreso=None, continuar=True):
    """Barrido completo de Seguridad Social: todas las provincias, todas
    las paginas de resultados (el sitio no tiene limite de resultados
    como BOE, solo pagina de a 20). Resumible igual que sync_boe_historico."""
    db.init_db()
    scraper = SegSocialScraper()
    provincias = provincias or list(PROVINCIAS.keys())

    ultimo = None
    if continuar:
        prev = db.get_sync_state("Seguridad Social Historico")
        ultimo = prev["last_combo"] if prev else None
    saltando = ultimo is not None

    total = 0
    for prov_cod in provincias:
        if saltando:
            if prov_cod == ultimo:
                saltando = False
            continue

        lotes = scraper.buscar_todas_paginas([prov_cod], tipos_bien=list(SS_TIPOS_BIEN.keys()))
        if lotes:
            msg = f"Histórico Seguridad Social - provincia {PROVINCIAS.get(prov_cod, prov_cod)}: {len(lotes)} lotes"
            log.info(msg)
            if progreso:
                progreso(msg)
            for lote in lotes:
                if con_detalle:
                    lote.update(scraper.detalle(lote["emb_id"]))
                db.upsert_lote(_lote_seg_social_a_fila_db(lote))
                total += 1

        db.set_sync_state("Seguridad Social Historico", last_combo=prov_cod)

    db.set_sync_state("Seguridad Social Historico", last_full_sync=datetime.now().isoformat(timespec="seconds"))
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
