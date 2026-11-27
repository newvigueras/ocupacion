"""Lo que muestra cada pantalla, calculado sobre el ``Planificador`` (sin HTML)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select

from ..bd.planificacion import SEPARAR_POR_CLIENTE, Planificador
from ..bd.tablas import Asignacion, DatoReal, Invernadero, Partida

NOMBRE_BASE = {"bloques": "Bloques", "partidas": "Partidas", "real": "Real"}
NOMBRE_TIPO = {"injerto": "Injerto", "normal": "Planta normal", "componente": "Componentes"}


def miles(n: float | int | None) -> str:
    if n is None:
        return ""
    return f"{n:,.0f}".replace(",", ".")


def pct_clase(pct: float) -> str:
    """Color de la barra: verde, ámbar, rojo (como ``pctColor`` del HTML)."""
    if pct > 100:
        return "mal"
    if pct >= 85:
        return "aviso"
    return "bien"


def dia_por_defecto(plan: Planificador) -> date:
    cal = plan.calendario
    return min(max(date.today(), cal.primer_dia), cal.ultimo_dia)


def semana_por_defecto(plan: Planificador) -> int:
    return plan.calendario.semana_de(dia_por_defecto(plan)) or 1


# --- Resumen ------------------------------------------------------------------------

@dataclass
class FilaInv:
    nombre: str
    capacidad: int
    ocupado: int
    especial: bool = False

    @property
    def pct(self) -> float:
        return 100 * self.ocupado / self.capacidad if self.capacidad and not self.especial else 0.0

    @property
    def clase(self) -> str:
        return pct_clase(self.pct)


@dataclass
class Sala:
    nombre: str
    invernaderos: list[FilaInv] = field(default_factory=list)

    @property
    def capacidad(self) -> int:
        return sum(i.capacidad for i in self.invernaderos if not i.especial)

    @property
    def ocupado(self) -> int:
        return sum(i.ocupado for i in self.invernaderos if not i.especial)

    @property
    def pct(self) -> float:
        return 100 * self.ocupado / self.capacidad if self.capacidad else 0.0

    @property
    def clase(self) -> str:
        return pct_clase(self.pct)

    @property
    def clave(self) -> str:
        return self.nombre.lower().replace(" ", "")


def resumen(plan: Planificador, base: str, dia: date, ext: bool = True) -> list[Sala]:
    i = (dia - plan.calendario.primer_dia).days
    occ = plan.ocupacion(base, ext)
    salas: dict[str, Sala] = {}
    for inv in plan.invernaderos():
        nombre = inv.sala or "Otros destinos"
        fila = FilaInv(inv.nombre, inv.capacidad, occ[inv.id][i] if inv.id in occ else 0,
                       inv.especial)
        if inv.especial and not fila.ocupado:
            continue
        salas.setdefault(nombre, Sala(nombre)).invernaderos.append(fila)
    return list(salas.values())


@dataclass
class FilaCalle:
    numero: int
    zona: str
    capacidad: int
    estimada: bool
    ocupado: int = 0
    partidas: list[tuple[str, int]] = field(default_factory=list)

    @property
    def pct(self) -> float:
        return 100 * self.ocupado / self.capacidad if self.capacidad else 0.0

    @property
    def clase(self) -> str:
        return pct_clase(self.pct)


def calles_de(plan: Planificador, base: str, nombre: str, dia: date) -> tuple[Invernadero, list[FilaCalle], int]:
    """Calles de un invernadero ese día (con lo ya colocado en Real, que manda) y lo que
    ``base`` tiene en él sin calle."""
    inv = plan.invernadero(nombre)
    filas = {c.id: FilaCalle(c.numero, c.zona, c.capacidad, c.estimada) for c in inv.calles}
    for a in plan.asignaciones_en_calles(base, inv):
        if a.calle_id in filas and a.entrada and a.liberacion and a.entrada <= dia <= a.liberacion:
            f = filas[a.calle_id]
            f.ocupado += a.bandejas
            f.partidas.append((a.codigo, a.bandejas))
    q = select(func.coalesce(func.sum(Asignacion.bandejas), 0)).where(
        Asignacion.base == base, Asignacion.invernadero_id == inv.id,
        Asignacion.calle_id.is_(None), Asignacion.entrada <= dia, Asignacion.liberacion >= dia)
    return inv, list(filas.values()), plan.s.scalar(q)


def movimientos_del_dia(plan: Planificador, dia: date) -> tuple[list[Partida], list[DatoReal]]:
    """Partidas que entran ese día (maestro) y las que salen (salida real, PDF de cargas)."""
    entran = list(plan.s.scalars(select(Partida).where(Partida.entrada == dia)
                                 .order_by(Partida.cultivo, Partida.codigo)))
    salen = list(plan.s.scalars(select(DatoReal).where(DatoReal.fecha_salida_real == dia)
                                .order_by(DatoReal.codigo)))
    return entran, salen


# --- Rejillas de planificación ------------------------------------------------------

@dataclass
class Columna:
    nombre: str
    sala: str
    capacidad: int
    ocupado: int  # el último día de la semana, con lo ya colocado
    pico: int  # el máximo de la semana
    especial: bool

    @property
    def pct(self) -> float:
        return 100 * self.ocupado / self.capacidad if self.capacidad and not self.especial else 0.0

    @property
    def clase(self) -> str:
        return pct_clase(self.pct)

    @property
    def excedido(self) -> bool:
        return not self.especial and self.pico > self.capacidad


@dataclass
class Fila:
    clave: dict  # lo que se manda al editar una celda
    titulo: str
    etiquetas: list[tuple[str, str]]  # (texto, clase)
    total: int
    celdas: dict[str, int]  # invernadero → bandejas (sin calle en Partidas y Real)
    con_calle: dict[str, int] = field(default_factory=dict)
    detalle: str = ""
    tecnico: date | None = None
    entrada: date | None = None
    liberacion: date | None = None

    @property
    def asignado(self) -> int:
        return sum(self.celdas.values()) + sum(self.con_calle.values())

    @property
    def pendiente(self) -> int:
        return self.total - self.asignado


@dataclass
class Rejilla:
    base: str
    semana: int
    etiqueta: str
    inicio: date
    fin: date
    tipo: str | None
    columnas: list[Columna]
    filas: list[Fila]

    @property
    def total(self) -> int:
        return sum(f.total for f in self.filas)

    @property
    def asignado(self) -> int:
        return sum(f.asignado for f in self.filas)

    @property
    def excedidos(self) -> list[Columna]:
        return [c for c in self.columnas if c.excedido]


def _bio_clase(bio: str) -> str:
    b = (bio or "").upper()
    if "ECO" in b:
        return "eco"
    return "bio" if b.startswith("BIO") else "nobio"


def _etiquetas(cultivo: str, categoria: str, bio: str) -> list[tuple[str, str]]:
    return [(cultivo, "cultivo"), (categoria, "cat-" + (categoria or "").lower()),
            (bio or "", _bio_clase(bio))]


def _columnas(plan: Planificador, base: str, semana: int, sala: str | None) -> list[Columna]:
    cal = plan.calendario
    sem = cal.semana(semana)
    occ = plan.ocupacion(base, incluir_ext=True)
    a = (sem.inicio - cal.primer_dia).days
    b = (sem.fin - cal.primer_dia).days
    cols = []
    for inv in plan.invernaderos():
        if sala and (inv.sala or "").lower().replace(" ", "") != sala:
            continue
        dias = occ.get(inv.id)
        cols.append(Columna(inv.nombre, inv.sala or "", inv.capacidad,
                            dias[b] if dias else 0, max(dias[a:b + 1]) if dias else 0,
                            inv.especial))
    return cols


def rejilla_bloques(plan: Planificador, semana: int, tipo: str, sala: str | None = None) -> Rejilla:
    sem = plan.calendario.semana(semana)
    columnas = _columnas(plan, "bloques", semana, sala)
    nombres = {i.id: i.nombre for i in plan.invernaderos()}
    puesto: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    q = select(Asignacion).where(Asignacion.base == "bloques", Asignacion.semana == semana,
                                 Asignacion.tipo_planta == tipo)
    for a in plan.s.scalars(q):
        cliente = a.cliente if a.categoria in SEPARAR_POR_CLIENTE else ""
        puesto[(a.cultivo, a.categoria, a.bio, cliente)][nombres[a.invernadero_id]] += a.bandejas
    filas = []
    for n, b in enumerate(plan.bloques_de_semana(semana, tipo), 1):
        cultivo, categoria, bio, cliente = b.clave
        filas.append(Fila(
            clave={"bloque": n, "cultivo": cultivo, "categoria": categoria, "bio": bio,
                   "cliente": cliente},
            titulo=f"{n}. {cultivo}" + (f" · {cliente}" if cliente else ""),
            etiquetas=_etiquetas(cultivo, categoria, bio), total=b.total,
            celdas=dict(puesto.get(b.clave, {})),
            detalle=f"{len(b.partidas)} partidas: " + ", ".join(p.codigo for p in b.partidas[:12])
            + (" …" if len(b.partidas) > 12 else ""),
        ))
    return Rejilla("bloques", semana, sem.etiqueta, sem.inicio, sem.fin, tipo, columnas, filas)


def _celdas_por_partida(plan: Planificador, base: str, codigos: list[str]):
    nombres = {i.id: i.nombre for i in plan.invernaderos()}
    sin: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    con: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for i in range(0, len(codigos), 500):
        q = select(Asignacion).where(Asignacion.base == base,
                                     Asignacion.codigo.in_(codigos[i:i + 500]))
        for a in plan.s.scalars(q):
            (con if a.calle_id else sin)[a.codigo][nombres[a.invernadero_id]] += a.bandejas
    return sin, con


def rejilla_partidas(plan: Planificador, semana: int, tipo: str | None,
                     sala: str | None = None) -> Rejilla:
    sem = plan.calendario.semana(semana)
    columnas = _columnas(plan, "partidas", semana, sala)
    partidas = sorted(plan.partidas_nuevas(semana, tipo), key=lambda p: (-p.bandejas, p.codigo))
    sin, con = _celdas_por_partida(plan, "partidas", [p.codigo for p in partidas])
    filas = [Fila(
        clave={"codigo": p.codigo}, titulo=p.codigo,
        etiquetas=_etiquetas(p.cultivo, p.categoria, p.bio), total=p.bandejas,
        celdas=dict(sin.get(p.codigo, {})), con_calle=dict(con.get(p.codigo, {})),
        detalle=f"{p.cliente} · {p.variedad}", tecnico=plan.fecha_tecnico(p.codigo),
        entrada=p.entrada, liberacion=plan.liberacion_efectiva(p),
    ) for p in partidas]
    return Rejilla("partidas", semana, sem.etiqueta, sem.inicio, sem.fin, tipo, columnas, filas)


def rejilla_real(plan: Planificador, semana: int, sala: str | None = None) -> Rejilla:
    sem = plan.calendario.semana(semana)
    columnas = _columnas(plan, "real", semana, sala)
    datos = sorted(plan.partidas_reales(semana), key=lambda d: (-d.bandejas_real, d.codigo))
    sin, con = _celdas_por_partida(plan, "real", [d.codigo for d in datos])
    filas = []
    for d in datos:
        entrada, liberacion = plan.fechas_reales(d)
        filas.append(Fila(
            clave={"codigo": d.codigo}, titulo=d.codigo,
            etiquetas=_etiquetas(d.cultivo, d.categoria, d.bio) + [(d.fuente, "fuente")],
            total=d.bandejas_real, celdas=dict(sin.get(d.codigo, {})),
            con_calle=dict(con.get(d.codigo, {})),
            detalle=f"{d.cliente} · {d.localizacion}" if d.localizacion else d.cliente,
            tecnico=plan.fecha_tecnico(d.codigo), entrada=entrada, liberacion=liberacion,
        ))
    return Rejilla("real", semana, sem.etiqueta, sem.inicio, sem.fin, None, columnas, filas)


def cuenta_bases(plan: Planificador) -> dict[str, tuple[int, int]]:
    filas = plan.s.execute(select(Asignacion.base, func.count(Asignacion.id),
                                  func.coalesce(func.sum(Asignacion.bandejas), 0))
                           .group_by(Asignacion.base)).all()
    res = {b: (0, 0) for b in NOMBRE_BASE}
    res.update({b: (n, s) for b, n, s in filas})
    return res


def salas(plan: Planificador) -> list[tuple[str, str]]:
    vistas = []
    for inv in plan.invernaderos():
        if inv.sala and (inv.sala, inv.sala.lower().replace(" ", "")) not in vistas:
            vistas.append((inv.sala, inv.sala.lower().replace(" ", "")))
    return vistas



# --- Reparto de una partida entre las calles de un invernadero ------------------------

@dataclass
class FilaReparto:
    numero: int
    zona: str
    capacidad: int
    estimada: bool
    ocupado: int  # máximo de las demás partidas durante la estancia de esta
    partidas: list[tuple[str, int]]
    esta: int  # bandejas de esta partida en la calle

    @property
    def libre(self) -> int:
        return max(0, self.capacidad - self.ocupado)

    @property
    def detalle(self) -> str:
        return "\n".join(f"{c}: {miles(b)}" for c, b in self.partidas) or "Vacía"


@dataclass
class Reparto:
    base: str
    codigo: str
    invernadero: str
    sala: str
    total: int  # bandejas de la partida
    entrada: date
    liberacion: date
    sin_calle: int  # en este invernadero, sin calle
    en_otros: int  # en otros invernaderos
    filas: list[FilaReparto]

    @property
    def en_calles(self) -> int:
        return sum(f.esta for f in self.filas)

    @property
    def aqui(self) -> int:
        return self.sin_calle + self.en_calles

    @property
    def maximo(self) -> int:
        """Lo máximo que puede tener en este invernadero (lo que no está en otros)."""
        return max(0, self.total - self.en_otros)

    @property
    def estimadas(self) -> bool:
        return any(f.estimada for f in self.filas)


def reparto_calles(plan: Planificador, base: str, codigo: str, nombre: str) -> Reparto:
    """Lo necesario para repartir una partida entre las calles de un invernadero."""
    inv = plan.invernadero(nombre)
    codigo = codigo.strip().upper()
    if base == "real":
        d = plan.dato_real(codigo)
        total = d.bandejas_real
        entrada, liberacion = plan.fechas_reales(d)
    else:
        p = plan.partida(codigo)
        total = p.bandejas
        entrada, liberacion = p.entrada, plan.liberacion_efectiva(p)
    entrada = entrada or plan.calendario.primer_dia
    liberacion = liberacion or plan.calendario.ultimo_dia
    numeros = {c.id: c.numero for c in inv.calles}
    esta: dict[int, int] = defaultdict(int)
    sin_calle = en_otros = 0
    for a in plan.s.scalars(select(Asignacion).where(Asignacion.base == base,
                                                     Asignacion.codigo == codigo)):
        if a.invernadero_id != inv.id:
            en_otros += a.bandejas
        elif a.calle_id in numeros:
            esta[numeros[a.calle_id]] += a.bandejas
        else:
            sin_calle += a.bandejas
    occ = plan.ocupacion_calles(base, inv.nombre, entrada, liberacion, excluir_codigo=codigo)
    filas = [FilaReparto(o.numero, o.zona, o.capacidad, o.estimada, o.pico, o.partidas,
                         esta.get(o.numero, 0)) for o in occ.values()]
    return Reparto(base, codigo, inv.nombre, inv.sala or "", total, entrada, liberacion,
                   sin_calle, en_otros, filas)
