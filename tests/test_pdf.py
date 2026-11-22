"""PDF de túneles y de cargas (documento maestro §12.1–12.2), a partir del texto."""

from datetime import date

import pytest
from sqlalchemy.orm import Session

from planificador.bd import DatoReal, Planificador, crear_motor, guardar_partidas, preparar_bd
from planificador.bd.pdf import (
    Palabra, cargar_texto, formato_de_grupo, leer_linea_cargas, leer_parte_cargas,
    leer_parte_injerto, texto_de_palabras, tipo_de_texto,
)
from planificador.cli import main
from planificador.motor import leer_excel

SEP = "_________"
TUNELES = "\n".join([
    "Empresa: Grupo Semilleros                                   Página 1",
    "NºParte   Fecha Entrega   Nº Partida   Band.Sol   Cliente   Nº Plantas   Nº Band.   "
    "Localización   Fec Entrada   Obs Inj   FecRealSal",
    "TI15 - TOMATE INJ 150",
    "007 - BI",
    "1234567   29/12/2026   26X02589   228   AGRICOLA X SL   30420   210   41-3   24/11/2026   "
    "CASI 2 BRAZOS   30/11/2026",
    "TINJ BRONSKI",
    SEP,
    "1234568   29/12/2026   26X02589   228RUSTICA LA OLIVA SL   1000   10.5   41-4   24/11/2026   "
    "30/11/2026",
    SEP,
    "34.065  313.0",
    "TJ02 - TOMATE JIFFY 8x8",
    "No definido",
    "1234569   05/01/2027   26X09999   50   CLIENTE Z   5000   48   44-2   01/12/2026   OBS LIBRE",
    "sigue la observación",
    "1234570   05/01/2027   26X03902   40   CLIENTE Z   4000   39   44-3   01/12/2026   02/12/2026",
])

CARGAS = "\n".join([
    "Grupo Semilleros La Sala          PARTE DE CARGA",
    "DIA: 15/12/2026          Página 1",
    "HORA   CLIENTE   PARTIDA   VARIEDAD   Nº BAND   INVERNA-CARRO",
    "08:30   AGRICOLA X SL 26X02589   BRONSKI   100 41-3-2",
    "PTE   CLIENTE LARGO26010905   TACO   50 1-105",
    "AGRICOLA Y   26X02589   BRONSKI   20 41-3",
    "CAMION 1",
    "***",
])


@pytest.fixture
def s(tmp_path, excel_ejemplo):
    motor = crear_motor(f"sqlite:///{tmp_path / 'pdf.db'}")
    preparar_bd(motor)
    with Session(motor) as sesion:
        guardar_partidas(sesion, leer_excel(excel_ejemplo))
        sesion.commit()
        yield sesion


def test_texto_por_posicion():
    palabras = [Palabra(60, 110, 100.6, "29/12/2026"), Palabra(10, 45, 100, "1234567"),
                Palabra(114, 119, 100, "y"), Palabra(10, 30, 120, "TI15"), Palabra(5, 6, 130, " ")]
    assert texto_de_palabras(palabras) == "1234567   29/12/2026 y\nTI15"


def test_leer_parte_injerto():
    rs = leer_parte_injerto(TUNELES)
    assert [r.partida for r in rs] == ["26X02589", "26X02589", "26X09999", "26X03902"]
    a, b, c, d = rs
    assert (a.band_sol, a.cliente, a.bandejas, a.localizacion) == (228, "AGRICOLA X SL", 210, "41-3")
    assert (a.fec_real_sal, a.observaciones) == ("30/11/2026", "CASI 2 BRAZOS TINJ BRONSKI")
    assert (a.categoria_nombre, a.bio) == ("TOMATE INJ 150", "BI")
    assert (b.band_sol, b.cliente, b.bandejas) == (228, "RUSTICA LA OLIVA SL", 10.5)
    assert (c.bio, c.fec_real_sal, c.observaciones) == ("No definido", "", "OBS LIBRE sigue la observación")
    assert d.fec_real_sal == "02/12/2026"
    assert formato_de_grupo("TOMATE INJ 150") == "150"
    assert formato_de_grupo("TOMATE 104 REPICADO") == "104"
    assert formato_de_grupo("TOMATE JIFFY 8x8") == ""


def test_leer_parte_cargas():
    rs = leer_parte_cargas(CARGAS)
    assert [(r.partida, r.cliente, r.bandejas, r.dia) for r in rs] == [
        ("26X02589", "AGRICOLA X SL", 100, "15/12/2026"),
        ("26010905", "CLIENTE LARGO", 50, "15/12/2026"),
        ("26X02589", "AGRICOLA Y", 20, "15/12/2026"),
    ]
    assert leer_linea_cargas("26X-PRUEBA") is None
    assert (tipo_de_texto(CARGAS), tipo_de_texto(TUNELES)) == ("cargas", "tuneles")


def test_tuneles_y_cargas_en_real(s):
    # 26X02589 ya estaba colocada (EXTENSIONES) con otra fecha de entrada.
    s.add(DatoReal(codigo="26X02589", bandejas_real=300, fec_entrada_real=date(2026, 11, 28),
                   fuente="EXTENSIONES"))
    s.flush()
    plan = Planificador(s)
    plan.asignar_partida("real", "26X02589", "INV41", 200)

    r = cargar_texto(s, TUNELES, "parte.pdf")
    assert r.tipo == "tuneles" and r.partidas == ["26X02589", "26X09999", "26X03902"]
    assert r.fuera_del_maestro == ["26X09999"] and r.asignaciones_actualizadas == 1
    d = plan.dato_real("26X02589")
    assert (d.bandejas_real, d.fec_entrada_real, d.fecha_entrega_pdf) == (
        221, date(2026, 11, 30), date(2026, 12, 29))
    assert (d.localizacion, d.estilo, d.categoria, d.formato, d.bio) == (
        "41-3, 41-4", "Almería", "Normal", "150", "BIO")
    assert d.fuente == "EXTENSIONES"  # la fuente no cambia
    nueva = plan.dato_real("26X09999")
    assert (nueva.fuente, nueva.categoria, nueva.cultivo, nueva.formato) == (
        "PDF_TUNELES", "Jiffy", "Jiffy", "")
    assert nueva.fec_entrada_real is None
    # La asignación toma la entrada nueva (y la entrega del PDF como liberación).
    assert plan.ocupacion_dia("real", date(2026, 11, 29))["INV41"] == 0
    assert plan.ocupacion_dia("real", date(2026, 11, 30))["INV41"] == 200

    r = cargar_texto(s, CARGAS, "cargas.pdf")
    assert r.tipo == "cargas" and r.bandejas == 170 and r.fechas == [date(2026, 12, 15)]
    assert r.asignaciones_actualizadas == 1
    assert plan.ocupacion_dia("real", date(2026, 12, 15))["INV41"] == 200
    assert plan.ocupacion_dia("real", date(2026, 12, 16))["INV41"] == 0
    salida = plan.dato_real("26010905")
    assert (salida.fuente, salida.fecha_salida_real, salida.bandejas_real) == (
        "PDF_CARGAS", date(2026, 12, 15), 0)


def test_cli_pdf_sin_partidas(tmp_path, capsys):
    pdf = tmp_path / "vacio.pdf"
    pdf.write_bytes(b"no es un pdf")
    assert main(["--bd", f"sqlite:///{tmp_path / 'cli.db'}", "cargar-pdf", str(pdf)]) == 1
    assert "ERROR" in capsys.readouterr().err
