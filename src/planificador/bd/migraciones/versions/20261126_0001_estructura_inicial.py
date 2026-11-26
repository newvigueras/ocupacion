"""Estructura inicial: las tablas tal como estaban el 26/11/2026 (fase 2 completa).

Las bases creadas antes de Alembic se «adoptan» en esta versión (ver ``conexion.migrar``).

Revisión: 0001
Anterior: 
Fecha: 2026-11-26 10:10:20.416424
"""

from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('ajuste',
    sa.Column('clave', sa.String(length=64), nullable=False),
    sa.Column('valor', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('clave')
    )
    op.create_table('copia_seguridad',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('base', sa.String(length=16), nullable=False),
    sa.Column('motivo', sa.String(length=64), nullable=False),
    sa.Column('asignaciones', sa.Integer(), nullable=False),
    sa.Column('bandejas', sa.Integer(), nullable=False),
    sa.Column('datos', sa.Text(), nullable=False),
    sa.Column('creada_en', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('copia_seguridad', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_copia_seguridad_base'), ['base'], unique=False)

    op.create_table('dato_real',
    sa.Column('codigo', sa.String(length=32), nullable=False),
    sa.Column('bandejas_real', sa.Integer(), nullable=False),
    sa.Column('fec_entrada_real', sa.Date(), nullable=True),
    sa.Column('fecha_entrega_pdf', sa.Date(), nullable=True),
    sa.Column('fecha_salida_real', sa.Date(), nullable=True),
    sa.Column('fecha_liberacion_ext', sa.Date(), nullable=True),
    sa.Column('liberacion_estimada', sa.Boolean(), nullable=False),
    sa.Column('localizacion', sa.Text(), nullable=False),
    sa.Column('observaciones', sa.Text(), nullable=False),
    sa.Column('tipo_planta', sa.String(length=16), nullable=False),
    sa.Column('cliente', sa.String(length=200), nullable=False),
    sa.Column('variedad', sa.String(length=200), nullable=False),
    sa.Column('categoria', sa.String(length=32), nullable=False),
    sa.Column('cultivo', sa.String(length=32), nullable=False),
    sa.Column('bio', sa.String(length=16), nullable=False),
    sa.Column('estilo', sa.String(length=16), nullable=False),
    sa.Column('formato', sa.String(length=32), nullable=False),
    sa.Column('fuente', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('codigo')
    )
    op.create_table('fecha_tecnico',
    sa.Column('codigo', sa.String(length=32), nullable=False),
    sa.Column('fecha', sa.Date(), nullable=False),
    sa.PrimaryKeyConstraint('codigo')
    )
    op.create_table('invernadero',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('nombre', sa.String(length=32), nullable=False),
    sa.Column('sala', sa.String(length=32), nullable=True),
    sa.Column('capacidad', sa.Integer(), nullable=False),
    sa.Column('especial', sa.Boolean(), nullable=False),
    sa.Column('orden', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('nombre')
    )
    op.create_table('partida',
    sa.Column('codigo', sa.String(length=32), nullable=False),
    sa.Column('tipo_planta', sa.String(length=16), nullable=False),
    sa.Column('cliente', sa.String(length=200), nullable=False),
    sa.Column('variedad', sa.String(length=200), nullable=False),
    sa.Column('categoria', sa.String(length=32), nullable=False),
    sa.Column('cultivo', sa.String(length=32), nullable=False),
    sa.Column('medida', sa.String(length=16), nullable=False),
    sa.Column('bio', sa.String(length=16), nullable=False),
    sa.Column('bandejas', sa.Integer(), nullable=False),
    sa.Column('entrada', sa.Date(), nullable=False),
    sa.Column('liberacion', sa.Date(), nullable=False),
    sa.Column('siembra', sa.Date(), nullable=True),
    sa.Column('siembra_real', sa.Date(), nullable=True),
    sa.Column('entrega', sa.Date(), nullable=False),
    sa.Column('formato', sa.String(length=32), nullable=False),
    sa.Column('estilo', sa.String(length=16), nullable=False),
    sa.Column('etapa', sa.String(length=16), nullable=False),
    sa.Column('tipo_siembra', sa.String(length=16), nullable=False),
    sa.Column('observaciones', sa.Text(), nullable=False),
    sa.Column('bandejas_excel', sa.Float(), nullable=True),
    sa.Column('plantas', sa.Float(), nullable=True),
    sa.Column('plantas_por_bandeja', sa.Float(), nullable=True),
    sa.Column('regla_bandejas', sa.String(length=120), nullable=False),
    sa.Column('planta_desarrollada', sa.Boolean(), nullable=False),
    sa.Column('archivo', sa.String(length=200), nullable=False),
    sa.Column('actualizada_en', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('codigo')
    )
    with op.batch_alter_table('partida', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_partida_entrada'), ['entrada'], unique=False)
        batch_op.create_index(batch_op.f('ix_partida_tipo_planta'), ['tipo_planta'], unique=False)

    op.create_table('calle',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('invernadero_id', sa.Integer(), nullable=False),
    sa.Column('numero', sa.Integer(), nullable=False),
    sa.Column('zona', sa.String(length=16), nullable=False),
    sa.Column('capacidad', sa.Integer(), nullable=False),
    sa.Column('estimada', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['invernadero_id'], ['invernadero.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('invernadero_id', 'numero')
    )
    op.create_table('asignacion',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('base', sa.String(length=16), nullable=False),
    sa.Column('semana', sa.Integer(), nullable=False),
    sa.Column('codigo', sa.String(length=32), nullable=False),
    sa.Column('invernadero_id', sa.Integer(), nullable=False),
    sa.Column('calle_id', sa.Integer(), nullable=True),
    sa.Column('bandejas', sa.Integer(), nullable=False),
    sa.Column('entrada', sa.Date(), nullable=True),
    sa.Column('liberacion', sa.Date(), nullable=True),
    sa.Column('tipo_planta', sa.String(length=16), nullable=False),
    sa.Column('cultivo', sa.String(length=32), nullable=False),
    sa.Column('categoria', sa.String(length=32), nullable=False),
    sa.Column('bio', sa.String(length=16), nullable=False),
    sa.Column('cliente', sa.String(length=200), nullable=False),
    sa.Column('creada_en', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['calle_id'], ['calle.id'], ),
    sa.ForeignKeyConstraint(['invernadero_id'], ['invernadero.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('asignacion', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_asignacion_base'), ['base'], unique=False)
        batch_op.create_index(batch_op.f('ix_asignacion_codigo'), ['codigo'], unique=False)
        batch_op.create_index(batch_op.f('ix_asignacion_invernadero_id'), ['invernadero_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_asignacion_semana'), ['semana'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('asignacion', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_asignacion_semana'))
        batch_op.drop_index(batch_op.f('ix_asignacion_invernadero_id'))
        batch_op.drop_index(batch_op.f('ix_asignacion_codigo'))
        batch_op.drop_index(batch_op.f('ix_asignacion_base'))

    op.drop_table('asignacion')
    op.drop_table('calle')
    with op.batch_alter_table('partida', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_partida_tipo_planta'))
        batch_op.drop_index(batch_op.f('ix_partida_entrada'))

    op.drop_table('partida')
    op.drop_table('invernadero')
    op.drop_table('fecha_tecnico')
    op.drop_table('dato_real')
    with op.batch_alter_table('copia_seguridad', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_copia_seguridad_base'))

    op.drop_table('copia_seguridad')
    op.drop_table('ajuste')
