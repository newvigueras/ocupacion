"""Migraciones de la base de datos (Alembic)."""

from datetime import date

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from planificador.bd import Partida, crear_motor, preparar_bd
from planificador.bd.conexion import _config_alembic, migrar, version_bd
from planificador.bd.tablas import Base
from planificador.cli import main


def test_las_migraciones_coinciden_con_las_tablas(tmp_path):
    """Si se cambia tablas.py sin crear su migración, esta prueba falla."""
    motor = crear_motor(f"sqlite:///{tmp_path / 'nueva.db'}")
    assert preparar_bd(motor) == "creada"
    with motor.connect() as conexion:
        diferencias = compare_metadata(MigrationContext.configure(conexion), Base.metadata)
    assert diferencias == []
    actual, cabeza = version_bd(motor)
    assert actual == cabeza
    assert preparar_bd(motor) == "al día"


def test_base_anterior_a_alembic_se_adopta_sin_perder_datos(tmp_path):
    motor = crear_motor(f"sqlite:///{tmp_path / 'vieja.db'}")
    Base.metadata.create_all(motor)  # como se creaban hasta el 26/11/2026
    with motor.begin() as c:
        c.execute(text("DROP TABLE copia_seguridad"))  # aún no existía el 22/11
    with Session(motor) as s:
        s.add(Partida(codigo="26X00001", tipo_planta="injerto", categoria="Normal",
                      cultivo="Tomate", bandejas=10, entrada=date(2026, 12, 1),
                      liberacion=date(2027, 1, 1), entrega=date(2026, 12, 29)))
        s.commit()

    assert preparar_bd(motor) == "adoptada"
    assert "copia_seguridad" in inspect(motor).get_table_names()
    assert version_bd(motor)[0] == version_bd(motor)[1]
    with Session(motor) as s:
        assert s.get(Partida, "26X00001").bandejas == 10
    assert migrar(motor) == "al día"


def test_se_puede_deshacer_la_migracion(tmp_path):
    motor = crear_motor(f"sqlite:///{tmp_path / 'deshacer.db'}")
    migrar(motor)
    with motor.begin() as conexion:
        command.downgrade(_config_alembic(conexion), "base")
    assert set(inspect(motor).get_table_names()) <= {"alembic_version"}
    assert migrar(motor) == "creada"


def test_cli_preparar_dice_la_version(tmp_path, capsys):
    bd = ["--bd", f"sqlite:///{tmp_path / 'cli.db'}"]
    assert main(bd + ["preparar"]) == 0
    assert "Estructura de la base: creada (versión 0001)" in capsys.readouterr().out
    assert main(bd + ["preparar"]) == 0
    assert "al día (versión 0001)" in capsys.readouterr().out
