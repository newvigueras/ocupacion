"""PDF de túneles y de cargas (documento maestro §12.1–12.2), a partir del texto."""

from datetime import date

import pytest
from sqlalchemy.orm import Session

from planificador.bd import DatoReal, Planificador, crear_motor, guardar_partidas, preparar_bd
from planificador.bd.pdf import (
    ErrorPdf, Palabra, cargar_texto, formato_de_grupo, leer_linea_cargas, leer_parte_cargas,
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


# Formato tal como sale de un parte real con pdfplumber (julio de 2026), clientes cambiados:
# miles con coma, bandejas con decimal, observaciones en dos columnas, la misma partida en dos
# partes (una sin Band.Sol) y subtotales al final de cada grupo.
TUNELES_REAL = "\n".join([
    "Empresa                                        Ejercicio           Informe"
    "                                        Fecha         Pág",
    "G.Semilleros La Sala S.L.                                        2026          Listado Salidas"
    " Previstas de Prendimiento con Ubicación                                        11/07/2026"
    "           1",
    "Cliente                                        Obsi                                FecPrevSal",
    "NºParte           Fecha Entrega       Nº Partida     Band.Sol  Variedad"
    "                                      Nº Plantas        Nº Band.  Localizacion"
    "                         Fec Entrada  Obs Inj                              FecRealSal"
    "   Inv     Sec1   Sec2",
    "TI15 - TOMATE INJ 150",
    "007 - BI",
    "26006234              20/07/2026      26X00365         192  CLIENTE UNO SL"
    "             13,910             93.0  82                                        06/07/2026"
    "                                        12/07/2026",
    "TINJ VARIEDAD 141                                        PATRON SOBRANTE",
    "_________",
    "26006240              06/07/2026      26X00365            CLIENTE UNO SL"
    "             15,082           101.0   82-83                                     06/07/2026"
    "  HEREDERO                            12/07/2026",
    "TINJ VARIEDAD 141",
    "_________",
    "28,992           194.0",
    "28,992           194.0",
    "TIJ6 - TINJ JIFFY 5x5",
    "No definido",
    "26006241              20/07/2026      25F08946             CLIENTE DOS LDA"
    "                    216               1.0  81                                        06/07/2026"
    "                                        12/07/2026",
    "TINJ HEREDERO DEF JIF                                        P. NARANJA JIFFY",
    "_________",
    "216               1.0",
    "29,208           195.0",
])

# Hoja de control del técnico: parecida, pero otras columnas. No se debe leer como túneles.
HOJA_TECNICO = "\n".join([
    "LISTADO SALIDAS PREVISTAS DE PRENDIMIENTO CON UBICACION",
    "N PARTE F. ENTREGA     PARTIDA / VARIEDAD             CLIENTE           F. ENTRADA F.PREV"
    "  PLANTAS BAND.  ORIGEN  CALLE REAL",
    "PI48 - PEPINO INJ 48                   NO DEFINIDO",
    "26006095  08/07/2026   25X06717                    CLIENTE TRES SL         25/06/2026"
    "   02/07                       106",
    "1.472  31",
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


def test_parte_injerto_con_formato_real():
    rs = leer_parte_injerto(TUNELES_REAL)
    assert [(r.partida, r.band_sol, r.bandejas, r.localizacion) for r in rs] == [
        ("26X00365", 192, 93.0, "82"), ("26X00365", None, 101.0, "82-83"),
        ("25F08946", None, 1.0, "81")]
    a, b, c = rs
    assert (a.fecha_entrega, a.fec_entrada, a.fec_real_sal) == ("20/07/2026", "06/07/2026", "12/07/2026")
    assert a.observaciones == "TINJ VARIEDAD 141 PATRON SOBRANTE"
    assert (b.fecha_entrega, b.observaciones) == ("06/07/2026", "HEREDERO TINJ VARIEDAD 141")
    assert (c.categoria_nombre, c.bio, c.cliente) == ("TINJ JIFFY 5x5", "No definido", "CLIENTE DOS LDA")
    assert sum(r.bandejas for r in rs) == 195  # el total del parte


def test_otros_listados_no_se_cargan(s):
    assert tipo_de_texto(HOJA_TECNICO) is None
    with pytest.raises(ErrorPdf, match="ni de carga"):
        cargar_texto(s, HOJA_TECNICO, "hoja.pdf")
    # Forzando el tipo sí se intenta (lo decide quien lo pide).
    assert cargar_texto(s, TUNELES_REAL, "real.pdf", "tuneles").bandejas == 195


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
