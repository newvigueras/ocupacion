"""Recalcular todas las semanas (§10.4) y reimportar los Excel (§10.6)."""

from datetime import date, timedelta

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import CABECERA, S, fila
from planificador.bd import (
    Asignacion, DatoReal, FechaTecnico, Planificador, crear_motor, guardar_partidas, preparar_bd,
)
from planificador.bd.recalcular import (
    copias, recalcular_base, recalcular_todo, reimportar, restaurar_copia,
)
from planificador.cli import main
from planificador.motor import ErrorExcel, analizar_filas, leer_excel


@pytest.fixture
def s(tmp_path, excel_ejemplo):
    motor = crear_motor(f"sqlite:///{tmp_path / 'recalc.db'}")
    preparar_bd(motor)
    with Session(motor) as sesion:
        guardar_partidas(sesion, leer_excel(excel_ejemplo))
        sesion.commit()
        yield sesion


@pytest.fixture
def plan(s):
    return Planificador(s)


def excel(tmp_path, nombre, filas):
    wb = Workbook()
    ws = wb.active
    ws.title = "SALIDA DE TOMATE CON FECHA PREV"
    ws.append(CABECERA)
    for f in filas:
        ws.append(f)
    ruta = tmp_path / nombre
    wb.save(ruta)
    return ruta


def cambiar(s, *filas):
    """Simula un Excel nuevo con esas partidas (fusiona por código)."""
    guardar_partidas(s, analizar_filas([CABECERA, *filas]))
    s.flush()


def asignaciones(s, base):
    return list(s.scalars(select(Asignacion).where(Asignacion.base == base)
                          .order_by(Asignacion.id)))


# Cambios del «Excel nuevo»: 26X02589 baja de 2.028 a 400 bandejas (6.000 plantas ÷ 15) y
# 26X03902 entra una semana más tarde y deja de ser BIO.
MENOS = fila("26X02589", "TINJ BRONSKI 141 2BR 2HV JIF", tipo="TIJ2", pl=6000, band=2535,
             obs="TALLOS 15 CM")
OTRA_SEMANA = fila("26X03902", "PIINJ X", band=40, lucha="N", siembra=S + timedelta(days=7))


def test_sin_cambios_no_toca_nada(s, plan):
    jiffy = plan.bloques_de_semana(49)[0]
    plan.asignar_bloque(49, "injerto", jiffy.clave, "INV41", 300)
    plan.asignar_partida("partidas", "26X02589", "INV41", 1000)
    plan.asignar_partida("partidas", "26X02589", "INV41", 500, calle=3)
    antes = {b: [(a.codigo, a.invernadero_id, a.calle_id, a.bandejas, a.entrada, a.liberacion)
                 for a in asignaciones(s, b)] for b in ("bloques", "partidas")}
    informes = recalcular_todo(s)
    assert [i.base for i in informes] == ["bloques", "partidas", "real"]
    for inf in informes[:2]:
        assert (inf.bandejas_antes, inf.bandejas_despues, inf.recortadas) == (1500 if inf.base
               == "partidas" else 300, 1500 if inf.base == "partidas" else 300, 0)
        assert inf.semanas_cambiadas == [] and inf.copia_id
        assert [(a.codigo, a.invernadero_id, a.calle_id, a.bandejas, a.entrada, a.liberacion)
                for a in asignaciones(s, inf.base)] == antes[inf.base]
    assert informes[2].vacia and informes[2].copia_id is None


