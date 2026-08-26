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
import unicodedata
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


def _normalizar_texto(texto):
    sin_tildes = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return sin_tildes.upper().strip()


# Seguridad Social escribe el nombre de provincia tal cual lo tiene su sitio
# (todo mayusculas, sin acentos consistentes - "MALAGA", a veces "MÁLAGA"),
# mientras que BOE (y el filtro de Provincia de la app) usan el nombre bien
# escrito ("Málaga"). Sin normalizar, filtrar por "Málaga" con Seguridad
# Social tildado no encontraba ninguna fila de esa fuente aunque existieran
# en la base - confirmado en vivo.
_PROVINCIA_CANONICA = {}
for _nombre in PROVINCIAS.values():
    _PROVINCIA_CANONICA[_normalizar_texto(_nombre)] = _nombre
    _PROVINCIA_CANONICA.setdefault(_normalizar_texto(_nombre.split("/")[0]), _nombre)


def _provincia_canonica(texto):
    return _PROVINCIA_CANONICA.get(_normalizar_texto(texto), texto)


def _lotes_a_entero(valor):
    if not valor:
        return 1
    v = str(valor).lower()
    if "sin lotes" in v:
        return 1
    m = re.search(r"\d+", v)
    return int(m.group(0)) if m else 1


# El filtro "Tipo de subasta" de la app comparaba contra tipo_subasta, un
# texto libre sacado del detalle de cada subasta (ej. "JUDICIAL EN VIA DE
# APREMIO") que solo tiene las opciones que ya se hayan sincronizado - por
# eso nunca coincidia con las categorias reales de BOE. El formulario real
# de busqueda avanzada de BOE (dato[0] / SUBASTA.ORIGEN) tiene 5 categorias
# fijas: Judicial/Notarial/AEAT/Otras administraciones tributarias/Subastas
# administrativas generales. Confirmado en vivo contra ese parametro: la
# primera letra del segundo tramo del id coincide siempre con la categoria
# (JA/JC/JV -> J, NE/NV/NH -> N, AT -> A, RC -> R, GA -> G), asi que se
# puede derivar sin pedidos extra al sitio.
_CATEGORIA_POR_LETRA = {
    "J": "Judicial",
    "N": "Notarial",
    "A": "AEAT",
    "R": "Otras administraciones tributarias",
    "G": "Subastas administrativas generales",
}


def _categoria_subasta(id_sub):
    m = re.match(r"SUB-([A-Z])", id_sub or "")
    return _CATEGORIA_POR_LETRA.get(m.group(1), "") if m else ""


def _lote_a_fila_db(lote: dict) -> dict:
    return {
        "id": lote["id"],
        "fuente": "BOE Subastas",
        "estado": lote.get("estado", ""),
        "tipo_subasta": lote.get("tipo_subasta", ""),
        "categoria_subasta": _categoria_subasta(lote["id"]),
        "tipo_bien": lote.get("tipo_bien", ""),
        "lotes": _lotes_a_entero(lote.get("lotes")),
        # provincia_busqueda es la provincia canonica con la que se hizo la
        # busqueda (coincide siempre con las opciones del filtro Provincia).
        # lote["provincia"] es texto de la pagina de detalle del bien, que
        # para provincias con nombre oficial doble BOE a veces muestra solo
        # la forma corta - confirmado en vivo: una subasta de Araba/Alava
        # mostraba "Provincia: Alava" en su ficha, no "Araba/Alava". Antes
        # se priorizaba ese texto de la ficha, asi que esa fila quedaba
        # guardada como "Alava" y el filtro (que busca "Araba/Alava" exacto)
        # nunca la encontraba - pasaba lo mismo para Alicante/Alacant,
        # Illes Balears, Castellon/Castello, Gipuzkoa, Bizkaia. Como ya se
        # sabe la provincia correcta desde la busqueda misma, no hace falta
        # arriesgarse con el texto de la ficha - se usa siempre esa primero.
        "provincia": lote.get("provincia_busqueda") or lote.get("provincia", ""),
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
        # Pedido explicito del cliente: la columna Nombre debe traer el
        # Acreedor (quien reclama la deuda), no el juzgado/organismo que
        # tramita el caso (que es lo que traia antes, via lote["nombre"] -
        # el titulo <h4> de la pagina de listado de BOE). El acreedor no
        # esta en todas las subastas (confirmado por el cliente y en vivo
        # contra BOE) - cuando no esta, se deja vacio a proposito, sin caer
        # de nuevo al nombre del juzgado.
        "nombre": lote.get("acreedor", ""),
        "fecha_inicio": lote.get("fecha_inicio", ""),
        "fecha_conclusion": lote.get("fecha_conclusion") or lote.get("fecha_fin_resumen", ""),
    }


