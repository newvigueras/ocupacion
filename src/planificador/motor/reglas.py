"""Reglas de negocio confirmadas por Viveros La Sala (documento maestro §5–§7 y §16).

Todo lo que el usuario puede querer cambiar sin tocar código (hoy: las excepciones
por partida) vive en la clase ``Reglas``; el resto son constantes confirmadas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- Fechas (§5) -----------------------------------------------------------
#: Días desde la siembra hasta la entrada al invernadero, por tipo de planta.
DIAS_ENTRADA = {"injerto": 6, "normal": 2, "componente": 2}
#: La partida se libera de golpe F Entrega + 3 días (ocupa hasta ese día incluido).
DIAS_LIBERACION = 3

# --- Jiffy (§7.1) ----------------------------------------------------------
TASA_JIFFY = {"8x8": 18, "5x5": 27}
#: Tipos que, en 5x5, tienen su propia tasa (TIJ6 no se abre: bandeja completa).
TASA_JIFFY_TIPO = {"TIJ6": 54}
JIFFY_POR_TIPO = {
    "TIJ0": "8x8", "TIJ2": "8x8", "TIJ3": "8x8", "TIJ4": "8x8",
    "TIJ6": "5x5", "TIJ7": "5x5", "TIJ8": "5x5", "TIJ9": "5x5",
}
#: «tallos 15 cm», «tallos 10-15 cm», «brazos 20 cm»… (no «brotes 1-2 cms»).
JIFFY_DESARROLLADA = re.compile(r"(?:tallos?|brazos?)\s*\d+(?:\s*-\s*\d+)?\s*cm", re.I)
JIFFY_MARCA = re.compile(r"(?<!\d)([58])\s*[*xX×]\s*\1(?!\d)")

# --- Hidropónico (§7.2) ----------------------------------------------------
TASA_HIDRO = {"7.5x7.5": 48, "10x10": 12}
HIDRO_POR_TIPO = {
    "TIH1": "7.5x7.5", "TIH2": "7.5x7.5", "TIH3": "7.5x7.5", "TIHY": "7.5x7.5",
    "TIHA": "10x10", "TIHB": "10x10", "TIHD": "10x10",
    # TIHI: medida sin confirmar → se usa la columna Band (decisión 22/11/2026).
}
HIDRO_ADELANTADA = re.compile(
    r"MUY\s+ADELA\w*|AVANZAD\w*|FLOR\s+ABIERT\w*|INDUCID\w*\s+A\s+FLOR", re.I
)
HIDRO_MARCA_75 = re.compile(r"7\s*[,.]\s*5\s*[*xX×]\s*7\s*[,.]\s*5")
HIDRO_MARCA_10 = re.compile(r"(?<!\d)10\s*[*xX×]\s*10(?!\d)")

#: Plantas por bandeja de toda planta desarrollada (Jiffy con tallos/brazos, hidro adelantada).
TASA_DESARROLLADA = 15

# --- Bandejas de extensión abiertas (§7.4) ---------------------------------
EXT_ABIERTOS = {
    "TH02": 40,  # tomate taco
    "PEH2": 40,  # pepino taco
    "MIH2": 40,  # minipepino taco
    "PI35": 18,  # pepino Jiffy 8x8
    "CCH5": 84,  # calabacín (decisión 22/11/2026: abierta a 84, como el PDF de ocupación)
}
EXT_ABIERTOS_TIPOS_PLANTA = frozenset({"injerto", "normal"})

#: Excepciones por partida (código → plantas por bandeja) con las que se arranca.
EXCEPCIONES_PARTIDA_INICIALES = {
    "26X03025": 15,  # ensayo DR0141 «lo quiere 29.12» (21/11/2026)
    "26X02999": 15,  # ensayo DR0141 «lo quiere 29.12» (21/11/2026)
}

# --- Clasificación (§6) ----------------------------------------------------
#: Códigos de partida que se importan (decisión 22/11/2026): año de dos cifras y
#: X, YE, F (injerto); nada o EC (planta normal); P, C, WE, VE (componentes).
#: Los demás (H26/…, B26/…, 23A…, 26KC…, 26ZE…) se ignoran de momento y se avisa.
CODIGO_CONOCIDO = re.compile(r"^[0-9]{2}(?:X|YE|F|EC|P|C|WE|VE)?[0-9]+$", re.I)

ECO_CODIGO = re.compile(r"^[0-9]{2}(VE|EC|WE|YE)", re.I)

CULTIVO_POR_PALABRA = {
    "PEINJ": "Pepino", "PEPINO": "Pepino",
    "PIINJ": "Pimiento", "PIMIENTO": "Pimiento",
    "SINJ": "Sandía", "SANDIA": "Sandía",
    "MINJ": "Melón", "MELON": "Melón",
    "BINJ": "Berenjena", "BERENJENA": "Berenjena",
    "CALABAZA": "Calabaza",
}
PALABRAS_TOMATE = frozenset({"TINJ", "TOMATE", ""})

#: Orden en que se muestran los cultivos.
CULTIVOS = [
    "Jiffy 8x8", "Jiffy 5x5", "Jiffy", "Hidropónico", "Tomate", "Pimiento",
    "Pepino", "Sandía", "Melón", "Berenjena", "Calabaza", "Otros",
]

TIPOS_PLANTA = ("injerto", "normal", "componente")


@dataclass
class Reglas:
    """Reglas que el usuario puede cambiar desde la aplicación."""

    #: Código de partida → plantas por bandeja. Manda sobre cualquier otra regla.
    excepciones_partida: dict[str, float] = field(
        default_factory=lambda: dict(EXCEPCIONES_PARTIDA_INICIALES)
    )

    @classmethod
    def por_defecto(cls) -> "Reglas":
        return cls()

    def excepcion(self, codigo: str) -> float | None:
        """Tasa propia de la partida; el código se compara sin mayúsculas ni espacios."""
        clave = codigo.strip().upper()
        tasa = self.excepciones_partida.get(clave)
        if tasa is None:
            tasa = next(
                (v for k, v in self.excepciones_partida.items() if k.strip().upper() == clave),
                None,
            )
        return tasa if tasa and tasa > 0 else None
