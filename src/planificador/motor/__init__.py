"""Motor de cálculo: lectura de Excel, partidas, fechas, ECO/BIO y bandejas."""

from .calendario import Calendario, PartidaEnSemana, Semana, inicio_semana1
from .excel import ErrorExcel, analizar_filas, fusionar, leer_excel
from .modelos import Aviso, Partida, ResultadoImportacion
from .reglas import Reglas

__all__ = [
    "Aviso", "Calendario", "ErrorExcel", "Partida", "PartidaEnSemana", "Reglas",
    "ResultadoImportacion", "Semana", "analizar_filas", "fusionar", "inicio_semana1",
    "leer_excel",
]
