"""Conexión a la base de datos.

La dirección sale de la variable de entorno ``PLANIFICADOR_BD``. Por defecto es un
archivo SQLite (``planificador.db``) en la carpeta actual; para la base común de la
empresa se usará PostgreSQL (``postgresql+psycopg://usuario:clave@servidor/planificador``)
sin cambiar el código.
"""

from __future__ import annotations

import json
import os
from importlib import resources

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, event, inspect, select
from sqlalchemy.orm import Session

from .tablas import BASURA, CAPACIDAD_BASURA, Ajuste, Base, Calle, Invernadero

URL_POR_DEFECTO = "sqlite:///planificador.db"


def url_bd() -> str:
    return os.environ.get("PLANIFICADOR_BD", URL_POR_DEFECTO)


def crear_motor(url: str | None = None) -> Engine:
    url = url or url_bd()
    motor = create_engine(url)
    if url.startswith("sqlite"):
        @event.listens_for(motor, "connect")
        def _claves_ajenas(conexion, _registro):  # SQLite no las comprueba por defecto
            cursor = conexion.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return motor


#: Versión en la que se «adoptan» las bases creadas antes de usar Alembic (22–26/11/2026).
VERSION_ADOPCION = "0001"
#: Tablas de esa versión: al adoptar solo se crean las que falten de estas (las posteriores
#: las crean sus migraciones).
TABLAS_ADOPCION = ("ajuste", "invernadero", "calle", "partida", "fecha_tecnico", "dato_real",
                   "asignacion", "copia_seguridad")


def _config_alembic(conexion: Connection) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", "planificador.bd:migraciones")
    cfg.attributes["connection"] = conexion
    return cfg


def version_bd(motor: Engine) -> tuple[str | None, str]:
    """(versión de la base, última versión que conoce la aplicación)."""
    with motor.connect() as conexion:
        actual = MigrationContext.configure(conexion).get_current_revision()
        cfg = _config_alembic(conexion)
    return actual, ScriptDirectory.from_config(cfg).get_current_head()


def migrar(motor: Engine) -> str:
    """Pone la estructura de la base al día con las migraciones de ``migraciones/versions``.

    Una base vacía se crea entera. Una base creada antes de Alembic (tiene tablas pero no
    ``alembic_version``) se completa con las tablas que le falten y se marca en la versión
    de adopción; después se aplican las migraciones posteriores. Devuelve qué se ha hecho:
    'al día', 'creada', 'adoptada' o 'actualizada'.
    """
    with motor.begin() as conexion:
        cfg = _config_alembic(conexion)
        cabeza = ScriptDirectory.from_config(cfg).get_current_head()
        actual = MigrationContext.configure(conexion).get_current_revision()
        if actual == cabeza:
            return "al día"
        tablas = set(inspect(conexion).get_table_names())
        if actual is None and "partida" in tablas:
            Base.metadata.create_all(conexion, tables=[Base.metadata.tables[n]
                                                       for n in TABLAS_ADOPCION])
            command.stamp(cfg, VERSION_ADOPCION)
            command.upgrade(cfg, "head")
            return "adoptada"
        command.upgrade(cfg, "head")
        return "creada" if actual is None else "actualizada"


def preparar_bd(motor: Engine, año_inicio: int = 2026, año_fin: int = 2027) -> str:
    """Pone las tablas al día (migraciones) y, si están vacías, carga invernaderos, calles
    y calendario. Devuelve lo que ha hecho ``migrar``."""
    estado = migrar(motor)
    with Session(motor) as s:
        if s.scalar(select(Invernadero.id).limit(1)) is None:
            cargar_invernaderos(s)
        if s.get(Ajuste, "año_inicio") is None:
            s.add_all([Ajuste(clave="año_inicio", valor=str(año_inicio)),
                       Ajuste(clave="año_fin", valor=str(año_fin))])
        s.commit()
    return estado


def cargar_invernaderos(s: Session) -> None:
    """40 invernaderos y 1.250 calles (§8) más el destino especial BASURA."""
    texto = resources.files("planificador.datos").joinpath("invernaderos.json").read_text("utf-8")
    datos = json.loads(texto)["invernaderos"]
    for orden, inv in enumerate(datos):
        calles = [Calle(numero=c["calle"], zona=c["zona"], capacidad=c["capacidad"],
                        estimada=c.get("estimada", False)) for c in inv["calles"]]
        s.add(Invernadero(nombre=inv["nombre"], sala=inv["sala"], orden=orden,
                          capacidad=sum(c.capacidad for c in calles), calles=calles))
    s.add(Invernadero(nombre=BASURA, sala=None, capacidad=CAPACIDAD_BASURA, especial=True,
                      orden=len(datos)))
