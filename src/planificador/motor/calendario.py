"""Calendario de semanas de domingo a sábado (documento maestro §5.1 y §10.7).

La semana 1 es la que contiene el 1 de enero del primer año. La numeración
interna es continua (1…n); la etiqueta es «semana del año/aa» cuando hay varios años.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Iterator

from .modelos import Partida


def inicio_semana1(año: int) -> date:
    """Domingo anterior o igual al 1 de enero."""
    uno = date(año, 1, 1)
    # weekday(): lunes=0 … domingo=6
    return uno - timedelta(days=(uno.weekday() + 1) % 7)


@dataclass(frozen=True)
class Semana:
    numero: int  # continuo: 1…n
    inicio: date  # recortado al calendario
    fin: date  # recortado al calendario
    sem: int  # semana del año de su sábado
    etiqueta: str  # '47' o '47/26'


@dataclass(frozen=True)
class PartidaEnSemana:
    partida: Partida
    nueva: bool  # entra esta semana
    sale: bool  # se libera esta semana


class Calendario:
    def __init__(self, año_inicio: int, año_fin: int | None = None):
        año_fin = año_fin or año_inicio
        if año_fin < año_inicio:
            raise ValueError("El año final no puede ser anterior al inicial")
        self.año_inicio = año_inicio
        self.año_fin = año_fin
        self.primer_dia = date(año_inicio, 1, 1)
        self.ultimo_dia = date(año_fin, 12, 31)
        self.origen = inicio_semana1(año_inicio)
        self.n_semanas = (self.ultimo_dia - self.origen).days // 7 + 1
        self.varios_años = año_fin > año_inicio

    @property
    def n_dias(self) -> int:
        return (self.ultimo_dia - self.primer_dia).days + 1

    def dias(self) -> Iterator[date]:
        for i in range(self.n_dias):
            yield self.primer_dia + timedelta(days=i)

    def contiene(self, dia: date) -> bool:
        return self.primer_dia <= dia <= self.ultimo_dia

    def semana_de(self, dia: date) -> int | None:
        if not self.contiene(dia):
            return None
        return (dia - self.origen).days // 7 + 1

    def semana(self, numero: int) -> Semana:
        if not 1 <= numero <= self.n_semanas:
            raise ValueError(f"Semana {numero} fuera del calendario (1…{self.n_semanas})")
        inicio = self.origen + timedelta(days=7 * (numero - 1))
        sabado = inicio + timedelta(days=6)
        sem = (sabado - date(sabado.year, 1, 1)).days // 7 + 1
        etiqueta = f"{sem}/{sabado.year % 100:02d}" if self.varios_años else str(sem)
        return Semana(
            numero=numero,
            inicio=max(inicio, self.primer_dia),
            fin=min(sabado, self.ultimo_dia),
            sem=sem,
            etiqueta=etiqueta,
        )

    def semanas(self) -> list[Semana]:
        return [self.semana(n) for n in range(1, self.n_semanas + 1)]

    def semanas_de_partida(self, p: Partida) -> range:
        """Semanas entre la de entrada y la de liberación (recortadas al calendario)."""
        desde = max(p.entrada, self.primer_dia)
        hasta = min(p.liberacion, self.ultimo_dia)
        if desde > hasta:
            return range(0)
        return range(self.semana_de(desde), self.semana_de(hasta) + 1)

    def partidas_de_semana(self, partidas: Iterable[Partida], numero: int) -> list[PartidaEnSemana]:
        res = []
        for p in partidas:
            if numero in self.semanas_de_partida(p):
                res.append(PartidaEnSemana(
                    partida=p,
                    nueva=self.semana_de(p.entrada) == numero,
                    sale=self.semana_de(p.liberacion) == numero,
                ))
        return res
