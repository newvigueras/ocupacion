"""Estructuras de datos del motor."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date


@dataclass
class Partida:
    codigo: str
    tipo_planta: str  # 'injerto' | 'normal' | 'componente'
    cliente: str
    variedad: str
    categoria: str  # Jiffy, Hidro, Cotiledon, Pinzado, Normal, Patrón, Variedad
    cultivo: str
    medida: str  # '8x8', '5x5', '7.5x7.5', '10x10' o ''
    bio: str  # 'ECO', 'ECO BIO', 'BIO', 'No BIO' ('' o 'ECO' en componentes)
    bandejas: int
    entrada: date
    liberacion: date
    siembra: date | None
    siembra_real: date | None
    entrega: date
    formato: str
    estilo: str  # 'Almería' | 'Normal'
    etapa: str  # '2HV', '3HV', '4HV', 'COT' o 'Normal'
    tipo_siembra: str
    observaciones: str
    bandejas_excel: float | None  # columna Band tal cual
    plantas: float | None  # columna Pl sol
    plantas_por_bandeja: float | None  # tasa usada; None si se usó Band
    regla_bandejas: str  # explicación legible del cálculo
    planta_desarrollada: bool = False
    fila: int | None = None  # fila del Excel

    @property
    def eco(self) -> bool:
        return self.bio.startswith("ECO")


@dataclass
class Aviso:
    tipo: str  # omitida, repetida, sin_medida, sin_pl_sol, desarrollada, excepcion
    mensaje: str
    codigo: str = ""
    fila: int | None = None


@dataclass
class ResultadoImportacion:
    archivo: str
    hoja: str
    fila_cabecera: int
    partidas: dict[str, Partida] = field(default_factory=dict)
    avisos: list[Aviso] = field(default_factory=list)

    def avisos_de(self, tipo: str) -> list[Aviso]:
        return [a for a in self.avisos if a.tipo == tipo]

    @property
    def total_bandejas(self) -> int:
        return sum(p.bandejas for p in self.partidas.values())

    def resumen_por_cultivo(self) -> dict[str, tuple[int, int]]:
        """Cultivo → (nº de partidas, bandejas), en el orden de ``CULTIVOS``."""
        from .reglas import CULTIVOS

        acc: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for p in self.partidas.values():
            acc[p.cultivo][0] += 1
            acc[p.cultivo][1] += p.bandejas
        orden = {c: i for i, c in enumerate(CULTIVOS)}
        claves = sorted(acc, key=lambda c: (orden.get(c, len(orden)), c))
        return {c: (acc[c][0], acc[c][1]) for c in claves}

    def contar_avisos(self) -> Counter:
        return Counter(a.tipo for a in self.avisos)
