from datetime import date

import pytest

from conftest import CABECERA, fila
from planificador.cli import main
from planificador.motor import Calendario, ErrorExcel, Reglas, analizar_filas, leer_excel
from planificador.motor.bandejas import redondear
from planificador.motor.clasificacion import categoria_injerto, cultivo, tipo_planta_de_codigo
from planificador.motor.excel import a_fecha, a_numero


@pytest.fixture
def res(excel_ejemplo):
    return leer_excel(excel_ejemplo)


# --- Bandejas: casos del documento maestro §17.1 -----------------------------

@pytest.mark.parametrize("codigo, bandejas, tasa", [
    ("26X02589", 2028, 15),   # Jiffy 8x8 con «tallos 15 cm»
    ("26X03378", 667, 15),    # «BRAZOS 20CM»
    ("26X03025", 24, 15),     # excepción por partida
    ("26X02999", 65, 15),     # excepción por partida
    ("26X02789", 1241, 54),   # Jiffy 5x5 TIJ6
    ("26X02476", 1100, 15),   # hidro «flor abierta»
    ("26X02560", 967, 15),    # hidro «MUY ADELATADA»
    ("26X03125", 138, 15),    # hidro «INDUCIDA A FLOR»
    ("26X02927", 833, 48),    # hidro 7,5x7,5
    ("26010905", 310, 40),    # taco TH02
    ("26010978", 616, 40),    # pepino PEH2
    ("26X02968", 333, 40),    # minipepino MIH2
    ("26X02997", 147, 15),    # pepino Jiffy PI35 (22/11/2026: 15, antes 18)
    ("26X02998", 160, 15),    # pepino Jiffy PI35 con «JIF» en la variedad
    ("26011200", 100, 84),    # calabacín CCH5 (22/11/2026)
])
def test_bandejas_casos_reales(res, codigo, bandejas, tasa):
    p = res.partidas[codigo]
    assert p.bandejas == bandejas
    assert p.plantas_por_bandeja == tasa


def test_tihi_usa_band(res):
    p = res.partidas["26X03900"]
    assert p.bandejas == 77 and p.plantas_por_bandeja is None and p.cultivo == "Hidropónico"


def test_jiffy_sin_medida_usa_band_y_avisa(res):
    p = res.partidas["26X03901"]
    assert p.bandejas == 55 and p.cultivo == "Jiffy"
    assert any(a.codigo == "26X03901" for a in res.avisos_de("sin_medida"))


def test_desarrolladas_y_excepciones_se_avisan(res):
    desarrolladas = {a.codigo for a in res.avisos_de("desarrollada")}
    assert {"26X02589", "26X03378", "26X02476", "26X02560", "26X03125"} <= desarrolladas
    assert {a.codigo for a in res.avisos_de("excepcion")} == {"26X03025", "26X02999"}


def test_excepcion_editable(excel_ejemplo):
    reglas = Reglas.por_defecto()
    assert leer_excel(excel_ejemplo, reglas=reglas).partidas["26X03373"].bandejas == 87
    reglas.excepciones_partida["26X03373"] = 20
    assert leer_excel(excel_ejemplo, reglas=reglas).partidas["26X03373"].bandejas == 448


def test_redondeo_medio_sube():
    assert redondear(2.5) == 3 and redondear(2.49) == 2


# --- Fechas -------------------------------------------------------------------

def test_fechas_injerto(res):
    p = res.partidas["26X02589"]
    assert p.entrada == date(2026, 11, 30)
    assert p.liberacion == date(2027, 1, 1)


def test_fecha_real_siembra_manda(res):
    assert res.partidas["26X03905"].entrada == date(2026, 11, 26)


def test_fechas_normal_y_componente(res):
    assert res.partidas["26010905"].entrada == date(2026, 11, 26)
    assert res.partidas["26P00001"].entrada == date(2026, 11, 26)


def test_omitidas_y_repetidas(res):
    omitidas = {a.codigo for a in res.avisos_de("omitida")}
    assert omitidas == {"26X03561", "26X03562", "26X03563"}
    assert res.partidas["26X04000"].bandejas == 11
    assert [a.codigo for a in res.avisos_de("repetida")] == ["26X04000"]


def test_codigos_desconocidos_se_ignoran(res):
    ignorados = {a.codigo for a in res.avisos_de("codigo_desconocido")}
    assert ignorados == {"H26/1187", "B26/8918", "23A00010", "26KC00001"}
    for c in ignorados:
        assert c not in res.partidas
    assert res.partidas["25010001"].tipo_planta == "normal"
    assert res.partidas["26F00001"].tipo_planta == "injerto"


# --- Clasificación ------------------------------------------------------------

def test_tipo_planta():
    assert tipo_planta_de_codigo("26X02589") == "injerto"
    assert tipo_planta_de_codigo("26YE00001") == "injerto"
    assert tipo_planta_de_codigo("26010905") == "normal"
    assert tipo_planta_de_codigo("26EC00001") == "normal"
    for c in ("26P1", "26C1", "26WE1", "26VE1"):
        assert tipo_planta_de_codigo(c) == "componente"


def test_eco_y_bio_son_distintos(res):
    p = res.partidas
    assert p["26X03902"].bio == "BIO"
    assert p["26YE00001"].bio == "ECO"
    assert p["26EC00001"].bio == "ECO"
    assert p["26X02589"].bio == "No BIO"
    # En componentes la lucha biológica no cuenta.
    assert p["26VE00001"].bio == "ECO"
    assert p["26P00001"].bio == ""


