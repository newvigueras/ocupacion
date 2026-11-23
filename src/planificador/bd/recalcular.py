"""Recalcular todas las semanas (§10.4) y reimportar los Excel (§10.6).

**Recalcular** rehace las asignaciones de las tres bases con las partidas y reglas de hoy,
conservando a qué invernadero (y calle) va cada cosa:

- Bloques: lo puesto se agrupa por bloque actual + invernadero y se vuelve a repartir
  partida a partida, topado al total actual del bloque.
- Partidas y Real: se agrupa por partida + invernadero (+ calle) y se vuelve a poner.

Si ahora hay menos bandejas, se recorta; si hay más, lo asignado no sube (queda pendiente).
Los trozos de partidas que ya no existen («huérfanas») no se tocan. En Bloques, los de
partidas que han cambiado de semana tampoco (el bloque es de una semana); en Partidas y
Real, en cambio, la asignación se va con la partida a su nueva semana.

**Reimportar** lee varios Excel de previsión (el tipo de cada partida sale de su código),
los fusiona por código con el maestro (el más nuevo manda) y luego:

- *Desde cero* (por defecto): vacía Bloques y Partidas y lo puesto a mano en Real; en Real
  se queda lo cargado de EXTENSIONES sin calle y de EXTENSIONCALLES. Las fechas técnico y
  los datos reales (PDF incluidos) no se tocan.
- *Conservar*: recalcula las tres bases.

Antes de tocar una base se guarda una copia (tabla ``copia_seguridad``) que se puede
restaurar.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..motor import leer_excel
from ..motor.modelos import ResultadoImportacion
from .importar import ResumenGuardado, guardar_partidas
from .planificacion import SEPARAR_POR_CLIENTE, ErrorPlan, Planificador
from .tablas import BASES, Asignacion, Calle, CopiaSeguridad, DatoReal, Invernadero, Partida

#: Copias que se guardan por base (las más antiguas se borran).
COPIAS_POR_BASE = 10

_COLUMNAS = ("base", "semana", "codigo", "invernadero_id", "calle_id", "bandejas", "entrada",
             "liberacion", "tipo_planta", "cultivo", "categoria", "bio", "cliente")


# --- Copias de seguridad ---------------------------------------------------------------

def guardar_copia(s: Session, base: str, motivo: str) -> CopiaSeguridad | None:
    """Copia las asignaciones de ``base`` (None si está vacía)."""
    filas = list(s.scalars(select(Asignacion).where(Asignacion.base == base)
                           .order_by(Asignacion.id)))
    if not filas:
        return None
    datos = [{c: (v.isoformat() if isinstance(v := getattr(a, c), date) else v)
              for c in _COLUMNAS} for a in filas]
    copia = CopiaSeguridad(base=base, motivo=motivo, asignaciones=len(filas),
                           bandejas=sum(a.bandejas for a in filas),
                           datos=json.dumps(datos, ensure_ascii=False))
    s.add(copia)
    s.flush()
    viejas = list(s.scalars(select(CopiaSeguridad.id).where(CopiaSeguridad.base == base)
                            .order_by(CopiaSeguridad.id.desc()).offset(COPIAS_POR_BASE)))
    if viejas:
        s.execute(delete(CopiaSeguridad).where(CopiaSeguridad.id.in_(viejas)))
    return copia


def copias(s: Session, base: str | None = None) -> list[CopiaSeguridad]:
    q = select(CopiaSeguridad).order_by(CopiaSeguridad.id.desc())
    if base:
        q = q.where(CopiaSeguridad.base == base)
    return list(s.scalars(q))


def restaurar_copia(s: Session, copia_id: int) -> tuple[CopiaSeguridad, CopiaSeguridad | None]:
    """Deja la base como estaba en la copia.

    Antes copia lo que hay (si hay algo); devuelve la copia restaurada y esa copia previa.
    """
    copia = s.get(CopiaSeguridad, copia_id)
    if copia is None:
        raise ErrorPlan(f"No existe la copia {copia_id}")
    previa = guardar_copia(s, copia.base, f"antes de restaurar la copia {copia_id}")
    s.execute(delete(Asignacion).where(Asignacion.base == copia.base))
    for fila in json.loads(copia.datos):
        for c in ("entrada", "liberacion"):
            fila[c] = date.fromisoformat(fila[c]) if fila[c] else None
        s.add(Asignacion(**fila))
    s.flush()
    return copia, previa


# --- Recalcular -----------------------------------------------------------------------

@dataclass
class InformeRecalculo:
    base: str
    asignaciones_antes: int = 0
    bandejas_antes: int = 0
    asignaciones_despues: int = 0
    bandejas_despues: int = 0
    #: Bandejas que ya no caben porque la partida (o el bloque) tiene ahora menos.
    recortadas: int = 0
    #: Bandejas cuyo ECO / BIO ha cambiado.
    cambio_bio: int = 0
    #: Trozos de partidas que ya no existen (o sin fechas): se dejan sin tocar.
    huerfanas: int = 0
    #: Trozos de partidas que han cambiado de semana de entrada. En Bloques se dejan sin
    #: tocar; en Partidas y Real se mueven a la semana nueva.
    otra_semana: int = 0
    #: (semana, tipo o '', bandejas antes, bandejas después) de las semanas que cambian.
    semanas_cambiadas: list[tuple[int, str, int, int]] = field(default_factory=list)
    copia_id: int | None = None

    @property
    def vacia(self) -> bool:
        return self.asignaciones_antes == 0


def _totales(s: Session, base: str, por_tipo: bool) -> Counter:
    q = select(Asignacion.semana, Asignacion.tipo_planta, func.sum(Asignacion.bandejas)) \
        .where(Asignacion.base == base).group_by(Asignacion.semana, Asignacion.tipo_planta)
    total: Counter = Counter()
    for semana, tipo, n in s.execute(q):
        total[(semana, tipo if por_tipo else "")] += n
    return total


def _cuenta(s: Session, base: str) -> tuple[int, int]:
    n, b = s.execute(select(func.count(Asignacion.id), func.coalesce(func.sum(Asignacion.bandejas), 0))
                     .where(Asignacion.base == base)).one()
    return n, b


def recalcular_base(s: Session, base: str, *, copia: bool = True,
                    motivo: str = "recalcular") -> InformeRecalculo:
    if base not in BASES:
        raise ErrorPlan(f"Base desconocida: {base}")
    plan = Planificador(s)
    inf = InformeRecalculo(base)
    inf.asignaciones_antes, inf.bandejas_antes = _cuenta(s, base)
    if inf.vacia:
        return inf
    if copia:
        c = guardar_copia(s, base, motivo)
        inf.copia_id = c.id if c else None
    antes = _totales(s, base, base == "bloques")
    if base == "bloques":
        _recalcular_bloques(plan, inf)
    else:
        _recalcular_partidas(plan, base, inf)
    s.flush()
    despues = _totales(s, base, base == "bloques")
    for clave in sorted(set(antes) | set(despues)):
        if antes[clave] != despues[clave]:
            inf.semanas_cambiadas.append((clave[0], clave[1], antes[clave], despues[clave]))
    inf.asignaciones_despues, inf.bandejas_despues = _cuenta(s, base)
    return inf


def recalcular_todo(s: Session, *, motivo: str = "recalcular") -> list[InformeRecalculo]:
    """Recalcula Bloques, Partidas y Real, todas las semanas (§10.4)."""
    return [recalcular_base(s, base, motivo=motivo) for base in BASES]


def _recalcular_bloques(plan: Planificador, inf: InformeRecalculo) -> None:
    s, cal = plan.s, plan.calendario
    celdas: dict[tuple, int] = {}  # (semana, tipo, cultivo, categoria, bio, cliente, inv) → bandejas
    quitar = []
    for a in s.scalars(select(Asignacion).where(Asignacion.base == "bloques")
                       .order_by(Asignacion.id)):
        p = s.get(Partida, a.codigo)
        if p is None:
            inf.huerfanas += 1
            continue
        semana = cal.semana_de(p.entrada)
        if semana != a.semana:
            inf.otra_semana += 1
            continue
        if a.bio != p.bio:
            inf.cambio_bio += a.bandejas
        cliente = p.cliente if p.categoria in SEPARAR_POR_CLIENTE else ""
        clave = (semana, p.tipo_planta, p.cultivo, p.categoria, p.bio, cliente, a.invernadero_id)
        celdas[clave] = celdas.get(clave, 0) + a.bandejas
        quitar.append(a.id)
    for i in range(0, len(quitar), 500):
        s.execute(delete(Asignacion).where(Asignacion.id.in_(quitar[i:i + 500])))
    s.flush()

    bloques: dict[tuple[int, str], dict] = {}
    resto: dict[tuple, int] = {}
    for (semana, tipo, *clave_bloque, inv_id), valor in celdas.items():
        if (semana, tipo) not in bloques:
            bloques[(semana, tipo)] = {b.clave: b for b in plan.bloques_de_semana(semana, tipo)}
            for b in bloques[(semana, tipo)].values():
                resto[(semana, tipo, b.clave)] = b.total
        clave_bloque = tuple(clave_bloque)
        bloque = bloques[(semana, tipo)].get(clave_bloque)
        disponible = resto.get((semana, tipo, clave_bloque), 0)
        val = min(valor, disponible)
        resto[(semana, tipo, clave_bloque)] = disponible - val
        colocado = plan._repartir_bloque(semana, bloque, s.get(Invernadero, inv_id), val) \
            if bloque and val > 0 else 0
        inf.recortadas += valor - colocado


def _recalcular_partidas(plan: Planificador, base: str, inf: InformeRecalculo) -> None:
    s, cal = plan.s, plan.calendario
    celdas: dict[tuple[str, int, int | None], int] = {}
    quitar = []
    for a in s.scalars(select(Asignacion).where(Asignacion.base == base)
                       .order_by(Asignacion.id)):
        if base == "real":
            d = s.get(DatoReal, a.codigo)
            entrada = plan.fechas_reales(d)[0] if d and d.bandejas_real > 0 else None
            origen = s.get(Partida, a.codigo) or d
        else:
            origen = s.get(Partida, a.codigo)
            entrada = origen.entrada if origen else None
        semana = cal.semana_de(entrada) if entrada else None
        if semana is None:
            inf.huerfanas += 1
            continue
        if semana != a.semana:
            inf.otra_semana += 1
        if a.bio != (origen.bio or ""):
            inf.cambio_bio += a.bandejas
        clave = (a.codigo, a.invernadero_id, a.calle_id)
        celdas[clave] = celdas.get(clave, 0) + a.bandejas
        quitar.append(a.id)
    for i in range(0, len(quitar), 500):
        s.execute(delete(Asignacion).where(Asignacion.id.in_(quitar[i:i + 500])))
    s.flush()

    for (codigo, inv_id, calle_id), valor in celdas.items():
        calle = s.get(Calle, calle_id) if calle_id else None
        puesto = plan._poner_partida(base, codigo, s.get(Invernadero, inv_id), calle, valor)
        inf.recortadas += valor - puesto


# --- Reimportar -----------------------------------------------------------------------

def _se_queda_en_real(a: Asignacion, fuente: str | None) -> bool:
    """Lo que se queda en Real al reimportar «desde cero»."""
    return (fuente == "EXTENSIONES" and a.calle_id is None) or fuente == "EXTENSIONCALLES"


@dataclass
class ArchivoReimportado:
    nombre: str
    partidas: int
    omitidas: int
    tipos: Counter
    guardado: ResumenGuardado


@dataclass
class ResumenReimportar:
    modo: str  # 'cero' | 'conservar'
    archivos: list[ArchivoReimportado] = field(default_factory=list)
    #: Base → asignaciones quitadas («desde cero»).
    quitadas: dict[str, int] = field(default_factory=dict)
    copias: dict[str, int] = field(default_factory=dict)
    quedan_real: int = 0
    informes: list[InformeRecalculo] = field(default_factory=list)


def reimportar(s: Session, rutas: Sequence[str | Path], *, conservar: bool = False,
               lecturas: Sequence[ResultadoImportacion] | None = None) -> ResumenReimportar:
    """Reimporta los Excel de previsión y recalcula (§10.6).

    Se leen todos antes de tocar nada: si uno falla, la base queda como estaba.
    """
    rutas = sorted((Path(r) for r in rutas), key=lambda r: r.stat().st_mtime)  # el más nuevo manda
    lecturas = list(lecturas) if lecturas is not None else [leer_excel(r) for r in rutas]
    res = ResumenReimportar("conservar" if conservar else "cero")
    for lectura in lecturas:
        guardado = guardar_partidas(s, lectura)
        res.archivos.append(ArchivoReimportado(
            lectura.archivo, len(lectura.partidas), len(lectura.avisos_de("omitida")),
            Counter(p.tipo_planta for p in lectura.partidas.values()), guardado))
    s.flush()

    if conservar:
        res.informes = recalcular_todo(s, motivo="reimportar (conservar)")
        return res

    fuentes = dict(s.execute(select(DatoReal.codigo, DatoReal.fuente)).all())
    for base in BASES:
        copia = guardar_copia(s, base, "reimportar desde cero")
        if copia:
            res.copias[base] = copia.id
        asign = list(s.scalars(select(Asignacion).where(Asignacion.base == base)))
        quitar = [a.id for a in asign
                  if base != "real" or not _se_queda_en_real(a, fuentes.get(a.codigo))]
        for i in range(0, len(quitar), 500):
            s.execute(delete(Asignacion).where(Asignacion.id.in_(quitar[i:i + 500])))
        res.quitadas[base] = len(quitar)
        if base == "real":
            res.quedan_real = len(asign) - len(quitar)
    s.flush()
    if res.quedan_real:
        # Lo que queda en Real se pone al día con las partidas nuevas (ya hay copia).
        res.informes = [recalcular_base(s, "real", copia=False)]
    return res


__all__ = [
    "ArchivoReimportado", "InformeRecalculo", "ResumenReimportar", "copias", "guardar_copia",
    "recalcular_base", "recalcular_todo", "reimportar", "restaurar_copia",
]

