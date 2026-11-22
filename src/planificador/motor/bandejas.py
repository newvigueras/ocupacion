"""Cálculo de bandejas (documento maestro §7).

La columna Band del Excel no es fiable en Jiffy, hidropónico, tacos, pepino y
calabacín: ahí se usa ``round(Pl sol / plantas por bandeja)`` con 0,5 hacia arriba.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import reglas as R
from .clasificacion import mayus


@dataclass
class CalculoBandejas:
    bandejas: int
    tasa: float | None  # plantas por bandeja usadas; None = columna Band
    regla: str
    medida: str = ""
    desarrollada: bool = False
    avisos: list[tuple[str, str]] = field(default_factory=list)  # (tipo, mensaje)


def redondear(valor: float) -> int:
    """Redondeo habitual: 0,5 sube."""
    return int(math.floor(valor + 0.5))


def medida_jiffy(observaciones: str, tipo_siembra: str) -> str:
    m = R.JIFFY_MARCA.search(observaciones or "")
    if m:
        return f"{m.group(1)}x{m.group(1)}"
    return R.JIFFY_POR_TIPO.get(mayus(tipo_siembra), "")


def medida_hidro(observaciones: str, variedad: str, tipo_siembra: str) -> str:
    for texto in (observaciones or "", variedad or ""):
        if R.HIDRO_MARCA_75.search(texto):
            return "7.5x7.5"
        if R.HIDRO_MARCA_10.search(texto):
            return "10x10"
    return R.HIDRO_POR_TIPO.get(mayus(tipo_siembra), "")


def calcular_bandejas(
    *,
    codigo: str,
    tipo_planta: str,
    categoria: str,
    tipo_siembra: str,
    observaciones: str,
    variedad: str,
    band: float | None,
    pl_sol: float | None,
    reglas: R.Reglas,
) -> CalculoBandejas:
    tipo = mayus(tipo_siembra)
    obs = observaciones or ""
    band_val = band if band is not None else 0

    medida = ""
    if categoria == "Jiffy":
        medida = medida_jiffy(obs, tipo)
    elif categoria == "Hidro":
        medida = medida_hidro(obs, variedad, tipo)

    def por_plantas(tasa: float, regla: str, desarrollada: bool = False) -> CalculoBandejas:
        if not pl_sol or pl_sol <= 0:
            return CalculoBandejas(
                redondear(band_val), None, "Band (falta Pl sol)", medida, desarrollada,
                [("sin_pl_sol", f"{regla}: sin Pl sol; se usa la columna Band")],
            )
        tasa_txt = f"{tasa:g}"
        return CalculoBandejas(
            redondear(pl_sol / tasa), tasa, f"{regla} (Pl sol ÷ {tasa_txt})", medida, desarrollada
        )

    # 1. Excepción por partida: manda sobre todo.
    exc = reglas.excepcion(codigo)
    if exc:
        calc = por_plantas(exc, "Excepción por partida")
        calc.avisos.append(("excepcion", f"Tasa propia de la partida: {exc:g} plantas por bandeja"))
        return calc

    # 2. Jiffy (solo injerto: la categoría Jiffy solo existe en injerto).
    if categoria == "Jiffy":
        if R.JIFFY_DESARROLLADA.search(obs):
            calc = por_plantas(R.TASA_DESARROLLADA, "Jiffy desarrollada", desarrollada=True)
            calc.avisos.append(("desarrollada", "Jiffy con tallos/brazos: 15 plantas por bandeja"))
            return calc
        if not medida:
            return CalculoBandejas(
                redondear(band_val), None, "Band (Jiffy sin medida)", "", False,
                [("sin_medida", f"Jiffy sin marca de medida ni tipo conocido ({tipo or 'sin tipo'}); se usa Band")],
            )
        if medida == "5x5" and tipo in R.TASA_JIFFY_TIPO:
            return por_plantas(R.TASA_JIFFY_TIPO[tipo], f"Jiffy 5x5 {tipo}")
        return por_plantas(R.TASA_JIFFY[medida], f"Jiffy {medida}")

    # 3. Hidropónico.
    if categoria == "Hidro":
        if R.HIDRO_ADELANTADA.search(obs):
            calc = por_plantas(R.TASA_DESARROLLADA, "Hidro adelantada", desarrollada=True)
            calc.avisos.append(("desarrollada", "Hidropónico adelantado: 15 plantas por bandeja"))
            return calc
        if not medida:
            return CalculoBandejas(
                redondear(band_val), None, "Band (hidro sin medida)", "", False,
                [("sin_medida", f"Hidropónico sin medida conocida ({tipo or 'sin tipo'}); se usa Band")],
            )
        return por_plantas(R.TASA_HIDRO[medida], f"Hidro {medida}")

    # 4. Tacos, pepino y calabacín como bandejas de extensión abiertas.
    if tipo_planta in R.EXT_ABIERTOS_TIPOS_PLANTA and tipo in R.EXT_ABIERTOS:
        return por_plantas(R.EXT_ABIERTOS[tipo], f"Extensión abierta {tipo}")

    # 5. Resto: columna Band.
    return CalculoBandejas(redondear(band_val), None, "Band")
