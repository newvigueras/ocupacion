"""Carga del colocado real desde EXTENSIONES.xlsx en la base Real (documento maestro §10.5).

EXTENSIONES.xlsx es el Excel de **movimientos** de extensión (cabecera con «Nº Partida»,
«Invernadero Destino» y «NºBand»). Junto a él se pueden dar uno o varios Excel de
**previsión** (cabecera con PARTIDA, F Siembra y F Entrega), de los que se toman la
fecha de entrega, la variedad, el cliente y la lucha biológica de las partidas que no
están en el maestro. El tipo de cada archivo se reconoce por su cabecera.

1. Posición neta por partida e invernadero: ``P`` suma al destino; ``C`` resta del
   origen y suma al destino; se descartan los saldos ≤ 0.
2. Por partida: entrada = fecha del primer movimiento; bandejas = suma de saldos.
3. Salida = fecha técnico → salida real (PDF de cargas) → liberación del maestro →
   F Entrega + 3 de los Excel de previsión → **estimada**: entrada + días medios de su
   familia + 3. Una fecha a más de 150 días de la colocación es «dudosa» y se estima.
4. «INVERNADERO N» → INVN; si no existe pero sí INVNA/INVNB, va a la mitad con menos
   ocupación el día de entrada. SURINVER, TÚNELES y los invernaderos que el
   planificador no tiene no se cargan (quedan en el resumen).
5. Una asignación por partida e invernadero, sin calle. No se tocan las partidas de
   Real con trabajo a mano (con calle, de PDF o manuales). Cargar otra vez rehace
   solo lo que vino de EXTENSIONES.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..motor import clasificacion as C
from ..motor.excel import ErrorExcel, a_codigo, a_fecha, a_numero, a_texto, normalizar_cabecera
from ..motor.reglas import DIAS_LIBERACION
from .planificacion import Planificador
from .tablas import Asignacion, DatoReal, Invernadero, Partida

FUENTE = "EXTENSIONES"
#: Fuentes de Real hechas a mano o con datos mejores: la carga no las toca.
FUENTES_PROTEGIDAS = frozenset({"MANUAL", "PDF_TUNELES", "PDF_CARGAS", "EXTENSIONCALLES"})

#: Días medios de colocación a entrega por familia de código (medidos con 0110.xlsx).
DIAS_ENTREGA_FAMILIA = {
    "numerica": 48, "X": 14, "EC": 43, "C": 27, "P": 34, "YE": 10, "VE": 26, "WE": 29,
    "B": 43, "H": 46,
}
DIAS_ENTREGA_RESTO = 30
#: Más días que estos entre la colocación y la salida: fecha dudosa, se estima.
DIAS_DUDOSA = 150
SIN_DATOS_CLIENTE = "(sin datos: solo en EXTENSIONES)"

# --- Archivos -------------------------------------------------------------------

_CABECERA_MOV = {
    "partida": ("partida", "npartida", "nopartida"),
    "fecha": ("fecha",),
    "hora": ("hora",),
    "origen": ("invernaderoorigen",),
    "destino": ("invernaderodestino",),
    "tipo": ("tipo",),
    "bandejas": ("nband", "noband", "nbandejas", "bandejas"),
}
_CABECERA_PREV = {
    "partida": ("partida", "npartida", "nopartida"),
    "f_siembra": ("fsiembra", "fechasiembra"),
    "f_entrega": ("fentrega", "fechaentrega"),
    "variedad": ("variedad",),
    "cliente": ("nombrecliente", "cliente"),
    "lucha": ("partidabiologica",),
}


def _clave(valor: Any) -> str:
    return normalizar_cabecera(valor).replace(" ", "")


def _mapear(fila: Sequence[Any], columnas: dict[str, tuple[str, ...]]) -> dict[str, int]:
    claves = [_clave(v) for v in fila]
    mapa = {}
    for campo, alias in columnas.items():
        for i, k in enumerate(claves):
            if k in alias:
                mapa[campo] = i
                break
    return mapa


def tipo_de_cabecera(fila: Sequence[Any]) -> str | None:
    """'movimientos', 'prevision' o None."""
    mov = _mapear(fila, _CABECERA_MOV)
    if {"partida", "destino", "bandejas"} <= mov.keys():
        return "movimientos"
    prev = _mapear(fila, _CABECERA_PREV)
    if {"partida", "f_siembra", "f_entrega"} <= prev.keys():
        return "prevision"
    return None


def _leer_hojas(ruta: Path) -> Iterable[tuple[str, list[tuple]]]:
    from zipfile import BadZipFile

    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    try:
        wb = load_workbook(ruta, read_only=True, data_only=True)
    except (InvalidFileException, BadZipFile, KeyError) as e:
        raise ErrorExcel(f"{ruta.name}: no se puede abrir como Excel .xlsx ({e})") from e
    try:
        for ws in wb.worksheets:
            yield ws.title, [tuple(f) for f in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def clasificar_archivo(ruta: str | Path) -> tuple[str, int, list[tuple]]:
    """Devuelve (tipo, fila de cabecera, filas) de la primera hoja reconocible."""
    ruta = Path(ruta)
    for _, filas in _leer_hojas(ruta):
        for i, fila in enumerate(filas[:15]):
            tipo = tipo_de_cabecera(fila)
            if tipo:
                return tipo, i, filas
    raise ErrorExcel(f"{ruta.name}: no es un Excel de movimientos (EXTENSIONES) ni de previsión")


# --- Movimientos ----------------------------------------------------------------

@dataclass(frozen=True)
class Movimiento:
    codigo: str
    fecha: date
    hora: Any
    origen: str  # etiqueta normalizada ('' si no hay)
    destino: str
    tipo: str  # 'P' o 'C'
    bandejas: float


_RE_INVERNADERO = re.compile(r"^INV\w*\s*\.?\s*(\d+)\s*([AB])?\b\s*(.*)$")


def etiqueta_invernadero(texto: Any) -> str:
    """«INVERNADERO 16» → 'INV16'; «INVERANDERO 33» → 'INV33';
    «INVERNADERO 7 SURINVER» → 'INV7 SURINVER'; «TUNELES» → 'TUNELES'."""
    t = C.mayus(texto)
    if not t:
        return ""
    m = _RE_INVERNADERO.match(t)
    if not m:
        return re.sub(r"\s+", " ", t)
    numero, lado, resto = m.groups()
    etiqueta = f"INV{int(numero)}{lado or ''}"
    return f"{etiqueta} {resto.strip()}" if resto.strip() else etiqueta


def leer_movimientos(cabecera: int, filas: list[tuple]) -> list[Movimiento]:
    mapa = _mapear(filas[cabecera], _CABECERA_MOV)

    def celda(fila, campo):
        i = mapa.get(campo)
        return fila[i] if i is not None and i < len(fila) else None

    movs = []
    for fila in filas[cabecera + 1:]:
        codigo = a_codigo(celda(fila, "partida"))
        fecha = a_fecha(celda(fila, "fecha"))
        bandejas = a_numero(celda(fila, "bandejas"))
        if not codigo or fecha is None or not bandejas:
            continue
        movs.append(Movimiento(
            codigo=codigo, fecha=fecha, hora=celda(fila, "hora"),
            origen=etiqueta_invernadero(celda(fila, "origen")),
            destino=etiqueta_invernadero(celda(fila, "destino")),
            tipo=C.mayus(celda(fila, "tipo")) or "P", bandejas=bandejas,
        ))
    movs.sort(key=lambda m: (m.fecha, str(m.hora or "")))
    return movs


def posiciones_netas(movs: Iterable[Movimiento]) -> dict[str, dict[str, float]]:
    """codigo → {etiqueta de invernadero → bandejas} (solo saldos > 0)."""
    saldo: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for m in movs:
        if m.tipo == "C" and m.origen:
            saldo[m.codigo][m.origen] -= m.bandejas
        if m.destino:
            saldo[m.codigo][m.destino] += m.bandejas
    return {c: {inv: b for inv, b in invs.items() if b > 0}
            for c, invs in saldo.items() if any(b > 0 for b in invs.values())}


# --- Previsión (datos de partidas que no están en el maestro) ----------------------

@dataclass
class DatosPrevision:
    entrega: date | None
    variedad: str
    cliente: str
    lucha: bool


def leer_prevision(cabecera: int, filas: list[tuple]) -> dict[str, DatosPrevision]:
    mapa = _mapear(filas[cabecera], _CABECERA_PREV)

    def celda(fila, campo):
        i = mapa.get(campo)
        return fila[i] if i is not None and i < len(fila) else None

    datos = {}
    for fila in filas[cabecera + 1:]:
        codigo = a_codigo(celda(fila, "partida"))
        if not codigo:
            continue
        datos[codigo] = DatosPrevision(
            entrega=a_fecha(celda(fila, "f_entrega")),
            variedad=a_texto(celda(fila, "variedad")),
            cliente=a_texto(celda(fila, "cliente")),
            lucha=C.es_lucha_biologica(celda(fila, "lucha")),
        )
    return datos


# --- Fechas -----------------------------------------------------------------------

def familia(codigo: str) -> str:
    c = C.mayus(codigo)
    if c[:1] in ("B", "H"):
        return c[0]
    m = re.match(r"^\d{2}([A-Z]*)", c)
    if not m:
        return "resto"
    letras = m.group(1)
    if not letras:
        return "numerica"
    return letras if letras in DIAS_ENTREGA_FAMILIA else "resto"


def salida_estimada(codigo: str, entrada: date) -> date:
    dias = DIAS_ENTREGA_FAMILIA.get(familia(codigo), DIAS_ENTREGA_RESTO)
    return entrada + timedelta(days=dias + DIAS_LIBERACION)


# --- Carga ------------------------------------------------------------------------

@dataclass
class ResumenExtensiones:
    archivo_movimientos: str = ""
    archivos_prevision: list[str] = field(default_factory=list)
    movimientos: int = 0
    partidas: int = 0  # con algo colocado
    bandejas_netas: float = 0
    salida_tecnico_o_pdf: int = 0
    salida_maestro: int = 0
    salida_prevision: int = 0
    salida_estimada: int = 0
    dudosas: list[str] = field(default_factory=list)
    partidas_cargadas: int = 0
    asignaciones: int = 0
    bandejas_cargadas: int = 0
    no_cargadas: dict[str, float] = field(default_factory=dict)  # etiqueta → bandejas
    conservadas: list[str] = field(default_factory=list)  # Real con trabajo a mano
    fuera_de_calendario: list[str] = field(default_factory=list)

    @property
    def bandejas_no_cargadas(self) -> float:
        return sum(self.no_cargadas.values())


def cargar_extensiones(s: Session, archivos: Sequence[str | Path]) -> ResumenExtensiones:
    """Carga uno o varios archivos (movimientos + previsión) en la base Real."""
    movimientos: list[tuple[float, Path, int, list[tuple]]] = []
    previsiones: list[tuple[float, Path, int, list[tuple]]] = []
    for archivo in archivos:
        ruta = Path(archivo)
        tipo, cab, filas = clasificar_archivo(ruta)
        destino = movimientos if tipo == "movimientos" else previsiones
        destino.append((ruta.stat().st_mtime, ruta, cab, filas))
    if not movimientos:
        raise ErrorExcel("Falta el Excel de movimientos (EXTENSIONES)")

    # Si hay varios del mismo tipo, manda el más reciente.
    movimientos.sort(key=lambda x: x[0])
    previsiones.sort(key=lambda x: x[0])
    _, ruta_mov, cab_mov, filas_mov = movimientos[-1]
    movs = leer_movimientos(cab_mov, filas_mov)
    prevision: dict[str, DatosPrevision] = {}
    for _, _, cab, filas in previsiones:
        prevision.update(leer_prevision(cab, filas))

    res = ResumenExtensiones(archivo_movimientos=ruta_mov.name,
                             archivos_prevision=[r.name for _, r, _, _ in previsiones],
                             movimientos=len(movs))
    netas = posiciones_netas(movs)
    entrada_de = {}
    for m in movs:
        entrada_de.setdefault(m.codigo, m.fecha)
    res.partidas = len(netas)
    res.bandejas_netas = sum(sum(v.values()) for v in netas.values())

    plan = Planificador(s)
    cal = plan.calendario
    invs = {i.nombre: i for i in s.scalars(select(Invernadero))}

    # Partidas de Real que no se tocan.
    con_calle = set(s.scalars(select(Asignacion.codigo).where(
        Asignacion.base == "real", Asignacion.calle_id.is_not(None))))
    reales = {d.codigo: d for d in s.scalars(select(DatoReal))}
    protegidas = con_calle | {c for c, d in reales.items() if d.fuente in FUENTES_PROTEGIDAS}

    # Rehacer lo que vino de EXTENSIONES (sin calle).
    de_ext = [c for c, d in reales.items() if d.fuente == FUENTE and c not in protegidas]
    for i in range(0, len(de_ext), 500):
        s.execute(delete(Asignacion).where(
            Asignacion.base == "real", Asignacion.calle_id.is_(None),
            Asignacion.codigo.in_(de_ext[i:i + 500])))
    s.flush()

    # Ocupación de Real en memoria, para elegir la mitad A/B con menos ocupación.
    tramos: dict[int, list[tuple[date, date, int]]] = defaultdict(list)
    for a in s.scalars(select(Asignacion).where(Asignacion.base == "real")):
        t = plan._tramo(a)
        tramos[t.invernadero_id].append((t.entrada, t.liberacion, t.bandejas))

    def ocupado(inv: Invernadero, dia: date) -> int:
        return sum(b for e, l, b in tramos[inv.id] if e <= dia <= l)

    maestro = {p.codigo: p for p in s.scalars(select(Partida))}

    for codigo in sorted(netas, key=lambda c: (entrada_de[c], c)):
        if codigo in protegidas:
            res.conservadas.append(codigo)
            continue
        entrada = entrada_de[codigo]
        p = maestro.get(codigo)
        prev = prevision.get(codigo)

        # Salida
        tecnico = plan.fecha_tecnico(codigo)
        dato = reales.get(codigo)
        salida_pdf = dato.fecha_salida_real if dato else None
        liberacion_ext: date | None = None
        estimada = False
        if tecnico or salida_pdf:
            res.salida_tecnico_o_pdf += 1
        else:
            candidatas = []
            if p is not None:
                candidatas.append(("maestro", p.liberacion))
            if prev is not None and prev.entrega is not None:
                candidatas.append(("prevision", prev.entrega + timedelta(days=DIAS_LIBERACION)))
            for origen, fecha in candidatas:
                if (fecha - entrada).days > DIAS_DUDOSA:
                    if codigo not in res.dudosas:
                        res.dudosas.append(codigo)
                    continue
                liberacion_ext = fecha
                if origen == "maestro":
                    res.salida_maestro += 1
                else:
                    res.salida_prevision += 1
                break
            if liberacion_ext is None:
                liberacion_ext = salida_estimada(codigo, entrada)
                estimada = True
                res.salida_estimada += 1

        # Datos de la partida
        if p is not None:
            tipo, cliente, variedad = p.tipo_planta, p.cliente, p.variedad
            categoria, cultivo, bio = p.categoria, p.cultivo, p.bio
            estilo, formato = p.estilo, p.formato
        else:
            tipo = C.tipo_planta_de_codigo(codigo)
            variedad = prev.variedad if prev else ""
            cliente = prev.cliente if prev and prev.cliente else SIN_DATOS_CLIENTE
            categoria = C.categoria(codigo, tipo, variedad)
            cultivo = C.cultivo(variedad, categoria) if variedad else "Otros"
            bio = C.bio_valor(codigo, tipo, prev.lucha if prev else False)
            estilo, formato = "", ""

        total = round(sum(netas[codigo].values()))
        if dato is None:
            dato = DatoReal(codigo=codigo)
            s.add(dato)
        dato.bandejas_real = total
        dato.fec_entrada_real = entrada
        dato.fecha_liberacion_ext = liberacion_ext
        dato.liberacion_estimada = estimada
        dato.localizacion = ", ".join(sorted(netas[codigo]))
        dato.tipo_planta, dato.cliente, dato.variedad = tipo, cliente, variedad
        dato.categoria, dato.cultivo, dato.bio = categoria, cultivo, bio
        dato.estilo, dato.formato = estilo, formato
        dato.fuente = FUENTE
        s.flush()

        _, liberacion = plan.fechas_reales(dato)
        semana = cal.semana_de(entrada)
        if semana is None:
            res.fuera_de_calendario.append(codigo)
            continue

        cargada = False
        for etiqueta, bandejas in sorted(netas[codigo].items()):
            inv = invs.get(etiqueta)
            if inv is None and not etiqueta.endswith(("A", "B")):
                mitades = [invs[n] for n in (etiqueta + "A", etiqueta + "B") if n in invs]
                if mitades:
                    inv = min(mitades, key=lambda i: (ocupado(i, entrada), i.nombre))
            if inv is None or inv.especial:
                res.no_cargadas[etiqueta] = res.no_cargadas.get(etiqueta, 0) + bandejas
                continue
            n = round(bandejas)
            if n <= 0:
                continue
            s.add(Asignacion(
                base="real", semana=semana, codigo=codigo, invernadero_id=inv.id, calle_id=None,
                bandejas=n, entrada=entrada, liberacion=liberacion, tipo_planta=tipo,
                cultivo=cultivo, categoria=categoria, bio=bio, cliente=cliente,
            ))
            tramos[inv.id].append((entrada, liberacion or cal.ultimo_dia, n))
            res.asignaciones += 1
            res.bandejas_cargadas += n
            cargada = True
        res.partidas_cargadas += cargada
    s.flush()
    return res
