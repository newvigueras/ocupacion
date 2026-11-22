"""Carga del colocado real por calle desde EXTENSIONCALLES.xlsx (documento maestro §10.5b).

EXTENSIONCALLES.xlsx es una **foto de lo que hay ahora**: una fila por partida con una
sola ubicación («Inv-Sec» = invernadero-calle, p. ej. ``44-17``) y sus bandejas, sin
fechas. Se carga junto con los Excel de previsión (fechas de entrega) y, si se quiere,
con EXTENSIONES.xlsx (solo se usa su primera fecha de colocación).

1. «Inv-Sec»: Excel convirtió en fecha muchas celdas. Año de conversión = el más
   frecuente entre las fechas con día ≠ 1. Si la fecha es de ese año, día = invernadero
   y mes = calle («22-6» → 22/jun); si es de otro, mes = invernadero y año de dos cifras
   = calle («4-16» → abr/2016). Las de ese año con día 1 son ambiguas y se toman como
   invernadero 1.
2. Calles: en los invernaderos 1–4, 10 y 11 llevan delante el número del invernadero
   (INV1A 101–130). Se busca ``sec`` o ``inv×100+sec`` entre las calles de INVn, INVnA e
   INVnB (la mitad sale sola de la calle).
3. Cada fila es *calle* (se carga), *fuera* (invernadero que el planificador no tiene),
   *desconocida* (el invernadero sí, la calle no) o *sin ubicación*.
4. Entrada = primer movimiento de EXTENSIONES → la ya guardada en Real → la del maestro
   → F.Siembra + días medios por familia; nunca después de hoy. Salida como en
   EXTENSIONES; si ya pasó y no hay fecha técnico, se deja la de hoy (la foto dice que
   sigue ahí).
5. Reparto entre calles contiguas (estimación, se puede desactivar): en cada calle de
   inicio caben primero las partidas pequeñas; lo que sobra (las mayores primero) va a
   las calles más cercanas con hueco del mismo invernadero, alternando delante y detrás;
   si el invernadero está lleno, se queda en su calle.
6. Lo que esas partidas tuvieran en Real se sustituye; lo de las demás no se toca.
   Cargar otra vez da lo mismo.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..motor.excel import ErrorExcel, a_codigo, a_fecha, a_numero, a_texto
from .extensiones import (
    Archivos, _CABECERA_CALLES, _mapear, clasificar_archivos, escribir_dato, familia,
    leer_movimientos, preparar_partida,
)
from .planificacion import Planificador
from .tablas import Asignacion, Calle, DatoReal, Invernadero, Partida

FUENTE = "EXTENSIONCALLES"

#: Días medios de la siembra a la colocación por familia (medidos con EXTENSIONES).
DIAS_SIEMBRA_ENTRADA = {"H": 0, "B": 0, "P": 2, "C": 3, "numerica": 5, "EC": 5, "VE": 5,
                        "WE": 5, "YE": 5, "X": 6}
DIAS_SIEMBRA_ENTRADA_RESTO = 2

_EPOCA_EXCEL = date(1899, 12, 30)
_RE_INV_SEC = re.compile(r"^(\d+)\s*[-/.]\s*(\d+)$")


def redondear(x: float) -> int:
    """Redondeo a la mitad hacia arriba (como Math.round del planificador HTML)."""
    return math.floor(x + 0.5)


# --- Lectura ----------------------------------------------------------------------

@dataclass(frozen=True)
class InvSec:
    inv: int
    sec: int
    via: str  # 'texto', 'fecha' o 'ambigua'


def _como_fecha(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if v < 367:
            return None
        return _EPOCA_EXCEL + timedelta(days=round(v))
    return None


def leer_inv_sec(v: Any, año_ref: int) -> InvSec | None:
    """Descifra una celda «Inv-Sec» (texto o fecha hecha por Excel)."""
    if v is None or v == "":
        return None
    f = _como_fecha(v)
    if f is not None:
        if f.year == año_ref:
            return InvSec(f.day, f.month, "ambigua" if f.day == 1 else "fecha")
        return InvSec(f.month, f.year % 100, "fecha")
    if isinstance(v, (int, float)):
        return None
    m = _RE_INV_SEC.match(str(v).strip())
    return InvSec(int(m.group(1)), int(m.group(2)), "texto") if m else None


@dataclass
class FilaCalles:
    codigo: str
    cliente: str
    semilla: str
    siembra: date | None
    bandejas: float
    ubicacion: InvSec | None
    crudo: Any


@dataclass
class LecturaCalles:
    filas: list[FilaCalles]
    omitidas: int  # filas sin bandejas
    año_ref: int


def leer_calles(cabecera: int, filas: list[tuple], hoy: date | None = None) -> LecturaCalles:
    mapa = _mapear(filas[cabecera], _CABECERA_CALLES)

    def celda(fila, campo):
        i = mapa.get(campo)
        return fila[i] if i is not None and i < len(fila) else None

    años = Counter()
    for fila in filas[cabecera + 1:]:
        f = _como_fecha(celda(fila, "invsec"))
        if f is not None and f.day != 1:
            años[f.year] += 1
    año_ref = años.most_common(1)[0][0] if años else (hoy or date.today()).year

    leidas, omitidas = [], 0
    for fila in filas[cabecera + 1:]:
        codigo = a_codigo(celda(fila, "partida"))
        if not codigo:
            continue
        bandejas = a_numero(celda(fila, "bandejas"))
        if not bandejas or bandejas <= 0:
            omitidas += 1
            continue
        crudo = celda(fila, "invsec")
        leidas.append(FilaCalles(
            codigo=codigo, cliente=a_texto(celda(fila, "cliente"))[:40],
            semilla=a_texto(celda(fila, "semilla")), siembra=a_fecha(celda(fila, "f_siembra")),
            bandejas=bandejas, ubicacion=leer_inv_sec(crudo, año_ref), crudo=crudo,
        ))
    return LecturaCalles(leidas, omitidas, año_ref)


# --- Reparto entre calles contiguas -------------------------------------------------

@dataclass
class Trozo:
    """Bandejas de una partida que empiezan en una calle (``partes``: calle → bandejas)."""
    invernadero: str
    calle: int
    bandejas: int
    partes: dict[int, int] = field(default_factory=dict)
    _resto: int = 0


@dataclass
class ResumenReparto:
    repartidas: int = 0  # trozos que ocupan más de una calle
    movidas: int = 0  # bandejas fuera de su calle de inicio
    sin_hueco: int = 0  # bandejas que no caben en su invernadero


def repartir_en_calles(trozos: list[Trozo],
                       calles: dict[str, list[tuple[int, int]]]) -> ResumenReparto:
    """Reparte cada trozo entre su calle y las contiguas con hueco (§10.5b 5).

    ``calles``: invernadero → [(número, capacidad)] ordenado por número.
    """
    capacidad = {(inv, n): cap for inv, lista in calles.items() for n, cap in lista}
    usado: dict[tuple[str, int], int] = defaultdict(int)

    grupos: dict[tuple[str, int], list[Trozo]] = defaultdict(list)
    for t in trozos:
        grupos[(t.invernadero, t.calle)].append(t)
    for k, lista in grupos.items():  # 1) cada uno en su calle, los pequeños primero
        cap = capacidad.get(k, 0)
        libre = cap
        for t in sorted(lista, key=lambda t: t.bandejas):
            aqui = max(0, min(t.bandejas, libre)) if cap > 0 else t.bandejas
            t.partes = {t.calle: aqui} if aqui > 0 else {}
            t._resto = t.bandejas - aqui
            libre -= aqui
            usado[k] += aqui

    res = ResumenReparto()
    for t in sorted((t for t in trozos if t._resto > 0), key=lambda t: -t._resto):
        lista = calles[t.invernadero]
        p = next(i for i, (n, _) in enumerate(lista) if n == t.calle)
        resto = t._resto

        def poner(n: int, cap: int) -> None:
            nonlocal resto
            libre = cap - usado[(t.invernadero, n)]
            if libre <= 0:
                return
            x = min(libre, resto)
            t.partes[n] = t.partes.get(n, 0) + x
            usado[(t.invernadero, n)] += x
            resto -= x
            res.movidas += x

        d = 1
        while resto > 0 and (p + d < len(lista) or p - d >= 0):
            if p + d < len(lista):
                poner(*lista[p + d])
            if resto > 0 and p - d >= 0:
                poner(*lista[p - d])
            d += 1
        if resto > 0:  # 3) invernadero lleno: el resto se queda en su calle
            t.partes[t.calle] = t.partes.get(t.calle, 0) + resto
            usado[(t.invernadero, t.calle)] += resto
            res.sin_hueco += resto
        t._resto = 0

    for t in trozos:
        t.partes = dict(sorted(t.partes.items()))
        res.repartidas += len(t.partes) > 1
    return res


# --- Carga ----------------------------------------------------------------------------

@dataclass
class Cuenta:
    partidas: int = 0
    bandejas: int = 0


@dataclass
class ResumenCalles:
    archivo_calles: str = ""
    archivo_movimientos: str = ""
    archivos_prevision: list[str] = field(default_factory=list)
    filas: int = 0
    omitidas: int = 0
    año_ref: int = 0
    desde_fecha: int = 0  # celdas Inv-Sec rehechas desde una fecha
    ambiguas: int = 0
    en_calle: Cuenta = field(default_factory=Cuenta)
    fuera: Cuenta = field(default_factory=Cuenta)
    desconocida: Cuenta = field(default_factory=Cuenta)
    sin_ubicacion: Cuenta = field(default_factory=Cuenta)
    fuera_por_invernadero: dict[str, int] = field(default_factory=dict)
    calles_desconocidas: list[str] = field(default_factory=list)
    entrada: Counter = field(default_factory=Counter)  # movimientos/existente/maestro/siembra/hoy
    salida: Counter = field(default_factory=Counter)  # tecnico_o_pdf/maestro/prevision/estimada
    dudosas: list[str] = field(default_factory=list)
    retrasadas: Cuenta = field(default_factory=Cuenta)  # salida pasada: se deja hoy
    sustituidas_ext: int = 0
    sustituidas_calles: int = 0
    sustituidas_otras: int = 0
    reparto: ResumenReparto | None = None
    partidas_cargadas: int = 0
    asignaciones: int = 0
    bandejas_cargadas: int = 0
    calles_con_carga: int = 0
    invernaderos_con_carga: int = 0
    calles_excedidas: list[tuple[str, int, int, int]] = field(default_factory=list)  # inv, calle, carga, cap
    sin_semana: list[str] = field(default_factory=list)
    ext_sin_foto: Cuenta = field(default_factory=Cuenta)

    @property
    def exceso_calles(self) -> int:
        return sum(c - cap for _, _, c, cap in self.calles_excedidas)


@dataclass
class _Ubicada:
    bandejas: int
    estado: str  # 'calle', 'fuera', 'desconocida', 'sin'
    invernadero: str | None = None
    calle: int | None = None
    trozo: Trozo | None = None


def cargar_por_calles(s: Session, archivos: Sequence[str | Path] | Archivos, *,
                      repartir: bool = True, hoy: date | None = None) -> ResumenCalles:
    """Carga EXTENSIONCALLES.xlsx (con previsión y, si se quiere, EXTENSIONES) en Real."""
    arch = archivos if isinstance(archivos, Archivos) else clasificar_archivos(archivos)
    if arch.calles is None:
        raise ErrorExcel("Falta el Excel de calles (EXTENSIONCALLES)")
    hoy = hoy or date.today()
    ruta, cab, filas = arch.calles
    lectura = leer_calles(cab, filas, hoy)
    prevision = arch.prevision()
    primera: dict[str, date] = {}
    res = ResumenCalles(archivo_calles=ruta.name, archivos_prevision=arch.nombres_prevision,
                        filas=len(lectura.filas), omitidas=lectura.omitidas,
                        año_ref=lectura.año_ref)
    if arch.movimientos is not None:
        res.archivo_movimientos = arch.movimientos[0].name
        for m in leer_movimientos(*arch.movimientos[1:]):
            primera.setdefault(m.codigo, m.fecha)

    plan = Planificador(s)
    cal = plan.calendario

    # 1) Dónde está cada fila.
    invs = {i.nombre: i for i in s.scalars(select(Invernadero))}
    por_numero: dict[int, list[Calle]] = defaultdict(list)
    calles_de: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for inv in invs.values():
        m = re.match(r"^INV(\d+)[AB]?$", inv.nombre)
        if not m or inv.especial:
            continue
        for c in sorted(inv.calles, key=lambda c: c.numero):
            por_numero[int(m.group(1))].append(c)
            calles_de[inv.nombre].append((c.numero, c.capacidad))
    calle_id = {(c.invernadero.nombre, c.numero): c for cs in por_numero.values() for c in cs}

    def buscar(n: int, sec: int) -> Calle | None:
        return next((c for c in por_numero.get(n, ()) if c.numero in (sec, n * 100 + sec)), None)

    por_partida: dict[str, dict[str, Any]] = {}
    for f in lectura.filas:
        e = por_partida.setdefault(f.codigo, {"fila": f, "ubicadas": [], "total": 0})
        b = max(1, redondear(f.bandejas))
        x = f.ubicacion
        if x is None:
            u, cuenta = _Ubicada(b, "sin"), res.sin_ubicacion
        elif x.inv not in por_numero:
            u, cuenta = _Ubicada(b, "fuera"), res.fuera
            clave = f"INV {x.inv}"
            res.fuera_por_invernadero[clave] = res.fuera_por_invernadero.get(clave, 0) + b
        else:
            c = buscar(x.inv, x.sec)
            if c is None:
                u, cuenta = _Ubicada(b, "desconocida"), res.desconocida
                res.calles_desconocidas.append(f"{x.inv}-{x.sec}")
            else:
                u, cuenta = _Ubicada(b, "calle", c.invernadero.nombre, c.numero), res.en_calle
        if x is not None and x.via != "texto":
            res.desde_fecha += 1
        if x is not None and x.via == "ambigua":
            res.ambiguas += 1
        cuenta.partidas += 1
        cuenta.bandejas += b
        e["ubicadas"].append(u)
        e["total"] += b

    a_cargar = {c: e for c, e in por_partida.items()
                if any(u.estado == "calle" for u in e["ubicadas"])}
    if not a_cargar:
        return res

    # 1b) Reparto entre calles contiguas.
    trozos = []
    for e in a_cargar.values():
        for u in e["ubicadas"]:
            if u.estado == "calle":
                u.trozo = Trozo(u.invernadero, u.calle, u.bandejas)
                trozos.append(u.trozo)
    reparto = repartir_en_calles(trozos, calles_de)
    if repartir:
        res.reparto = reparto

    # 2) Fechas y datos de cada partida.
    maestro = {p.codigo: p for p in s.scalars(select(Partida))}
    reales = {d.codigo: d for d in s.scalars(select(DatoReal))}
    preparadas = []
    for codigo, e in a_cargar.items():
        f: FilaCalles = e["fila"]
        dato, p = reales.get(codigo), maestro.get(codigo)
        entrada, origen = primera.get(codigo), "movimientos"
        if entrada is None and dato is not None and dato.fec_entrada_real:
            entrada, origen = dato.fec_entrada_real, "existente"
        if entrada is None and p is not None and p.entrada:
            entrada, origen = p.entrada, "maestro"
        if entrada is None and f.siembra:
            dias = DIAS_SIEMBRA_ENTRADA.get(familia(codigo), DIAS_SIEMBRA_ENTRADA_RESTO)
            entrada, origen = f.siembra + timedelta(days=dias), "siembra"
        if entrada is None:
            entrada, origen = hoy, "hoy"
        entrada = max(min(entrada, hoy), cal.primer_dia)
        res.entrada[origen] += 1

        prep = preparar_partida(plan, codigo, entrada, p, prevision.get(codigo), dato,
                                variedad_extra=f.semilla or "OTROS", cliente_extra=f.cliente)
        res.salida[prep.origen] += 1
        if prep.dudosa:
            res.dudosas.append(codigo)
        # La foto dice que sigue en el invernadero: si su salida ya pasó, se deja hasta hoy.
        if (prep.liberacion_ext is not None and prep.liberacion_ext < hoy
                and not plan.fecha_tecnico(codigo)):
            prep.liberacion_ext = hoy
            res.retrasadas.partidas += 1
            res.retrasadas.bandejas += e["total"]
        preparadas.append((entrada, codigo, e, prep))

    # 3) Lo que esas partidas tuvieran en Real se sustituye.
    codigos = list(a_cargar)
    for i in range(0, len(codigos), 500):
        lote = codigos[i:i + 500]
        for a in s.scalars(select(Asignacion).where(Asignacion.base == "real",
                                                    Asignacion.codigo.in_(lote))):
            fuente = reales[a.codigo].fuente if a.codigo in reales else ""
            if fuente == FUENTE:
                res.sustituidas_calles += 1
            elif fuente == "EXTENSIONES" and a.calle_id is None:
                res.sustituidas_ext += 1
            else:
                res.sustituidas_otras += 1
        s.execute(delete(Asignacion).where(Asignacion.base == "real",
                                           Asignacion.codigo.in_(lote)))
    s.flush()

    # 4) Una asignación por partida y calle.
    carga: dict[tuple[str, int], int] = defaultdict(int)
    for entrada, codigo, e, prep in sorted(preparadas, key=lambda x: (x[0], x[1])):
        colocadas = [u for u in e["ubicadas"] if u.estado == "calle"]
        partes = lambda u: u.trozo.partes if repartir and u.trozo.partes else {u.calle: u.bandejas}  # noqa: E731

        def localiza(u: _Ubicada) -> str:
            ns = list(partes(u))
            texto = (f"{ns[0]} y {len(ns) - 1} más" if len(ns) > 3
                     else ", ".join(map(str, ns)))
            return f"{u.invernadero} calle {texto}"

        dato = escribir_dato(s, reales.get(codigo), codigo, prep, bandejas=e["total"],
                             entrada=entrada, localizacion=", ".join(map(localiza, colocadas)),
                             fuente=FUENTE)
        reales[codigo] = dato
        semana = cal.semana_de(entrada)
        if semana is None:
            res.sin_semana.append(codigo)
            continue
        _, liberacion = plan.fechas_reales(dato)
        por_calle: dict[tuple[str, int], int] = defaultdict(int)
        for u in colocadas:
            for n, b in partes(u).items():
                por_calle[(u.invernadero, n)] += b
        for (inv, n), b in por_calle.items():
            if b <= 0:
                continue
            s.add(Asignacion(
                base="real", semana=semana, codigo=codigo, invernadero_id=invs[inv].id,
                calle_id=calle_id[(inv, n)].id, bandejas=b, entrada=entrada,
                liberacion=liberacion, tipo_planta=prep.tipo_planta, cultivo=prep.cultivo,
                categoria=prep.categoria, bio=prep.bio, cliente=prep.cliente,
            ))
            carga[(inv, n)] += b
            res.asignaciones += 1
            res.bandejas_cargadas += b
        res.partidas_cargadas += 1
    s.flush()

    # 5) Avisos.
    res.calles_con_carga = len(carga)
    res.invernaderos_con_carga = len({inv for inv, _ in carga})
    for (inv, n), b in carga.items():
        cap = calle_id[(inv, n)].capacidad
        if b > cap:
            res.calles_excedidas.append((inv, n, b, cap))
    res.calles_excedidas.sort(key=lambda x: -(x[2] - x[3]))
    en_foto = {f.codigo for f in lectura.filas}
    sin_foto = s.execute(
        select(Asignacion.codigo, Asignacion.bandejas)
        .join(DatoReal, DatoReal.codigo == Asignacion.codigo)
        .where(Asignacion.base == "real", Asignacion.calle_id.is_(None),
               DatoReal.fuente == "EXTENSIONES", Asignacion.liberacion > hoy)
    ).all()
    sin_foto = [(c, b) for c, b in sin_foto if c not in en_foto]
    res.ext_sin_foto = Cuenta(len({c for c, _ in sin_foto}), sum(b for _, b in sin_foto))
    return res
