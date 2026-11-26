"""Usuarios de la aplicación web (fase 3).

Revisión: 0002
Anterior: 0001
Fecha: 2026-11-26 10:17:52.614667
"""

from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('usuario',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('nombre', sa.String(length=64), nullable=False),
    sa.Column('nombre_completo', sa.String(length=200), server_default='', nullable=False),
    sa.Column('rol', sa.String(length=16), server_default='consulta', nullable=False),
    sa.Column('clave_hash', sa.String(length=256), nullable=False),
    sa.Column('activo', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('creado_en', sa.DateTime(), nullable=False),
    sa.Column('ultimo_acceso', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('nombre')
    )


def downgrade() -> None:
    op.drop_table('usuario')