# Seguridad Social usa su propia taxonomia de tipo de bien (Finca Rustica,
# Finca Urbana, Vehiculo, Embarcacion, Resto de Bienes Muebles), distinta de
# la de BOE (Inmueble/Vehiculo/Bien mueble). Se guarda tal cual viene del
# sitio -sin normalizar a las categorias de BOE- porque el cliente quiere
# poder filtrar por la categoria real de cada fuente (pedido explicito:
# "si te fijas en la web de seguridad social los filtros son distintos").
def _lote_seg_social_a_fila_db(lote: dict) -> dict:
    fecha = lote.get("fecha_subasta", "")
    return {
        "id": lote["id"],
        "fuente": "Seguridad Social",
        "estado": _inferir_estado(fecha),
        "tipo_subasta": "RECAUDACIÓN SEGURIDAD SOCIAL",
        # categoria_subasta (Judicial/Notarial/AEAT/...) es una clasificacion
        # propia del sitio de BOE, no existe como concepto en seg-social.es.
        "categoria_subasta": "",
        "tipo_bien": lote.get("tipo_bien", ""),
        "lotes": _lotes_a_entero(lote.get("lotes")),
        "provincia": _provincia_canonica(lote.get("provincia") or lote.get("provincia_busqueda", "")),
        "localidad": lote.get("localidad", ""),
        "direccion": lote.get("direccion", ""),
        # descripcion viene de detalle() (una consulta HTTP aparte, que
        # puede fallar - confirmado en vivo: en un archivo real del
        # cliente, ~20% de los lotes de Seguridad Social tenian TODOS los
        # campos de detalle vacios por un fallo de red en esa consulta
        # puntual). nombre ya viene de la pagina de listado (la misma
        # consulta que trae el lote en si, no falla por separado) y suele
        # tener un texto igual de descriptivo (ej. "BICICLETA DECORATIVA
        # CON PRODUCTOS PELUQUERIA") - usarlo de respaldo evita dejar la
        # columna Descripcion vacia cuando ya hay un texto util a mano.
        "descripcion": lote.get("descripcion") or lote.get("nombre", ""),
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
                filas_lote = scraper.detalle_lotes(lote["id"])
                ids_nuevos = []
                for fila_lote in filas_lote:
                    combinado = dict(lote)
                    combinado.update(fila_lote)
                    fila_db = _lote_a_fila_db(combinado)
                    db.upsert_lote(fila_db)
                    ids_nuevos.append(fila_db["id"])
                    total += 1
                if ids_nuevos:
                    # si esta subasta tenia mas lotes en una sincronizacion
                    # anterior (o antes tenia 1 y ahora tiene varios, o al
                    # reves), esto borra las filas que ya no corresponden -
                    # ver db.limpiar_lotes_huerfanos. Si filas_lote vino
                    # vacia (fallo de red en detalle_lotes) no se toca nada,
                    # se reintenta en la proxima sincronizacion.
                    db.limpiar_lotes_huerfanos(lote["id"], ids_nuevos)
            else:
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


VENTANA_DIAS_HISTORICO = 30
# Tamaño fijo de cada tramo de fecha del barrido historico. Antes el
# checkpoint resumible solo guardaba el ULTIMO COMBO provincia+estado
# terminado, y _buscar_boe_fragmentado biseccionaba el rango entero
# (2021..hoy) de una sola vez en memoria - si la app se cerraba a mitad de
# un combo (algo normal en una app de escritorio) se perdia TODO su avance:
# la proxima vez ese combo arrancaba de cero desde 2021 otra vez. Como la
# biseccion recorre primero la mitad mas vieja del rango, un combo
# interrumpido varias veces terminaba sincronizado solo en años viejos y
# nunca llegaba a los recientes - confirmado en vivo por el cliente: 182
# subastas concluidas en Malaga en 2026 en la web real de BOE, pero 0 en el
# export (el historico de Malaga se habia quedado pisando 2021-2022 sin
# avanzar mas alla). Partiendo el barrido en tramos fijos y guardando el
# progreso tramo a tramo (no solo combo a combo) se puede retomar dentro de
# un mismo combo en vez de perder ese avance.
def _ventanas_historico(desde, hasta, dias=VENTANA_DIAS_HISTORICO):
    """Tramos de tamaño fijo para cubrir desde..hasta. El limite de cada
    tramo es SIEMPRE cursor + dias-1 (nunca se recorta contra `hasta`) para
    que la clave de cada tramo no cambie de un dia a otro - `hasta` por
    defecto es hoy (ver sync_boe_historico), que avanza cada dia; si el
    ultimo tramo se recortara contra `hasta`, su limite cambiaria en cada
    corrida y el checkpoint guardado dejaria de coincidir con ningun tramo
    real, salteando ese combo para siempre sin retomarlo nunca. Pedir de
    mas (fechas un poco en el futuro) no trae resultados de mas: PC/FS son
    subastas YA concluidas, ninguna real va a tener fecha de conclusion
    futura."""
    d1, d2 = date.fromisoformat(desde), date.fromisoformat(hasta)
    tramos = []
    cursor = d1
    while cursor <= d2:
        fin = cursor + timedelta(days=dias - 1)
        tramos.append((cursor.isoformat(), fin.isoformat()))
        cursor = fin + timedelta(days=1)
    return tramos


def sync_boe_historico(desde=HISTORICO_DESDE, hasta=None, provincias=None, con_detalle=True, progreso=None, continuar=True):
    """Barrido historico completo de BOE (estados PC/FS = Concluida).
    Resumible tramo a tramo: guarda en sync_state el ultimo combo
    provincia+estado + tramo de fecha terminado, y la proxima vez arranca
    justo despues de ahi - ni repite el combo entero desde 2021, ni se
    saltea de largo un combo que quedo a mitad de camino."""
    db.init_db()
    hasta = hasta or date.today().isoformat()
    scraper = BOEScraper()
    provincias = provincias or list(PROVINCIAS.keys())
    combos = [(p, e) for e in ESTADOS_HISTORICOS for p in provincias]
    ventanas = _ventanas_historico(desde, hasta)

    combo_completo = None
    combo_parcial = None
    ventana_completa = None
    if continuar:
        prev = db.get_sync_state("BOE Subastas Historico")
        anterior = prev["last_combo"] if prev else None
        if anterior:
            partes = anterior.split(":")
            if len(partes) == 3:
                combo_parcial = f"{partes[0]}:{partes[1]}"
                ventana_completa = partes[2]
                # Si desde/VENTANA_DIAS_HISTORICO cambiaran entre corridas,
                # el tramo guardado podria no coincidir con ninguno de los
                # tramos actuales - sin este chequeo, el combo entero se
                # salteaba en silencio (saltando_ventana quedaba en True
                # toda la vuelta) y terminaba marcado como completo sin
                # haber bajado nada. Si el tramo guardado ya no existe, se
                # descarta el resumen parcial y ese combo se procesa entero
                # de nuevo en vez de perderlo silenciosamente.
                if ventana_completa not in (v[1] for v in ventanas):
                    combo_parcial = None
                    ventana_completa = None
            else:
                combo_completo = anterior
    saltando_combo = combo_completo is not None or combo_parcial is not None

    total = 0
    for prov_cod, estado_cod in combos:
        clave = f"{prov_cod}:{estado_cod}"
        if saltando_combo:
            if clave in (combo_completo, combo_parcial):
                saltando_combo = False
                if clave == combo_completo:
                    continue
                # combo_parcial: sigue de largo, se retoma a mitad de camino abajo
            else:
                continue

        saltando_ventana = clave == combo_parcial
        for v_desde, v_hasta in ventanas:
            if saltando_ventana:
                if v_hasta == ventana_completa:
                    saltando_ventana = False
                continue

            lotes = _buscar_boe_fragmentado(scraper, prov_cod, estado_cod, v_desde, v_hasta)
            if lotes:
                msg = f"Histórico BOE - {PROVINCIAS[prov_cod]} / {ESTADOS[estado_cod]} ({v_desde}..{v_hasta}): {len(lotes)} lotes"
                log.info(msg)
                if progreso:
                    progreso(msg)
                for lote in lotes:
                    if con_detalle:
                        filas_lote = scraper.detalle_lotes(lote["id"])
                        ids_nuevos = []
                        for fila_lote in filas_lote:
                            combinado = dict(lote)
                            combinado.update(fila_lote)
                            fila_db = _lote_a_fila_db(combinado)
                            db.upsert_lote(fila_db)
                            ids_nuevos.append(fila_db["id"])
                            total += 1
                        if ids_nuevos:
                            db.limpiar_lotes_huerfanos(lote["id"], ids_nuevos)
                    else:
                        db.upsert_lote(_lote_a_fila_db(lote))
                        total += 1

            db.set_sync_state("BOE Subastas Historico", last_combo=f"{clave}:{v_hasta}")

        # combo entero terminado (todos sus tramos) - se guarda SIN sufijo
        # de tramo para que la proxima vez se saltee entero de una y
        # arranque el siguiente combo desde su primer tramo, no a mitad.
        db.set_sync_state("BOE Subastas Historico", last_combo=clave)

    # El for termino la lista ENTERA sin cortarse (nadie hizo return/raise
    # antes) - fue una pasada completa de verdad, no una interrumpida a
    # mitad de camino. Reiniciar last_combo para que el proximo click
    # arranque una pasada nueva desde el principio (y agarre subastas
    # nuevas que hayan aparecido), en vez de creer que ya paso por todos
    # los combos y saltearselos todos sin bajar nada.
    db.set_sync_state(
        "BOE Subastas Historico",
        last_full_sync=datetime.now().isoformat(timespec="seconds"),
        reiniciar_combo=True,
    )
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

    # Mismo motivo que en sync_boe_historico: pasada completa de verdad,
    # reiniciar el combo para que la proxima vuelva a arrancar desde el
    # principio en vez de saltearse todas las provincias de largo.
    db.set_sync_state(
        "Seguridad Social Historico",
        last_full_sync=datetime.now().isoformat(timespec="seconds"),
        reiniciar_combo=True,
    )
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
