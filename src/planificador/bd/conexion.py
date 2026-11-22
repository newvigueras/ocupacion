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

from sqlalchemy import Engine, create_engine, event, select
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


def preparar_bd(motor: Engine, año_inicio: int = 2026, año_fin: int = 2027) -> None:
    """Crea las tablas y, si están vacías, carga invernaderos, calles y calendario."""
    Base.metadata.create_all(motor)
    with Session(motor) as s:
        if s.scalar(select(Invernadero.id).limit(1)) is None:
            cargar_invernaderos(s)
        if s.get(Ajuste, "año_inicio") is None:
            s.add_all([Ajuste(clave="año_inicio", valor=str(año_inicio)),
                       Ajuste(clave="año_fin", valor=str(año_fin))])
        s.commit()


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