def test_categorias(res):
    p = res.partidas
    assert p["26X02589"].categoria == "Jiffy"  # JIF gana a 2HV
    assert p["26X02476"].categoria == "Hidro"
    assert p["26X03903"].categoria == "Pinzado" and p["26X03903"].etapa == "3HV"
    assert p["26X03904"].categoria == "Cotiledon"
    assert p["26WE00001"].categoria == "Patrón"
    assert p["26VE00001"].categoria == "Variedad"
    assert p["26010905"].categoria == "Normal"


def test_cultivos(res):
    p = res.partidas
    assert p["26X02589"].cultivo == "Jiffy 8x8"
    assert p["26X02789"].cultivo == "Jiffy 5x5"
    assert p["26X02927"].cultivo == "Hidropónico" and p["26X02927"].medida == "7.5x7.5"
    assert p["26X02968"].cultivo == "Pepino"
    assert p["26X03904"].cultivo == "Sandía"
    assert p["26X03902"].cultivo == "Pimiento"
    assert p["26010905"].cultivo == "Tomate"
    assert p["26011200"].cultivo == "Calabacín"  # 22/11/2026: línea propia
    assert cultivo("CALABACÍN SINATRA", "Normal") == "Calabacín"
    assert cultivo("LECHUGA X", "Normal") == "Otros"
    assert cultivo("MELÓN X", "Normal") == "Melón"
    assert categoria_injerto("TINJ X 2HV") == "Pinzado"


def test_estilo(res):
    assert res.partidas["26X03903"].estilo == "Almería"  # «CASI»
    assert res.partidas["26X03904"].estilo == "Almería"
    assert res.partidas["26X02589"].estilo == "Normal"


def test_formato_y_cliente(res):
    p = res.partidas["26X02589"]
    assert p.formato == "216" and p.cliente == "CLIENTE PRUEBA"


# --- Conversión de celdas -----------------------------------------------------

def test_conversiones():
    assert a_numero("30.420") == 30420
    assert a_numero("1.234,5") == 1234.5
    assert a_numero("7,5") == 7.5
    assert a_fecha("24/11/2026") == date(2026, 11, 24)
    assert a_fecha(46350) == date(2026, 11, 24)


# --- Calendario (§17.2) -------------------------------------------------------

def test_calendario_2026_2027():
    cal = Calendario(2026, 2027)
    assert cal.n_semanas == 105 and cal.n_dias == 730
    s1 = cal.semana(1)
    assert (s1.inicio, s1.fin, s1.etiqueta) == (date(2026, 1, 1), date(2026, 1, 3), "1/26")
    s47 = cal.semana(47)
    assert (s47.inicio, s47.fin, s47.etiqueta) == (date(2026, 11, 15), date(2026, 11, 21), "47/26")
    ult = cal.semana(105)
    assert (ult.inicio, ult.fin, ult.etiqueta) == (date(2027, 12, 26), date(2027, 12, 31), "1/28")


def test_partida_en_semanas(res):
    cal = Calendario(2026, 2027)
    p = res.partidas["26X02589"]
    assert list(cal.semanas_de_partida(p)) == [49, 50, 51, 52, 53]
    en_49 = {x.partida.codigo: x for x in cal.partidas_de_semana(res.partidas.values(), 49)}
    assert en_49["26X02589"].nueva and not en_49["26X02589"].sale
    en_53 = {x.partida.codigo: x for x in cal.partidas_de_semana([p], 53)}
    assert en_53["26X02589"].sale and not en_53["26X02589"].nueva


# --- Correcciones de la revisión (22/11/2026) -----------------------------------

def test_excepcion_sin_importar_mayusculas(excel_ejemplo):
    reglas = Reglas.por_defecto()
    reglas.excepciones_partida[" 26x03373 "] = 20
    assert leer_excel(excel_ejemplo, reglas=reglas).partidas["26X03373"].bandejas == 448


def test_repetida_no_duplica_avisos():
    filas = [CABECERA,
             fila("26X05000", "TINJ X JIF", tipo="TIJ2", pl=1500, obs="TALLOS 15 CM"),
             fila("26X05000", "TINJ X JIF", tipo="TIJ2", pl=3000, obs="TALLOS 15 CM")]
    res = analizar_filas(filas)
    assert res.partidas["26X05000"].bandejas == 200
    desarrolladas = res.avisos_de("desarrollada")
    assert len(desarrolladas) == 1 and desarrolladas[0].fila == 3
    assert len(res.avisos_de("repetida")) == 1


def test_excel_danado_o_xls_da_error_claro(tmp_path):
    danado = tmp_path / "danado.xlsx"
    danado.write_bytes(b"esto no es un Excel")
    with pytest.raises(ErrorExcel, match="no se puede abrir"):
        leer_excel(danado)
    antiguo = tmp_path / "antiguo.xls"
    antiguo.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(ErrorExcel, match=r"\.xls antiguo"):
        leer_excel(antiguo)


def test_cli_sigue_con_los_demas_archivos(tmp_path, excel_ejemplo, capsys):
    danado = tmp_path / "danado.xlsx"
    danado.write_bytes(b"esto no es un Excel")
    assert main(["importar", str(danado), str(excel_ejemplo)]) == 1
    salida = capsys.readouterr()
    assert "ERROR" in salida.err and "prevision.xlsx" in salida.out
