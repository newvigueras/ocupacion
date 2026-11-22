"""Excel de ejemplo con el formato real («SALIDA DE TOMATE CON FECHA PREV»)."""

from datetime import date

import pytest
from openpyxl import Workbook

CABECERA = [
    "PARTIDA", "Nombre Cliente", "VARIEDAD", "F Siembra", "Fecha Real Siembra",
    "F Entrega", "Band", "Pl sol", "Band Act", "OBSERVA", "Tipo de Siembra",
    "Partida biológica?", "Soporte de Siembra",
]

S = date(2026, 11, 24)  # siembra por defecto
E = date(2026, 12, 29)  # entrega por defecto


def fila(codigo, variedad, *, tipo="", pl=None, band=100, obs="", lucha="N",
         siembra=S, real=None, entrega=E, cliente="CLIENTE PRUEBA", soporte=216):
    return [codigo, cliente, variedad, siembra, real, entrega, band, pl, 999, obs,
            tipo, lucha, soporte]


# Casos del documento maestro §17.1 y de las decisiones del 22/11/2026.
FILAS = [
    fila("26X02589", "TINJ BRONSKI 141 2BR 2HV JIF", tipo="TIJ2", pl=30420,
         band=2535, obs="TALLOS 15 CM"),
    fila("26X03378", "TINJ XXX JIF", tipo="TIJ2", pl=10000, obs="BRAZOS 20CM"),
    fila("26X03025", "TINJ ENSAYO JIF", tipo="TIJ2", pl=360, obs="LO QUIERE 29.12"),
    fila("26X02999", "TINJ ENSAYO JIF", tipo="TIJ3", pl=981, obs="LO QUIERE 29.12"),
    fila("26X02789", "TINJ RAF JIF", tipo="TIJ6", pl=67000),
    fila("26X02476", "TINJ X HIDRO", tipo="TIH2", pl=16500,
         obs="EST+AVANZADO POSIBLE FLOR ABIERTA"),
    fila("26X02560", "TINJ X HIDRO", tipo="TIH2", pl=14500, obs="MUY ADELATADA"),
    fila("26X03125", "TINJ X HIDRO", tipo="TIH2", pl=2070, obs="INDUCIDA A FLOR"),
    fila("26X02927", "TINJ X HIDRO", tipo="TIH1", pl=40000, obs="LANA ROCA 7,5*7,5"),
    fila(26010905, "TOMATE TACO", tipo="TH02", pl=12400),
    fila(26010978, "PEPINO TACO", tipo="PEH2", pl=24624),
    fila("26X02968", "PEINJ MINIPEPINO", tipo="MIH2", pl=13320),
    fila("26X02997", "PEINJ PEPINO", tipo="PI35", pl=2200),
    fila(26011200, "CALABACIN X", tipo="CCH5", pl=8400, band=120),
    fila("26X03900", "TINJ X HIDRO 120", tipo="TIHI", pl=12000, band=77),
    fila("26X03901", "TINJ X JIF", tipo="ZZZ", pl=1800, band=55),
    fila("26X03902", "PIINJ X", band=40, lucha="S"),
    fila("26YE00001", "TINJ ECO", band=30),
    fila("26EC00001", "TOMATE ECO", band=30),
    fila("26VE00001", "TOMATE VARIEDAD ECO", band=20, lucha="S"),
    fila("26WE00001", "CALABAZA PATRON ECO", band=20),
    fila("26P00001", "TOMATE PATRON", band=20, lucha="S"),
    fila("26C00001", "TOMATE VARIEDAD", band=20),
    fila("26X03903", "TINJ X 3HV", band=50, obs="CASI 2 BRAZOS"),
    fila("26X03904", "SINJ X COT", band=60, obs="ALMERÍA"),
    fila("26X03373", "TINJ PLANTA X", tipo="TI12", pl=8950, band=87,
         obs="PLANTA DESARROLLADA"),
    fila("26X03905", "MINJ X", real=date(2026, 11, 20), band=10),
    # Códigos desconocidos: se ignoran (22/11/2026)
    fila("H26/1187", "PIMIENTO RIO GRANDE", band=133),
    fila("B26/8918", "PIMIENTO BEMOL", band=33),
    fila("23A00010", "PEINJ PRUEBA", band=3230),
    fila("26KC00001", "TOMATE X", band=20),
    fila(25010001, "PIMIENTO X", band=10),
    fila("26F00001", "TINJ X", band=10),
    # Omitidas
    fila("26X03561", "TINJ X", band=0),
    fila("26X03562", "TINJ X", entrega=date(2026, 11, 1)),
    fila("26X03563", "TINJ X", siembra=None),
    # Repetida: gana la última
    fila("26X04000", "TINJ X", band=10),
    fila("26X04000", "TINJ X", band=11),
]


@pytest.fixture
def excel_ejemplo(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "SALIDA DE TOMATE CON FECHA PREV"
    ws.append(["VIVEROS LA SALA - previsión"])
    ws.append([])
    ws.append(CABECERA)
    for f in FILAS:
        ws.append(f)
    ruta = tmp_path / "prevision.xlsx"
    wb.save(ruta)
    return ruta
