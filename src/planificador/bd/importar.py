"""Guardar en la base de datos las partidas leídas de los Excel de previsión (§10.6).

Se fusiona **por código** con lo que ya hay: las partidas que no vienen en el Excel
se mantienen, y las fechas técnico y las asignaciones no se tocan.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..motor.modelos import Partida as PartidaMotor
from ..motor.modelos import ResultadoImportacion
from .tablas import Partida

#: Campos que se copian del motor a la tabla (todos menos ``fila``).
_CAMPOS = [f.name for f in fields(PartidaMotor) if f.name != "fila"]


@dataclass
class ResumenGuardado:
    nuevas: int = 0
    actualizadas: int = 0
    sin_cambios: int = 0

    @property
    def total(self) -> int:
        return self.nuevas + self.actualizadas + self.sin_cambios


def guardar_partidas(s: Session, res: ResultadoImportacion) -> ResumenGuardado:
    resumen = ResumenGuardado()
    codigos = list(res.partidas)
    existentes = {
        p.codigo: p for p in s.scalars(select(Partida).where(Partida.codigo.in_(codigos)))
    } if codigos else {}
    for codigo, pm in res.partidas.items():
        valores = {c: getattr(pm, c) for c in _CAMPOS}
        fila = existentes.get(codigo)
        if fila is None:
            s.add(Partida(**valores, archivo=res.archivo))
            resumen.nuevas += 1
            continue
        cambia = any(getattr(fila, c) != v for c, v in valores.items())
        if cambia:
            for c, v in valores.items():
                setattr(fila, c, v)
            fila.archivo = res.archivo
            resumen.actualizadas += 1
        else:
            resumen.sin_cambios += 1
    s.flush()
    return resumen
