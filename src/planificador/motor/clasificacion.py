"""Tipo de planta, categoría, cultivo, ECO/BIO, estilo y etapa (documento maestro §6)."""

from __future__ import annotations

import re
import unicodedata

from .reglas import CULTIVO_POR_PALABRA, ECO_CODIGO, PALABRAS_TOMATE


def sin_tildes(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in texto if not unicodedata.combining(c))


def mayus(texto: str | None) -> str:
    return sin_tildes(str(texto or "")).upper().strip()


def tipo_planta_de_codigo(codigo: str) -> str:
    """Tipo por el código: X/YE/F injerto; P/C/VE/WE componente; resto (dígitos, EC) normal."""
    resto = mayus(codigo)[2:]
    if resto.startswith(("X", "YE", "F")):
        return "injerto"
    if resto.startswith(("P", "C", "V", "W")):
        return "componente"
    return "normal"


def es_ecologica(codigo: str) -> bool:
    return bool(ECO_CODIGO.match(mayus(codigo)))


def bio_valor(codigo: str, tipo_planta: str, lucha_biologica: bool) -> str:
    """ECO (código VE/EC/WE/YE) y BIO (lucha biológica) son cosas distintas (§6.4)."""
    eco = es_ecologica(codigo)
    if tipo_planta == "componente":
        return "ECO" if eco else ""
    if eco:
        return "ECO BIO" if lucha_biologica else "ECO"
    return "BIO" if lucha_biologica else "No BIO"


def es_lucha_biologica(valor) -> bool:
    return mayus(valor) in {"S", "SI"}


def categoria_injerto(variedad: str) -> str:
    v = mayus(variedad)
    if "JIF" in v:
        return "Jiffy"
    if "HIDRO" in v:
        return "Hidro"
    if "COT" in v:
        return "Cotiledon"
    if re.search(r"\dHV", v):
        return "Pinzado"
    return "Normal"


def categoria_componente(codigo: str) -> str:
    """Tercer carácter del código: P y W → Patrón; C y V → Variedad."""
    letra = mayus(codigo)[2:3]
    return "Patrón" if letra in {"P", "W"} else "Variedad"


def categoria(codigo: str, tipo_planta: str, variedad: str) -> str:
    if tipo_planta == "injerto":
        return categoria_injerto(variedad)
    if tipo_planta == "componente":
        return categoria_componente(codigo)
    return "Normal"


def cultivo(variedad: str, categoria_: str, medida: str = "") -> str:
    palabras = mayus(variedad).split()
    primera = palabras[0] if palabras else ""
    if primera in CULTIVO_POR_PALABRA:
        return CULTIVO_POR_PALABRA[primera]
    if primera in PALABRAS_TOMATE:
        if categoria_ == "Jiffy":
            return f"Jiffy {medida}" if medida in {"8x8", "5x5"} else "Jiffy"
        if categoria_ == "Hidro":
            return "Hidropónico"
        return "Tomate"
    return "Otros"


def estilo(observaciones: str) -> str:
    o = mayus(observaciones)
    if "ALMERIA" in o or re.search(r"\bCASI\b", o):
        return "Almería"
    return "Normal"


def etapa(variedad: str) -> str:
    v = mayus(variedad)
    m = re.search(r"([234])HV", v)
    if m:
        return f"{m.group(1)}HV"
    if "COT" in v:
        return "COT"
    return "Normal"
