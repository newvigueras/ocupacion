"""Carga del colocado real por calle desde EXTENSIONCALLES.xlsx (documento maestro §10.5b)."""

from datetime import date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_extensiones import CAB_MOV, libro, mov
from planificador.bd import (
    Asignacion, DatoReal, Planificador, crear_motor, guardar_partidas, preparar_bd,
)
from planificador.bd.calles import InvSec, Trozo, cargar_por_calles, leer_inv_sec, repartir_en_calles
from planificador.cli import main
from planificador.motor import leer_excel

CAB_CALLES = ["Partida", "Nombre Cliente", "F.Siembra", "Semilla", "Nº Lote", "T.Ban", "Pl.sol",
              "NºPla", "Nº Band", "Sem?", "Inv-Sec"]
HOY = date(2026, 11, 21)


def fc(codigo, inv_sec, bandejas, *, cliente="CLI", semilla="TOMATE X", siembra=None):
    return [codigo, cliente, siembra, semilla, 1, "B216", None, 1000, bandejas, None, inv_sec]


FOTO = [
    fc("26X02589", "41-3", 100),                                   # en el maestro
    fc("26X07777", datetime(2026, 6, 22), 30, cliente="CLI X", semilla="TOMATE RAF",
       siembra=datetime(2026, 11, 10)),                             # «22-6» hecho fecha
    fc("26X02789", "61-13", 50),                                    # invernadero que no está
    fc("26X03378", "41-99", 20),                                    # calle que no existe
    fc("26X03902", None, 40),                                       # sin ubicación
    fc(26010905, "1-5", 80),                                        # INV1A calle 105
    fc("26X02476", "11-25", 60),                                    # INV11B calle 1125
    fc("26X03025", "41-4", 0),                                      # sin bandejas: omitida
]


@pytest.fixture
def s(tmp_path, excel_ejemplo):
    motor = crear_motor(f"sqlite:///{tmp_path / 'calles.db'}")
    preparar_bd(motor)
    with Session(motor) as sesion:
        guardar_partidas(sesion, leer_excel(excel_ejemplo))
        sesion.commit()
        yield sesion


@pytest.fixture
def foto(tmp_path):
    return libro(tmp_path / "EXTENSIONCALLES.xlsx", CAB_CALLES, FOTO)


def reales(s, codigo):
    return {(a.invernadero.nombre, a.calle.numero if a.calle else None, a.bandejas)
            for a in s.scalars(select(Asignacion).where(Asignacion.base == "real",
                                                        Asignacion.codigo == codigo))}


def test_leer_inv_sec():
    assert leer_inv_sec("44-17", 2026) == InvSec(44, 17, "texto")
    assert leer_inv_sec(" 61 - 13 ", 2026) == InvSec(61, 13, "texto")
    assert leer_inv_sec(datetime(2026, 6, 22), 2026) == InvSec(22, 6, "fecha")
    assert leer_inv_sec(datetime(2016, 4, 1), 2026) == InvSec(4, 16, "fecha")
    assert leer_inv_sec(datetime(2026, 3, 1), 2026) == InvSec(1, 3, "ambigua")
    assert leer_inv_sec(46195, 2026) == InvSec(22, 6, "fecha")  # número de serie de Excel
    assert leer_inv_sec(None, 2026) is None and leer_inv_sec("calle", 2026) is None


def test_reparto_entre_calles_contiguas():
    calles = {"INVX": [(1, 10), (2, 10), (3, 10), (4, 10)], "INVY": [(1, 10), (2, 10)]}
    grande, pequeña, lleno = Trozo("INVX", 2, 25), Trozo("INVX", 2, 5), Trozo("INVY", 1, 100)
    r = repartir_en_calles([grande, pequeña, lleno], calles)
    assert pequeña.partes == {2: 5}                  # las pequeñas caben primero
    assert grande.partes == {1: 10, 2: 5, 3: 10}     # delante y detrás por igual
    assert lleno.partes == {1: 90, 2: 10}            # invernadero lleno: se queda en su calle
    assert (r.repartidas, r.movidas, r.sin_hueco) == (2, 30, 80)


