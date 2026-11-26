"""Pantallas de consulta: Por invernadero (gráfico), Por semana y Ocupación Hotel (Gantt).

Todo sale de las mismas cuentas que los % del resto de pantallas (``Planificador.tramos``):
en Bloques y Partidas cuenta también lo ya colocado en Real (§10.3 3b).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from sqlalchemy import select

from ..bd.planificacion import Planificador, _orden_cultivo
from ..bd.tablas import Asignacion, DatoReal, Partida
from ..motor.calendario import Semana
from .datos import FilaInv, _etiquetas, miles, pct_clase, semana_por_defecto

#: Color de cada categoría (los del §14, como las etiquetas de las rejillas).
COLOR_CATEGORIA = {
    "Jiffy": "#3D6FA8", "Normal": "#B0405A", "Pinzado": "#7B5AB0", "Hidro": "#1F9E82",
    "Cotiledón": "#D19A2E", "Patrón": "#8A5A2B", "Variedad": "#2E7D9A",
}


def clave_sala(nombre: str | None) -> str:
    return (nombre or "").lower().replace(" ", "")


# --- Trozos con su detalle ----------------------------------------------------------

@dataclass
class Trozo:
    """Lo que ocupa un invernadero entre dos fechas (una asignación, o lo que queda de ella)."""

    invernadero: str
    codigo: str
    cultivo: str
    categoria: str
    bio: str
    cliente: str
    entrada: date
    liberacion: date
    bandejas: int
    real: bool  # viene de Real (lo ya colocado) estando en otra base

    @property
    def color(self) -> str:
        return COLOR_CATEGORIA.get(self.categoria, "#7A8A80")

    @property
    def con_bio(self) -> bool:
        b = (self.bio or "").upper()
        return "ECO" in b or b.startswith("BIO")


def trozos(plan: Planificador, base: str) -> list[Trozo]:
    """Lo que cuenta para la ocupación de ``base``, con categoría, ECO/BIO y cliente."""
    nombres = {i.id: i.nombre for i in plan.invernaderos()}
    asig = {a.id: a for a in plan.s.scalars(
        select(Asignacion).where(Asignacion.base.in_({base, "real"})))}
    res = []
    for t in plan.tramos(base, incluir_ext=True):
        a = asig.get(t.id)
        res.append(Trozo(
            nombres.get(t.invernadero_id, "?"), t.codigo, t.cultivo,
            a.categoria if a else "", a.bio if a else "", a.cliente if a else "",
            t.entrada, t.liberacion, t.bandejas,
            real=base != "real" and a is not None and a.base == "real"))
    return res


def juntar_calles(lista: list[Trozo]) -> list[Trozo]:
    """Un trozo por partida e invernadero: las calles de una misma partida van juntas."""
    juntos: dict[tuple, Trozo] = {}
    for t in lista:
        k = (t.invernadero, t.codigo, t.real, t.entrada, t.liberacion)
        if k in juntos:
            juntos[k].bandejas += t.bandejas
        else:
            juntos[k] = replace(t)
    return list(juntos.values())


# --- Periodo (Por invernadero y Hotel) ----------------------------------------------

@dataclass
class Periodo:
    desde: Semana
    hasta: Semana
    a: int  # primer día, como índice del calendario
    b: int  # último día

    @property
    def inicio(self) -> date:
        return self.desde.inicio

    @property
    def fin(self) -> date:
        return self.hasta.fin

    @property
    def n_dias(self) -> int:
        return self.b - self.a + 1

    def dia(self, i: int) -> date:
        return self.inicio + timedelta(days=i)

    def contiene(self, dia: date) -> bool:
        return self.inicio <= dia <= self.fin


SEMANAS_POR_DEFECTO = 20


def periodo(plan: Planificador, desde: int | None = None, hasta: int | None = None) -> Periodo:
    """Por defecto: desde dos semanas antes de la actual, 20 semanas."""
    cal = plan.calendario
    d = desde or max(1, semana_por_defecto(plan) - 2)
    d = min(max(d, 1), cal.n_semanas)
    h = hasta or d + SEMANAS_POR_DEFECTO - 1
    h = min(max(h, d), cal.n_semanas)
    sd, sh = cal.semana(d), cal.semana(h)
    return Periodo(sd, sh, (sd.inicio - cal.primer_dia).days, (sh.fin - cal.primer_dia).days)


def semanas_del_periodo(plan: Planificador, p: Periodo) -> list[Semana]:
    return [plan.calendario.semana(n) for n in range(p.desde.numero, p.hasta.numero + 1)]


def _marcas(plan: Planificador, p: Periodo, x, maximo: int) -> list[tuple[float, str]]:
    """Raya al empezar cada semana, con su fecha (sin amontonarse si hay muchas)."""
    semanas = semanas_del_periodo(plan, p)
    paso = max(1, -(-len(semanas) // maximo))
    return [(round(x((s.inicio - p.inicio).days), 1), f"{s.inicio:%d/%m}")
            for n, s in enumerate(semanas) if n % paso == 0]


# --- Por invernadero ----------------------------------------------------------------

@dataclass
class SemanaInv:
    semana: Semana
    bandejas: int  # el último día de la semana
    capacidad: int
    contenido: list[tuple[str, int]]  # cultivo → bandejas ese día

    @property
    def pct(self) -> float:
        return 100 * self.bandejas / self.capacidad if self.capacidad else 0.0

    @property
    def clase(self) -> str:
        return pct_clase(self.pct)


@dataclass
class Grafico:
    nombre: str
    sala: str
    capacidad: int
    especial: bool
    valores: list[int]  # ocupación de cada día del periodo
    inicio: date
    semanas: list[SemanaInv]
    # Dibujo (SVG)
    ancho: int = 0
    alto: int = 0
    linea: str = ""
    area: str = ""
    y_capacidad: float = -1.0
    marcas: list[tuple[float, str]] = field(default_factory=list)
    x_hoy: float | None = None
    abajo: float = 0.0

    @property
    def pico(self) -> int:
        return max(self.valores, default=0)

    @property
    def dia_pico(self) -> date:
        return self.inicio + timedelta(days=self.valores.index(self.pico))

    @property
    def pct_pico(self) -> float:
        return 100 * self.pico / self.capacidad if self.capacidad and not self.especial else 0.0

    @property
    def clase(self) -> str:
        return pct_clase(self.pct_pico)

    @property
    def con_datos(self) -> int:
        return sum(1 for s in self.semanas if s.bandejas)

    @property
    def valores_texto(self) -> str:
        return ",".join(map(str, self.valores))


ANCHO_GRAFICO, ALTO_GRAFICO = 900, 170
_IZQ, _DER, _ARRIBA, _ABAJO = 12, 12, 18, 22


def _dibujar(plan: Planificador, g: Grafico, p: Periodo, hoy: date) -> None:
    n = len(g.valores)
    ancho_util = ANCHO_GRAFICO - _IZQ - _DER
    alto_util = ALTO_GRAFICO - _ARRIBA - _ABAJO
    tope = max(0 if g.especial else g.capacidad, g.pico, 1) * 1.08

    def x(i: float) -> float:
        return _IZQ + (i / n) * ancho_util

    def y(v: float) -> float:
        return _ARRIBA + alto_util - (v / tope) * alto_util

    # Escalones: la ocupación cambia de un día para otro.
    previo = g.valores[0]
    trazo = [f"M{x(0):.1f},{y(previo):.1f}"]
    for i, v in enumerate(g.valores[1:], 1):
        if v != previo:
            trazo.append(f"H{x(i):.1f}V{y(v):.1f}")
            previo = v
    trazo.append(f"H{x(n):.1f}")
    g.ancho, g.alto, g.abajo = ANCHO_GRAFICO, ALTO_GRAFICO, round(y(0), 1)
    g.linea = "".join(trazo)
    g.area = g.linea + f"V{y(0):.1f}H{x(0):.1f}Z"
    if g.capacidad and not g.especial:
        g.y_capacidad = round(y(g.capacidad), 1)
    g.marcas = _marcas(plan, p, x, maximo=14)
    if p.contiene(hoy):
        g.x_hoy = round(x((hoy - p.inicio).days + 0.5), 1)


def graficos(plan: Planificador, base: str, p: Periodo, sala: str | None = None,
             hoy: date | None = None) -> list[Grafico]:
    """Ocupación día a día de cada invernadero en el periodo y su contenido cada semana."""
    hoy = hoy or date.today()
    occ = plan.ocupacion(base, incluir_ext=True)
    semanas = semanas_del_periodo(plan, p)
    contenido: dict[str, dict[int, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int)))
    for t in trozos(plan, base):
        if t.entrada > p.fin or t.liberacion < p.inicio:
            continue
        for s in semanas:
            if t.entrada <= s.fin <= t.liberacion:
                contenido[t.invernadero][s.numero][t.cultivo or "Otros"] += t.bandejas
    res = []
    for inv in plan.invernaderos():
        if sala and clave_sala(inv.sala) != sala:
            continue
        dias = occ.get(inv.id)
        valores = dias[p.a:p.b + 1] if dias else [0] * p.n_dias
        if inv.especial and not any(valores):
            continue
        filas = [SemanaInv(s, valores[(s.fin - p.inicio).days],
                           0 if inv.especial else inv.capacidad,
                           sorted(contenido[inv.nombre][s.numero].items(), key=lambda kv: -kv[1]))
                 for s in semanas]
        g = Grafico(inv.nombre, inv.sala or "Otros destinos", inv.capacidad, inv.especial,
                    valores, p.inicio, filas)
        _dibujar(plan, g, p, hoy)
        res.append(g)
    return res


# --- Por semana ---------------------------------------------------------------------

@dataclass
class FilaSemana:
    invernadero: str
    codigo: str
    cliente: str
    variedad: str
    cultivo: str
    categoria: str
    bio: str
    bandejas: int
    entrada: date
    liberacion: date
    nueva: bool  # entra esta semana
    sale: bool  # se libera esta semana
    real: bool

    @property
    def etiquetas(self) -> list[tuple[str, str]]:
        return _etiquetas(self.cultivo, self.categoria, self.bio)


@dataclass
class Grupo:
    nombre: str
    filas: list[FilaSemana]

    @property
    def bandejas(self) -> int:
        return sum(f.bandejas for f in self.filas)

    @property
    def partidas(self) -> int:
        return len({f.codigo for f in self.filas})


@dataclass
class VistaSemana:
    semana: Semana
    filas: list[FilaSemana]
    chips: list[FilaInv]  # % de cada invernadero ocupado el último día de la semana
    grupos: list[Grupo]

    @property
    def partidas(self) -> int:
        return len({f.codigo for f in self.filas})

    @property
    def nuevas(self) -> int:
        return len({f.codigo for f in self.filas if f.nueva})

    @property
    def salen(self) -> int:
        return len({f.codigo for f in self.filas if f.sale})


def _clientes_y_variedades(plan: Planificador, codigos: list[str]) -> dict[str, tuple[str, str]]:
    """Código → (cliente, variedad): del maestro y, si no está, de Real."""
    res: dict[str, tuple[str, str]] = {}
    for modelo in (DatoReal, Partida):  # el maestro, el último: manda
        for i in range(0, len(codigos), 500):
            q = select(modelo).where(modelo.codigo.in_(codigos[i:i + 500]))
            for x in plan.s.scalars(q):
                res[x.codigo] = (x.cliente, x.variedad)
    return res


def vista_semana(plan: Planificador, base: str, semana: int, agrupar: str = "inv",
                 inv: str | None = None) -> VistaSemana:
    sem = plan.calendario.semana(semana)
    occ = plan.ocupacion(base, incluir_ext=True)
    b = (sem.fin - plan.calendario.primer_dia).days
    chips = [FilaInv(i.nombre, i.capacidad, occ[i.id][b], i.especial)
             for i in plan.invernaderos() if i.id in occ and occ[i.id][b]]

    de_la_semana = [t for t in trozos(plan, base)
                    if t.entrada <= sem.fin and t.liberacion >= sem.inicio
                    and (not inv or t.invernadero == inv)]
    juntos = juntar_calles(de_la_semana)
    detalle = _clientes_y_variedades(plan, sorted({t.codigo for t in juntos}))
    filas = []
    for t in juntos:
        cliente, variedad = detalle.get(t.codigo, (t.cliente, ""))
        filas.append(FilaSemana(
            t.invernadero, t.codigo, cliente or t.cliente, variedad, t.cultivo, t.categoria,
            t.bio, t.bandejas, t.entrada, t.liberacion,
            nueva=sem.inicio <= t.entrada <= sem.fin,
            sale=sem.inicio <= t.liberacion <= sem.fin, real=t.real))

    if agrupar == "cultivo":
        def clave(f: FilaSemana) -> str:
            return f.cultivo or "Otros"

        def orden(k: str):
            return (_orden_cultivo(k), k)
    else:
        posicion = {i.nombre: n for n, i in enumerate(plan.invernaderos())}

        def clave(f: FilaSemana) -> str:
            return f.invernadero

        def orden(k: str):
            return (posicion.get(k, 999), k)
    por_grupo: dict[str, list[FilaSemana]] = defaultdict(list)
    for f in filas:
        por_grupo[clave(f)].append(f)
    grupos = [Grupo(k, sorted(por_grupo[k], key=lambda f: (f.entrada, f.codigo, f.invernadero)))
              for k in sorted(por_grupo, key=orden)]
    return VistaSemana(sem, filas, chips, grupos)


# --- Ocupación Hotel (Gantt) --------------------------------------------------------

@dataclass
class Barra:
    x: float
    y: float
    ancho: float
    alto: float
    color: str
    opacidad: float
    texto: str  # lo que sale al pasar el ratón
    rotulo: str = ""  # código dentro de la barra, si cabe


@dataclass
class FilaGantt:
    nombre: str
    y: float
    alto: float
    capacidad: int
    especial: bool
    valores: list[int]  # ocupación de cada día del periodo
    y_capacidad: float | None = None

    @property
    def valores_texto(self) -> str:
        return ",".join(map(str, self.valores))

    def chip(self, i: int) -> FilaInv:
        return FilaInv(self.nombre, self.capacidad, self.valores[i], self.especial)


@dataclass
class Gantt:
    periodo: Periodo
    ancho: int
    alto: int
    izquierda: int
    arriba: int
    ancho_dia: float
    filas: list[FilaGantt]
    barras: list[Barra]
    marcas: list[tuple[float, str]] = field(default_factory=list)
    x_hoy: float | None = None

    def x_dia(self, i: int) -> float:
        return round(self.izquierda + (i + 0.5) * self.ancho_dia, 1)


ALTO_FILA, ALTO_CARRIL_UNO = 46, 16


def _apilar(lista: list[Trozo]) -> tuple[list[tuple[Trozo, int]], int]:
    """Cada trozo tan abajo como quepa, con un alto igual a sus bandejas: la fila se llena
    como se llena el invernadero. Devuelve (trozo, bandejas por debajo) y la altura total."""
    puestos: list[tuple[int, int, date, date]] = []  # (desde, hasta, entrada, liberación)
    res = []
    for t in sorted(lista, key=lambda t: (t.entrada, -t.bandejas, t.codigo)):
        a_la_vez = sorted((d, h) for d, h, e, l in puestos
                          if e <= t.liberacion and l >= t.entrada)
        y = 0
        for d, h in a_la_vez:  # el primer hueco donde cabe
            if d >= y + t.bandejas:
                break
            y = max(y, h)
        puestos.append((y, y + t.bandejas, t.entrada, t.liberacion))
        res.append((t, y))
    return res, max((h for _, h, _, _ in puestos), default=0)


def _carriles(lista: list[Trozo]) -> tuple[list[tuple[Trozo, int]], int]:
    """Cada trozo en el primer carril libre, para que no se tapen unos a otros."""
    fines: list[date] = []
    res = []
    for t in sorted(lista, key=lambda t: (t.entrada, t.liberacion, t.codigo)):
        for n, fin in enumerate(fines):
            if fin < t.entrada:
                fines[n] = t.liberacion
                break
        else:
            n = len(fines)
            fines.append(t.liberacion)
        res.append((t, n))
    return res, max(len(fines), 1)


def gantt(plan: Planificador, base: str, p: Periodo, sala: str | None = None,
          inv: str | None = None, hoy: date | None = None) -> Gantt:
    hoy = hoy or date.today()
    occ = plan.ocupacion(base, incluir_ext=True)
    invs = [i for i in plan.invernaderos()
            if (not sala or clave_sala(i.sala) == sala) and (not inv or i.nombre == inv)]
    nombres = {i.nombre for i in invs}
    por_inv: dict[str, list[Trozo]] = defaultdict(list)
    for t in juntar_calles([t for t in trozos(plan, base) if t.invernadero in nombres
                            and t.entrada <= p.fin and t.liberacion >= p.inicio]):
        por_inv[t.invernadero].append(t)

    izquierda, arriba = 74, 24
    ancho_dia = max(3.0, min(14.0, 1100 / p.n_dias))
    uno = len(invs) == 1
    filas, barras, y = [], [], float(arriba)
    for i in invs:
        dias = occ.get(i.id)
        valores = dias[p.a:p.b + 1] if dias else [0] * p.n_dias
        if i.especial and not any(valores):
            continue
        lista = por_inv.get(i.nombre, [])
        if uno:  # un solo invernadero: cada partida en su carril, con su código
            carriles, n_carriles = _carriles(lista)
            alto = max(ALTO_FILA, n_carriles * ALTO_CARRIL_UNO + 8)
            colocados = [(t, 4 + n * ALTO_CARRIL_UNO, ALTO_CARRIL_UNO - 1) for t, n in carriles]
        else:  # todos: alto según las bandejas, sobre la capacidad del invernadero
            alto = ALTO_FILA
            apilados, tope = _apilar(lista)
            escala = (alto - 6) / max(tope, 0 if i.especial else i.capacidad, 1)
            colocados = [(t, alto - 2 - (debajo + t.bandejas) * escala, t.bandejas * escala)
                         for t, debajo in apilados]
        for t, arriba_barra, alto_barra in colocados:
            desde, hasta = max(t.entrada, p.inicio), min(t.liberacion, p.fin)
            x = izquierda + (desde - p.inicio).days * ancho_dia
            ancho = ((hasta - desde).days + 1) * ancho_dia - 1
            texto = (f"{t.codigo} · {t.cliente} · {t.cultivo} {t.categoria}"
                     + (f" ({t.bio})" if t.bio else "")
                     + f" · {miles(t.bandejas)} bandejas · {t.entrada:%d/%m/%Y} – "
                     f"{t.liberacion:%d/%m/%Y}" + (" · ya colocado (Real)" if t.real else ""))
            barras.append(Barra(round(x, 1), round(y + arriba_barra, 1),
                                round(max(ancho, 1.5), 1), round(max(alto_barra, 0.8), 1),
                                t.color, 0.55 if t.con_bio else 0.92, texto,
                                t.codigo if uno and ancho > 70 else ""))
        y_cap = None
        if not uno and not i.especial:  # raya de la capacidad
            y_cap = round(y + alto - 2 - i.capacidad * escala, 1)
        filas.append(FilaGantt(i.nombre, y, alto, i.capacidad, i.especial, valores, y_cap))
        y += alto
    g = Gantt(p, int(izquierda + p.n_dias * ancho_dia + 12), int(y + 6), izquierda, arriba,
              ancho_dia, filas, barras)
    g.marcas = _marcas(plan, p, lambda d: izquierda + d * ancho_dia, maximo=40)
    if p.contiene(hoy):
        g.x_hoy = g.x_dia((hoy - p.inicio).days)
    return g