def test_partidas_recorta_y_mueve_de_semana(s, plan):
    plan.asignar_partida("partidas", "26X02589", "INV41", 1000)
    plan.asignar_partida("partidas", "26X02589", "INV41", 500, calle=3)
    plan.asignar_partida("partidas", "26X03902", "INV42", 40)
    s.add(Asignacion(base="partidas", semana=49, codigo="26X77777", invernadero_id=1,
                     bandejas=12, entrada=date(2026, 11, 30), liberacion=date(2026, 12, 20)))
    s.flush()
    cambiar(s, MENOS, OTRA_SEMANA)

    inf = recalcular_base(s, "partidas")
    # Se rehace en el orden en que se puso: la celda de INV41 se queda las 400 y la calle 3, 0.
    assert (inf.bandejas_antes, inf.bandejas_despues, inf.recortadas) == (1552, 452, 1100)
    assert (inf.huerfanas, inf.otra_semana, inf.cambio_bio) == (1, 1, 40)
    assert inf.semanas_cambiadas == [(49, "", 1552, 412), (50, "", 0, 40)]
    filas = {(a.codigo, a.calle_id is not None): a for a in asignaciones(s, "partidas")}
    assert filas[("26X02589", False)].bandejas == 400
    assert ("26X02589", True) not in filas
    movida = filas[("26X03902", False)]
    assert (movida.semana, movida.entrada, movida.bio) == (50, date(2026, 12, 7), "No BIO")
    assert filas[("26X77777", False)].bandejas == 12  # la huérfana, tal cual

    # La copia deja la base como estaba.
    copia, previa = restaurar_copia(s, inf.copia_id)
    assert copia.bandejas == 1552 and previa.bandejas == 452
    assert sum(a.bandejas for a in asignaciones(s, "partidas")) == 1552
    assert [c.motivo for c in copias(s, "partidas")][0].startswith("antes de restaurar")


def test_bloques_topa_al_total_del_bloque(s, plan):
    jiffy = plan.bloques_de_semana(49)[0]
    piinj = next(b for b in plan.bloques_de_semana(49) if "26X03902" in
                 {p.codigo for p in b.partidas})
    plan.asignar_bloque(49, "injerto", jiffy.clave, "INV41", jiffy.total)
    plan.asignar_bloque(49, "injerto", piinj.clave, "INV42", 40)
    total_antes = jiffy.total  # (el bloque se calcula con las partidas al día)
    cambiar(s, MENOS, OTRA_SEMANA)

    inf = recalcular_base(s, "bloques")
    nuevo = next(b for b in plan.bloques_de_semana(49) if b.clave == jiffy.clave)
    assert total_antes - nuevo.total == 1628
    assert inf.recortadas == 1628
    # 26X03902 ya no entra la semana 49: su trozo se deja sin tocar (el bloque es de esa semana).
    assert (inf.otra_semana, inf.huerfanas) == (1, 0)
    assert plan.asignado("bloques", "26X02589") == 400
    assert plan.asignado("bloques", "26X03902") == 40
    assert inf.bandejas_despues == nuevo.total + 40
    assert inf.semanas_cambiadas == [(49, "injerto", total_antes + 40, nuevo.total + 40)]


def test_real_con_menos_bandejas_y_fecha_nueva(s, plan):
    s.add(DatoReal(codigo="26X02589", bandejas_real=300, fec_entrada_real=date(2026, 11, 30),
                   fuente="MANUAL"))
    s.flush()
    plan.asignar_partida("real", "26X02589", "INV41", 300)
    d = plan.dato_real("26X02589")
    d.bandejas_real, d.fecha_salida_real = 200, date(2026, 12, 15)
    inf = recalcular_base(s, "real")
    assert (inf.bandejas_despues, inf.recortadas) == (200, 100)
    (a,) = asignaciones(s, "real")
    assert (a.bandejas, a.liberacion) == (200, date(2026, 12, 15))


