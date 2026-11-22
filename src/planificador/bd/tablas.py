"""Tablas de la base de datos común (documento maestro §4).

Las tres bases de asignaciones (Bloques, Partidas y Real) son una sola tabla
``asignacion`` con la columna ``base``: lo hecho en una base no aparece en las otras.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

BASES = ("bloques", "partidas", "real")

#: Destino especial de capacidad prácticamente ilimitada (§5.9).
BASURA = "BASURA"
CAPACIDAD_BASURA = 999_999_999


class Base(DeclarativeBase):
    pass


class Ajuste(Base):
    """Ajustes generales (clave → valor en texto), p. ej. los años del calendario."""

    __tablename__ = "ajuste"

    clave: Mapped[str] = mapped_column(String(64), primary_key=True)
    valor: Mapped[str] = mapped_column(Text)


class Invernadero(Base):
    __tablename__ = "invernadero"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(32), unique=True)
    sala: Mapped[str | None] = mapped_column(String(32))
    #: Suma de las capacidades de sus calles (§8).
    capacidad: Mapped[int] = mapped_column(Integer)
    #: BASURA: no cuenta como exceso ni se filtra por sala.
    especial: Mapped[bool] = mapped_column(Boolean, default=False)
    orden: Mapped[int] = mapped_column(Integer, default=0)

    calles: Mapped[list[Calle]] = relationship(
        back_populates="invernadero", order_by="Calle.numero", cascade="all, delete-orphan"
    )


class Calle(Base):
    __tablename__ = "calle"
    __table_args__ = (UniqueConstraint("invernadero_id", "numero"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    invernadero_id: Mapped[int] = mapped_column(ForeignKey("invernadero.id"))
    numero: Mapped[int] = mapped_column(Integer)
    zona: Mapped[str] = mapped_column(String(16))  # Arriba | Abajo | Completo
    capacidad: Mapped[int] = mapped_column(Integer)
    #: Salas 3–5: se conoce la capacidad del invernadero pero no su reparto por calle.
    estimada: Mapped[bool] = mapped_column(Boolean, default=False)

    invernadero: Mapped[Invernadero] = relationship(back_populates="calles")


class Partida(Base):
    """Maestro de partidas (previsión). Se actualiza por código al importar los Excel."""

    __tablename__ = "partida"

    codigo: Mapped[str] = mapped_column(String(32), primary_key=True)
    tipo_planta: Mapped[str] = mapped_column(String(16), index=True)
    cliente: Mapped[str] = mapped_column(String(200), default="")
    variedad: Mapped[str] = mapped_column(String(200), default="")
    categoria: Mapped[str] = mapped_column(String(32))
    cultivo: Mapped[str] = mapped_column(String(32))
    medida: Mapped[str] = mapped_column(String(16), default="")
    bio: Mapped[str] = mapped_column(String(16), default="")
    bandejas: Mapped[int] = mapped_column(Integer)
    entrada: Mapped[date] = mapped_column(Date, index=True)
    liberacion: Mapped[date] = mapped_column(Date)  # calculada: F Entrega + 3
    siembra: Mapped[date | None] = mapped_column(Date)
    siembra_real: Mapped[date | None] = mapped_column(Date)
    entrega: Mapped[date] = mapped_column(Date)
    formato: Mapped[str] = mapped_column(String(32), default="")
    estilo: Mapped[str] = mapped_column(String(16), default="Normal")
    etapa: Mapped[str] = mapped_column(String(16), default="Normal")
    tipo_siembra: Mapped[str] = mapped_column(String(16), default="")
    observaciones: Mapped[str] = mapped_column(Text, default="")
    bandejas_excel: Mapped[float | None] = mapped_column(Float)
    plantas: Mapped[float | None] = mapped_column(Float)
    plantas_por_bandeja: Mapped[float | None] = mapped_column(Float)
    regla_bandejas: Mapped[str] = mapped_column(String(120), default="")
    planta_desarrollada: Mapped[bool] = mapped_column(Boolean, default=False)
    archivo: Mapped[str] = mapped_column(String(200), default="")
    actualizada_en: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class FechaTecnico(Base):
    """Liberación puesta a mano; manda sobre la calculada en las tres bases (§5.4)."""

    __tablename__ = "fecha_tecnico"

    codigo: Mapped[str] = mapped_column(String(32), primary_key=True)
    fecha: Mapped[date] = mapped_column(Date)


class DatoReal(Base):
    """Datos reales de una partida (§4.6). La lista de partidas de Real sale solo de aquí."""

    __tablename__ = "dato_real"

    codigo: Mapped[str] = mapped_column(String(32), primary_key=True)
    bandejas_real: Mapped[int] = mapped_column(Integer)
    fec_entrada_real: Mapped[date | None] = mapped_column(Date)
    fecha_entrega_pdf: Mapped[date | None] = mapped_column(Date)
    fecha_salida_real: Mapped[date | None] = mapped_column(Date)
    fecha_liberacion_ext: Mapped[date | None] = mapped_column(Date)
    liberacion_estimada: Mapped[bool] = mapped_column(Boolean, default=False)
    localizacion: Mapped[str] = mapped_column(Text, default="")
    observaciones: Mapped[str] = mapped_column(Text, default="")
    tipo_planta: Mapped[str] = mapped_column(String(16), default="")
    cliente: Mapped[str] = mapped_column(String(200), default="")
    variedad: Mapped[str] = mapped_column(String(200), default="")
    categoria: Mapped[str] = mapped_column(String(32), default="")
    cultivo: Mapped[str] = mapped_column(String(32), default="")
    bio: Mapped[str] = mapped_column(String(16), default="")
    estilo: Mapped[str] = mapped_column(String(16), default="")
    formato: Mapped[str] = mapped_column(String(32), default="")
    #: PDF_TUNELES, PDF_CARGAS, EXTENSIONES, EXTENSIONCALLES o MANUAL.
    fuente: Mapped[str] = mapped_column(String(32), default="MANUAL")


class Asignacion(Base):
    """Un trozo de una partida en un invernadero (y, si se quiere, en una calle) (§4.3).

    ``codigo`` no es clave ajena: una asignación puede quedarse «huérfana» si la
    partida desaparece del maestro (§10.4).
    """

    __tablename__ = "asignacion"

    id: Mapped[int] = mapped_column(primary_key=True)
    base: Mapped[str] = mapped_column(String(16), index=True)  # bloques | partidas | real
    semana: Mapped[int] = mapped_column(Integer, index=True)  # semana de entrada
    codigo: Mapped[str] = mapped_column(String(32), index=True)
    invernadero_id: Mapped[int] = mapped_column(ForeignKey("invernadero.id"), index=True)
    calle_id: Mapped[int | None] = mapped_column(ForeignKey("calle.id"))
    bandejas: Mapped[int] = mapped_column(Integer)
    entrada: Mapped[date | None] = mapped_column(Date)
    liberacion: Mapped[date | None] = mapped_column(Date)
    # Copia de la partida para agrupar por bloque sin volver al maestro.
    tipo_planta: Mapped[str] = mapped_column(String(16), default="injerto")
    cultivo: Mapped[str] = mapped_column(String(32), default="")
    categoria: Mapped[str] = mapped_column(String(32), default="")
    bio: Mapped[str] = mapped_column(String(16), default="")
    cliente: Mapped[str] = mapped_column(String(200), default="")
    creada_en: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    invernadero: Mapped[Invernadero] = relationship()
    calle: Mapped[Calle | None] = relationship()
