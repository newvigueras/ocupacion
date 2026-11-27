"""Fase 2: base de datos, bases de asignaciones y ocupación."""

from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from conftest import CABECERA, fila
from planificador.bd import (
    Calle, DatoReal, ErrorPlan, Invernadero, Planificador, crear_motor, guardar_partidas,
    preparar_bd,
)
from planificador.cli import main
from planificador.motor import Reglas, analizar_filas, leer_excel


@pytest.fixture
def motor(tmp_path):
    m = crear_motor(f"sqlite:///{tmp_path / 'prueba.db'}")
    preparar_bd(m)
    return m


@pytest.fixture
def s(motor, excel_ejemplo):
    with Session(motor) as sesion:
        guardar_partidas(sesion, leer_excel(excel_ejemplo))
        sesion.commit()
        yield sesion


@pytest.fixture
def plan(s):
    return Planificador(s)


def dia(plan, base, inv, fecha, ext=True):
    return plan.ocupacion_dia(base, fecha, incluir_ext=ext)[inv]


# --- Invernaderos, calles y calendario (§8, §17.2) ---------------------------------

def test_capacidades(motor):
    preparar_bd(motor)  # una segunda vez no duplica nada
    with Session(motor) as sesion:
        invs = sesion.scalars(select(Invernadero).where(Invernadero.especial.is_(False))).all()
        assert len(invs) == 40
        assert sesion.scalar(select(func.count(Calle.id))) == 1250
        por_sala = {}
        for i in invs:
            por_sala[i.sala] = por_sala.get(i.sala, 0) + i.capacidad
        assert por_sala == {"Sala 1": 28830, "Sala 2": 48872, "Sala 3": 18606,
                            "Sala 4": 26378, "Sala 5": 27433}
        assert sum(por_sala.values()) == 150119
        assert Planificador(sesion).calendario.n_semanas == 105


# --- Guardar partidas (fusión por código, §10.6) -----------------------------------

def test_guardar_fusiona_por_codigo(motor, excel_ejemplo):
    with Session(motor) as sesion:
        res = leer_excel(excel_ejemplo)
        assert guardar_partidas(sesion, res).nuevas == len(res.partidas)
        assert guardar_partidas(sesion, res).sin_cambios == len(res.partidas)

        reglas = Reglas.por_defecto()
        reglas.excepciones_partida["26X03373"] = 20
        r = guardar_partidas(sesion, leer_excel(excel_ejemplo, reglas=reglas))
        assert r.actualizadas == 1

        # Un Excel con una sola partida no borra las demás.
        solo = analizar_filas([CABECERA, fila("26X09999", "TINJ NUEVA", band=7)])
        assert guardar_partidas(sesion, solo).nuevas == 1
        plan = Planificador(sesion)
        assert plan.partida("26X03373").bandejas == 448
        assert plan.partida("26X02589").bandejas == 2028


# --- Bloques de una semana (§10.1) -------------------------------------------------

def test_bloques_de_semana(plan):
    bloques = plan.bloques_de_semana(49, "injerto")
    assert bloques[0].cultivo == "Jiffy 8x8"
    jiffy = bloques[0]
    assert jiffy.cliente == "CLIENTE PRUEBA"  # en Jiffy se separa por cliente
    assert {p.codigo for p in jiffy.partidas} >= {"26X02589", "26X03378", "26X03025"}
    tomate = next(b for b in bloques if b.cultivo == "Tomate")
    assert tomate.cliente == ""  # en el resto, no
    # Planta normal: entra a la semana 48 (siembra + 2).
    assert all(b.cultivo for b in plan.bloques_de_semana(48, "normal"))
    assert plan.bloques_de_semana(49, "normal") == []


def test_asignar_bloque_reparte_y_topa(plan):
    jiffy = plan.bloques_de_semana(49)[0]
    # Misma liberación: se reparte por código; 100 salen todas de 26X02589.
    assert plan.asignar_bloque(49, "injerto", jiffy.clave, "INV41", 100) == 100
    assert plan.asignado("bloques", "26X02589") == 100
    # Volver a escribir en la celda la sustituye.
    assert plan.asignar_bloque(49, "injerto", jiffy.clave, "INV41", 50) == 50
    assert plan.asignado("bloques", "26X02589") == 50
    # En otro invernadero solo cabe lo que le queda al bloque.
    puesto = plan.asignar_bloque(49, "injerto", jiffy.clave, "INV42", 10**6)
    assert puesto == jiffy.total - 50
    assert sum(plan.asignado("bloques", p.codigo) for p in jiffy.partidas) == jiffy.total