def test_reimportar_desde_cero(tmp_path, s, plan):
    for codigo, fuente in (("26X02589", "EXTENSIONES"), ("26X03903", "EXTENSIONCALLES"),
                           ("26X03904", "PDF_TUNELES")):
        s.add(DatoReal(codigo=codigo, bandejas_real=50, fec_entrada_real=date(2026, 11, 30),
                       fuente=fuente))
    s.add(FechaTecnico(codigo="26X03904", fecha=date(2026, 12, 24)))
    s.flush()
    plan.asignar_partida("real", "26X02589", "INV41", 30)            # se queda (sin calle)
    plan.asignar_partida("real", "26X02589", "INV41", 20, calle=3)   # se quita (calle a mano)
    plan.asignar_partida("real", "26X03903", "INV42", 50, calle=5)   # se queda (por calle)
    plan.asignar_partida("real", "26X03904", "INV43", 50)            # se quita
    plan.asignar_partida("partidas", "26X02589", "INV41", 100)
    plan.asignar_bloque(49, "injerto", plan.bloques_de_semana(49)[0].clave, "INV41", 100)

    nuevo = excel(tmp_path, "nuevo.xlsx", [MENOS, OTRA_SEMANA, fila("26X09999", "TINJ X", band=7)])
    r = reimportar(s, [nuevo])
    assert r.modo == "cero"
    (a,) = r.archivos
    assert (a.partidas, a.guardado.nuevas, a.guardado.actualizadas) == (3, 1, 2)
    assert r.quitadas == {"bloques": 1, "partidas": 1, "real": 2} and r.quedan_real == 2
    assert set(r.copias) == {"bloques", "partidas", "real"}
    assert asignaciones(s, "bloques") == [] and asignaciones(s, "partidas") == []
    assert sorted((a.codigo, a.bandejas) for a in asignaciones(s, "real")) == [
        ("26X02589", 30), ("26X03903", 50)]
    assert plan.partida("26X02589").bandejas == 400
    assert plan.fecha_tecnico("26X03904") == date(2026, 12, 24)  # se conserva
    assert plan.dato_real("26X03904").fuente == "PDF_TUNELES"     # y los datos de PDF


def test_reimportar_conservando(tmp_path, s, plan):
    plan.asignar_partida("partidas", "26X02589", "INV41", 1000)
    nuevo = excel(tmp_path, "nuevo.xlsx", [MENOS])
    r = reimportar(s, [nuevo], conservar=True)
    assert r.modo == "conservar" and r.quitadas == {}
    inf = {i.base: i for i in r.informes}
    assert (inf["partidas"].bandejas_despues, inf["partidas"].recortadas) == (400, 600)


def test_reimportar_el_mas_nuevo_manda_y_si_falla_no_toca_nada(tmp_path, s, plan):
    import os

    viejo = excel(tmp_path, "viejo.xlsx", [fila("26X02589", "TINJ X", band=10)])
    nuevo = excel(tmp_path, "nuevo.xlsx", [fila("26X02589", "TINJ X", band=20)])
    os.utime(viejo, (1_700_000_000, 1_700_000_000))
    reimportar(s, [nuevo, viejo])
    assert plan.partida("26X02589").bandejas == 20

    plan.asignar_partida("partidas", "26X02589", "INV41", 20)
    roto = tmp_path / "roto.xlsx"
    roto.write_bytes(b"no es un excel")
    with pytest.raises(ErrorExcel):
        reimportar(s, [excel(tmp_path, "otro.xlsx", [MENOS]), roto])
    assert plan.partida("26X02589").bandejas == 20
    assert plan.asignado("partidas", "26X02589") == 20


def test_cli_recalcular_reimportar_y_copias(tmp_path, excel_ejemplo, capsys):
    bd = ["--bd", f"sqlite:///{tmp_path / 'cli.db'}"]
    assert main([*bd, "recalcular"]) == 0
    assert "nada que recalcular" in capsys.readouterr().out
    assert main([*bd, "importar", str(excel_ejemplo), "--guardar"]) == 0
    assert main([*bd, "asignar-partida", "26X02589", "INV41", "1000"]) == 0
    nuevo = excel(tmp_path, "nuevo.xlsx", [MENOS])
    capsys.readouterr()

    assert main([*bd, "reimportar", str(nuevo), "--conservar"]) == 0
    out = capsys.readouterr().out
    assert "Recortadas por haber ahora menos bandejas: 600" in out
    assert "1.000 → 400" in out

    assert main([*bd, "reimportar", str(nuevo)]) == 0
    assert "quitadas 0 asignaciones de Bloques, 1 de Partidas y 0 de Real" in \
        capsys.readouterr().out
    assert main([*bd, "copias", "--base", "partidas"]) == 0
    out = capsys.readouterr().out
    assert "reimportar desde cero" in out and "reimportar (conservar)" in out
    ultima = int(out.split("n.º")[1].split()[0])
    assert main([*bd, "restaurar-copia", str(ultima)]) == 0
    assert "restaurada" in capsys.readouterr().out
    assert main([*bd, "restaurar-copia", "999"]) == 1
    assert main([*bd, "reimportar", str(tmp_path / "no_existe.xlsx")]) == 1
    assert "No se ha cambiado nada" in capsys.readouterr().err

