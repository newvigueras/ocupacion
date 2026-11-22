"""Carga del colocado real desde EXTENSIONES.xlsx (documento maestro §10.5)."""

from datetime import date, datetime, time

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import CABECERA, fila
from planificador.bd import (
    Asignacion, DatoReal, Planificador, crear_motor, guardar_partidas, preparar_bd,
)
from planificador.bd.extensiones import (
    SIN_DATOS_CLIENTE, cargar_extensiones, etiqueta_invernadero, familia, posiciones_netas,
    leer_movimientos,
)
from planificador.cli import main
from planificador.motor import leer_excel

CAB_MOV = ["Nº Parte", "Nº Partida", "Fecha", "Hora", "C/Us", "Usuario", "Ori",
           "Invernadero Origen", "Des", "Invernadero Destino", "Tipo", "NºBand"]


def mov(codigo, dia, destino, bandejas, *, tipo="P", origen=None, hora=time(10, 0)):
    return [1, codigo, datetime(2026, 11, dia), hora, 63, "X", None, origen, None, destino,
            tipo, bandejas]


MOVIMIENTOS = [
    mov("26X02589", 28, "INVERNADERO 41", 100),             # en el maestro
    mov("H26/1187", 20, "INVERNADERO 44", 30),              # solo en la previsión
    mov("26X07777", 21, "INVERNADERO 45", 12),              # sin datos: estimada
    mov("B27/8056", 13, "INVERNADERO 36", 40),              # entrega dudosa
    mov("26X02789", 22, "INVERNADERO 33", 200),
    mov("26X02789", 23, "INVERNADERO 34", 50, tipo="C", origen="INVERANDERO 33"),
    mov("26X02476", 22, "INVERNADERO 7 SURINVER", 70),      # fuera del planificador
    mov(26010905, 24, "INVERNADERO 1", 300),                # INV1A o INV1B
    mov(26010978, 25, "INVERNADERO 1", 200),
    mov("26X03378", 24, "INVERNADERO 42", 25),              # Real hecho a mano: no se toca
]

PREVISION = [
    fila("H26/1187", "PIMIENTO RIO GRANDE", band=133, entrega=date(2026, 12, 20),
         cliente="CLIENTE H", lucha="S"),
    fila("B27/8056", "PIMIENTO X", band=40, entrega=date(2027, 12, 25)),
]


def libro(ruta, cabecera, filas):
    wb = Workbook()
    ws = wb.active
    ws.title = "SALIDA DE TOMATE CON FECHA PREV"
    ws.append(cabecera)
    for f in filas:
        ws.append(f)
    wb.save(ruta)
    return ruta


@pytest.fixture
def archivos(tmp_path):
    return [libro(tmp_path / "EXTENSIONES.xlsx", CAB_MOV, MOVIMIENTOS),
            libro(tmp_path / "0110.xlsx", CABECERA, PREVISION)]


@pytest.fixture
def s(tmp_path, excel_ejemplo):
    motor = crear_motor(f"sqlite:///{tmp_path / 'ext.db'}")
    preparar_bd(motor)
    with Session(motor) as sesion:
        guardar_partidas(sesion, leer_excel(excel_ejemplo))
        sesion.add(DatoReal(codigo="26X03378", bandejas_real=99, fuente="MANUAL"))
        sesion.commit()
        yield sesion


def reales(s, codigo):
    return {(a.invernadero.nombre, a.bandejas) for a in s.scalars(
        select(Asignacion).where(Asignacion.base == "real", Asignacion.codigo == codigo))}


def test_etiquetas_y_familias():
    assert etiqueta_invernadero("INVERNADERO 16") == "INV16"
    assert etiqueta_invernadero("INVERANDERO 33") == "INV33"
    assert etiqueta_invernadero("INVERNADERO 7 SURINVER") == "INV7 SURINVER"
    assert etiqueta_invernadero("TUNELES") == "TUNELES"
    assert etiqueta_invernadero(None) == ""
    assert [familia(c) for c in ("26010905", "26X1", "26EC1", "26YE1", "B27/8056", "H26/1", "23A1")] \
        == ["numerica", "X", "EC", "YE", "B", "H", "resto"]