def test_cargar_por_calles(s, foto):
    s.add(DatoReal(codigo="26X02589", bandejas_real=361, fuente="MANUAL"))
    s.add(DatoReal(codigo="26X03373", bandejas_real=87, fuente="MANUAL",
                   fec_entrada_real=date(2026, 11, 2)))
    s.flush()
    plan = Planificador(s)
    plan.asignar_partida("real", "26X02589", "INV42", 361)
    plan.asignar_partida("real", "26X03373", "INV42", 87)

    r = cargar_por_calles(s, [foto], hoy=HOY)
    assert (r.filas, r.omitidas, r.año_ref, r.desde_fecha) == (7, 1, 2026, 1)
    assert (r.en_calle.partidas, r.en_calle.bandejas) == (4, 270)
    assert r.fuera_por_invernadero == {"INV 61": 50}
    assert r.calles_desconocidas == ["41-99"] and r.sin_ubicacion.bandejas == 40
    assert r.sustituidas_otras == 1 and r.asignaciones == 4

    # Sustituye lo que la partida tenía en Real; lo demás no se toca.
    assert reales(s, "26X02589") == {("INV41", 3, 100)}
    assert reales(s, "26X03373") == {("INV42", None, 87)}
    # Numeración de calles con el invernadero delante.
    assert reales(s, "26010905") == {("INV1A", 105, 80)}
    assert reales(s, "26X02476") == {("INV11B", 1125, 60)}

    # Fechas: del maestro, nunca después de hoy; o por la siembra (X = + 6).
    d = plan.dato_real("26X02589")
    assert (d.fuente, d.fec_entrada_real, d.bandejas_real) == ("EXTENSIONCALLES", HOY, 100)
    assert plan.fechas_reales(d)[1] == date(2027, 1, 1)
    x = plan.dato_real("26X07777")
    assert x.fec_entrada_real == date(2026, 11, 16) and x.liberacion_estimada
    assert (x.cliente, x.variedad, x.localizacion) == ("CLI X", "TOMATE RAF", "INV22 calle 6")
    assert r.entrada == {"maestro": 3, "siembra": 1}


def test_salida_pasada_se_deja_hoy(s, foto):
    plan = Planificador(s)
    plan.poner_fecha_tecnico("26X02476", date(2026, 12, 1))
    hoy = date(2027, 1, 10)  # 26X02589 debía salir el 01/01/2027
    r = cargar_por_calles(s, [foto], hoy=hoy)
    assert plan.fechas_reales(plan.dato_real("26X02589"))[1] == hoy
    # La fecha técnico manda aunque ya haya pasado.
    assert plan.fechas_reales(plan.dato_real("26X02476"))[1] == date(2026, 12, 1)
    assert r.retrasadas.partidas == 3  # 26X02589, 26010905 y 26X07777


def test_con_movimientos_y_dos_veces(s, foto, tmp_path):
    movs = libro(tmp_path / "EXTENSIONES.xlsx", CAB_MOV, [mov("26X07777", 12, "INVERNADERO 22", 30)])
    r1 = cargar_por_calles(s, [foto, movs], hoy=HOY)
    assert Planificador(s).dato_real("26X07777").fec_entrada_real == date(2026, 11, 12)
    assert r1.entrada["movimientos"] == 1
    r2 = cargar_por_calles(s, [foto, movs], hoy=HOY)
    assert r2.sustituidas_calles == r1.asignaciones
    assert (r2.asignaciones, r2.bandejas_cargadas) == (r1.asignaciones, r1.bandejas_cargadas)
    assert reales(s, "26X07777") == {("INV22", 6, 30)}


def test_cli_por_calles(tmp_path, foto, capsys):
    bd = ["--bd", f"sqlite:///{tmp_path / 'cli.db'}"]
    assert main(bd + ["cargar-extensiones", str(foto), "--hoy", "2026-11-21"]) == 0
    salida = capsys.readouterr().out
    assert "En calles del planificador: 4 partidas" in salida and "INV 61" in salida
