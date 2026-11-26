"""Entorno de Alembic.

Lo usan ``preparar_bd`` (que pasa la conexión abierta en ``config.attributes``) y la orden
``alembic`` de la carpeta del proyecto (que toma la base de ``PLANIFICADOR_BD``).
"""

from alembic import context
from sqlalchemy import Connection

from planificador.bd.conexion import crear_motor, url_bd
from planificador.bd.tablas import Base

config = context.config
target_metadata = Base.metadata


def _migrar(conexion: Connection) -> None:
    context.configure(
        connection=conexion,
        target_metadata=target_metadata,
        # SQLite no sabe cambiar columnas: Alembic rehace la tabla («batch»).
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=url_bd(), target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
elif (conexion := config.attributes.get("connection")) is not None:
    _migrar(conexion)
else:
    with crear_motor().connect() as conexion:
        _migrar(conexion)
        conexion.commit()