def test_posicion_neta(tmp_path):
    filas = [CAB_MOV] + [tuple(m) for m in MOVIMIENTOS]
    netas = posiciones_netas(leer_movimientos(0, filas))
    assert netas["26X02789"] == {"INV33": 150, "INV34": 50}
    assert len(netas) == 9  # 10 movimientos, 9 partidas


def test_cargar_extensiones(s, archivos):
    r = cargar_extensiones(s, archivos)
    assert (r.movimientos, r.partidas) == (10, 9)
    assert r.bandejas_netas == 977
    assert r.conservadas == ["26X03378"]
    assert r.no_cargadas == {"INV7 SURINVER": 70}
    assert (r.salida_maestro, r.salida_prevision, r.salida_estimada) == (5, 1, 2)
    assert r.dudosas == ["B27/8056"]
    plan = Planificador(s)

    # Del maestro: liberación de la partida (F Entrega + 3).
    assert reales(s, "26X02589") == {("INV41", 100)}
    entrada, liberacion = plan.fechas_reales(plan.dato_real("26X02589"))
    assert (entrada, liberacion) == (date(2026, 11, 28), date(2027, 1, 1))

    # Solo en la previsión: entrega + 3, cliente y BIO de la previsión.
    h = plan.dato_real("H26/1187")
    assert h.fecha_liberacion_ext == date(2026, 12, 23)
    assert (h.cultivo, h.cliente, h.bio) == ("Pimiento", "CLIENTE H", "BIO")

    # Sin datos: estimada (X = 14 días + 3) y cliente «sin datos».
    x = plan.dato_real("26X07777")
    assert x.liberacion_estimada and x.fecha_liberacion_ext == date(2026, 12, 8)
    assert x.cliente == SIN_DATOS_CLIENTE

    # Fecha dudosa (más de 150 días): se estima (B = 43 + 3).
    assert plan.dato_real("B27/8056").fecha_liberacion_ext == date(2026, 12, 29)

    # «C» resta del origen y suma al destino; el error «INVERANDERO» no importa.
    assert reales(s, "26X02789") == {("INV33", 150), ("INV34", 50)}

    # INVERNADERO 1: la primera va a la mitad vacía (A) y la segunda a la menos ocupada (B).
    assert reales(s, "26010905") == {("INV1A", 300)}
    assert reales(s, "26010978") == {("INV1B", 200)}

    # SURINVER no se carga pero queda el dato real.
    assert reales(s, "26X02476") == set()
    assert plan.dato_real("26X02476").localizacion == "INV7 SURINVER"

    # Lo hecho a mano no se toca.
    assert plan.dato_real("26X03378").bandejas_real == 99
    assert r.bandejas_cargadas == 977 - 70 - 25


def test_cargar_dos_veces_da_lo_mismo(s, archivos):
    r1 = cargar_extensiones(s, archivos)
    total = lambda: sum(a.bandejas for a in s.scalars(
        select(Asignacion).where(Asignacion.base == "real")))
    antes = total()
    r2 = cargar_extensiones(s, archivos)
    assert total() == antes == r1.bandejas_cargadas == r2.bandejas_cargadas
    assert r2.asignaciones == r1.asignaciones


def test_calle_puesta_a_mano_se_respeta(s, archivos):
    cargar_extensiones(s, archivos)
    plan = Planificador(s)
    plan.asignar_partida("real", "26X02589", "INV41", 0)
    plan.asignar_partida("real", "26X02589", "INV41", 100, calle=7)
    r = cargar_extensiones(s, archivos)
    assert "26X02589" in r.conservadas
    assert reales(s, "26X02589") == {("INV41", 100)}


def test_falta_el_de_movimientos(s, archivos):
    with pytest.raises(ValueError, match="movimientos"):
        cargar_extensiones(s, archivos[1:])


def test_cli_cargar_extensiones(tmp_path, archivos, capsys):
    bd = ["--bd", f"sqlite:///{tmp_path / 'cli.db'}"]
    assert main(bd + ["cargar-extensiones", *map(str, archivos)]) == 0
    salida = capsys.readouterr().out
    assert "10 movimientos" in salida and "INV7 SURINVER" in salida