# --- Partidas, calles y bases independientes ----------------------------------------

def test_asignar_partida_topa_y_bases_independientes(plan):
    assert plan.asignar_partida("partidas", "26X02589", "INV41", 5000) == 2028
    assert plan.asignar_partida("partidas", "26X02589", "INV42", 100) == 0
    assert plan.asignar_partida("partidas", "26X02589", "INV41", 300) == 300  # sustituye
    assert plan.asignar_partida("partidas", "26X02589", "INV41", 200, calle=3) == 200
    assert plan.asignado("partidas", "26X02589") == 500
    assert plan.asignado("bloques", "26X02589") == 0
    assert dia(plan, "bloques", "INV41", date(2026, 12, 1), ext=False) == 0
    with pytest.raises(ErrorPlan, match="calle"):
        plan.asignar_partida("partidas", "26X02589", "INV41", 10, calle=9999)
    with pytest.raises(ErrorPlan, match="dato real"):
        plan.asignar_partida("real", "26X02589", "INV41", 10)


def test_ocupacion_entrada_y_liberacion_incluidas(plan):
    plan.asignar_partida("partidas", "26X02589", "INV41", 300)  # 30/11/2026 → 01/01/2027
    assert dia(plan, "partidas", "INV41", date(2026, 11, 29)) == 0
    assert dia(plan, "partidas", "INV41", date(2026, 11, 30)) == 300
    assert dia(plan, "partidas", "INV41", date(2027, 1, 1)) == 300
    assert dia(plan, "partidas", "INV41", date(2027, 1, 2)) == 0


def test_fecha_tecnico_manda(plan):
    plan.asignar_partida("partidas", "26X02589", "INV41", 300)
    jiffy = plan.bloques_de_semana(49)[0]
    plan.asignar_bloque(49, "injerto", jiffy.clave, "INV42", 100)
    assert plan.poner_fecha_tecnico("26X02589", date(2026, 12, 20)) == 2
    assert dia(plan, "partidas", "INV41", date(2026, 12, 20)) == 300
    assert dia(plan, "partidas", "INV41", date(2026, 12, 21)) == 0
    assert dia(plan, "bloques", "INV42", date(2026, 12, 21)) == 0
    plan.poner_fecha_tecnico("26X02589", None)
    assert dia(plan, "partidas", "INV41", date(2026, 12, 21)) == 300


# --- Real y «lo ya colocado» (§10.3 3b) ---------------------------------------------

def test_real_cuenta_y_se_sustituye(plan, s):
    s.add(DatoReal(codigo="26X02589", bandejas_real=361, fec_entrada_real=date(2026, 11, 28),
                   fuente="EXTENSIONES"))
    s.flush()
    assert plan.asignar_partida("real", "26X02589", "INV41", 1000) == 361
    d = date(2026, 12, 1)
    assert dia(plan, "real", "INV41", d) == 361
    assert dia(plan, "partidas", "INV41", d) == 361          # se cuenta lo colocado
    assert dia(plan, "partidas", "INV41", d, ext=False) == 0
    # 100 asignadas en el mismo invernadero: siguen contando 261 de Real + 100 del plan.
    plan.asignar_partida("partidas", "26X02589", "INV41", 100)
    assert dia(plan, "partidas", "INV41", d) == 361
    # 100 en otro: Real pierde 100 en INV41.
    plan.asignar_partida("partidas", "26X02589", "INV41", 0)
    plan.asignar_partida("partidas", "26X02589", "INV42", 100)
    assert dia(plan, "partidas", "INV41", d) == 261
    assert dia(plan, "partidas", "INV42", d) == 100
    # Real va por la entrada real: el 28/11/2026 es el sábado de la semana 48.
    assert [x.codigo for x in plan.partidas_reales(48)] == ["26X02589"]
    assert plan.partidas_reales(49) == []


def test_calles_con_real(plan, s):
    """Por calles, lo colocado en Real manda sobre lo planificado de la misma partida."""
    s.add(DatoReal(codigo="26X02589", bandejas_real=361, fec_entrada_real=date(2026, 11, 28),
                   fuente="EXTENSIONES"))
    s.flush()
    assert plan.asignar_calles("real", "26X02589", "INV41", {1: 92, 2: 500}) == ({1: 92, 2: 269}, 0)
    assert plan.asignar_calles("partidas", "26X02589", "INV41", {3: 50}) == ({3: 50}, 0)
    d1, d2 = date(2026, 11, 28), date(2027, 3, 1)
    occ = plan.ocupacion_calles("partidas", "INV41", d1, d2)
    assert (occ[1].pico, occ[2].pico, occ[3].pico) == (92, 269, 0)
    assert plan.ocupacion_calles("real", "INV41", d1, d2)[3].pico == 0
    with pytest.raises(ErrorPlan, match="no tiene calles"):
        plan.asignar_calles("partidas", "26X02589", "BASURA", {})
    with pytest.raises(ErrorPlan, match="Partidas o Real"):
        plan.asignar_calles("bloques", "26X02589", "INV41", {1: 5})


# --- Avisos (§5.8, §5.9) y máximo asignable -----------------------------------------

def test_exceso_basura_y_maximo(plan):
    assert plan.maximo_partida("partidas", "26X02589", "INV37") == 1996
    plan.asignar_partida("partidas", "26X02589", "INV37", 2028)  # se permite pasar
    excesos = plan.excedidos("partidas")
    assert [(e.invernadero, e.maximo, e.capacidad) for e in excesos] == [("INV37", 2028, 1996)]
    plan.asignar_partida("partidas", "26X02589", "INV37", 0)
    plan.asignar_partida("partidas", "26X02589", "BASURA", 2028)
    assert plan.excedidos("partidas") == []


def test_mezcla_de_cultivos(plan):
    plan.asignar_partida("partidas", "26X02589", "INV41", 100)  # Jiffy 8x8
    plan.asignar_partida("partidas", "26X02789", "INV41", 100)  # Jiffy 5x5: mismo cultivo
    assert plan.mezclas_cultivo("partidas") == []
    plan.asignar_partida("partidas", "26X03902", "INV41", 10)   # Pimiento
    mezcla = plan.mezclas_cultivo("partidas")
    assert mezcla[0].invernadero == "INV41" and mezcla[0].cultivos == ("Jiffy", "Pimiento")


# --- Órdenes de consola -------------------------------------------------------------

def test_cli_flujo_completo(tmp_path, excel_ejemplo, capsys):
    bd = ["--bd", f"sqlite:///{tmp_path / 'cli.db'}"]
    assert main(bd + ["preparar"]) == 0
    assert "150.119" in capsys.readouterr().out
    assert main(bd + ["importar", str(excel_ejemplo), "--guardar"]) == 0
    assert "nuevas" in capsys.readouterr().out
    assert main(bd + ["semana", "49"]) == 0
    assert "Jiffy 8x8" in capsys.readouterr().out
    assert main(bd + ["asignar-bloque", "49", "1", "INV41", "500"]) == 0
    assert main(bd + ["asignar-partida", "26X02789", "INV37", "1241"]) == 0
    assert main(bd + ["tecnico", "26X02789", "2026-12-24"]) == 0
    assert main(bd + ["ocupacion", "--base", "bloques", "--fecha", "2026-12-01"]) == 0
    assert "INV41" in capsys.readouterr().out
    assert main(bd + ["asignar-partida", "26X00000", "INV41", "5"]) == 1


def test_cli_avisos_solo_del_invernadero_tocado(tmp_path, excel_ejemplo, capsys):
    bd = ["--bd", f"sqlite:///{tmp_path / 'cli.db'}"]
    main(bd + ["importar", str(excel_ejemplo), "--guardar"])
    main(bd + ["asignar-partida", "26X02589", "INV41", "100"])   # Jiffy
    main(bd + ["asignar-partida", "26X03902", "INV41", "10"])    # Pimiento: mezcla en INV41
    capsys.readouterr()
    assert main(bd + ["asignar-partida", "26X02789", "INV42", "100"]) == 0
    out = capsys.readouterr().out
    assert "~ INV41" not in out and "otros 1 invernaderos tienen avisos" in out
    assert main(bd + ["asignar-partida", "26X03903", "INV41", "5"]) == 0
    out = capsys.readouterr().out
    assert "~ INV41: coinciden Jiffy, Pimiento, Tomate" in out and "otros" not in out
    assert main(bd + ["ocupacion", "--fecha", "2026-12-01"]) == 0
    assert "~ INV41" in capsys.readouterr().out  # aquí sale la lista entera
