"""Asignaciones y ocupación sobre la base de datos (documento maestro §5, §10.1–10.3).

- **Bloques** (Planificación Global): se asigna un bloque entero a un invernadero y se
  reparte partida a partida (§10.2).
- **Partidas** (Planificación por Partida): se asigna cada partida, con o sin calle.
- **Real** (Planificación Exacta): igual que Partidas, pero solo con partidas que
  tienen dato real (tabla ``dato_real``).

Ocupación diaria de un invernadero = suma de las asignaciones con
``entrada ≤ día ≤ liberación``. En Bloques y Partidas se puede contar además lo ya
colocado (base Real), y lo asignado de una partida sustituye bandeja a bandeja a lo
que Real tiene de ella (§10.3, paso 3b).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..motor.calendario import Calendario
from ..motor.reglas import CULTIVOS
from .tablas import BASES, Ajuste, Asignacion, Calle, DatoReal, FechaTecnico, Invernadero, Partida

#: En Jiffy e hidro el bloque se separa también por cliente (§10.1).
SEPARAR_POR_CLIENTE = frozenset({"Jiffy", "Hidro"})


class ErrorPlan(ValueError):
    pass


@dataclass
class Bloque:
    cultivo: str
    categoria: str
    bio: str
    cliente: str  # '' salvo en Jiffy e Hidro
    partidas: list[Partida]

    @property
    def clave(self) -> tuple[str, str, str, str]:
        return (self.cultivo, self.categoria, self.bio, self.cliente)

    @property
    def total(self) -> int:
        return sum(p.bandejas for p in self.partidas)


@dataclass(frozen=True)
class Tramo:
    """Una asignación lista para contar ocupación."""

    id: int | None
    invernadero_id: int
    codigo: str
    cultivo: str
    entrada: date
    liberacion: date
    bandejas: int


@dataclass(frozen=True)
class Exceso:
    invernadero: str
    capacidad: int
    maximo: int
    dia_maximo: date
    primer_dia: date


@dataclass(frozen=True)
class OcupacionCalle:
    numero: int
    zona: str
    capacidad: int
    estimada: bool
    pico: int  # máximo ocupado en el periodo pedido
    partidas: list[tuple[str, int]]

    @property
    def libre(self) -> int:
        return max(0, self.capacidad - self.pico)


@dataclass(frozen=True)
class Mezcla:
    invernadero: str
    primer_dia: date
    cultivos: tuple[str, ...]


def cultivo_para_mezcla(cultivo: str) -> str:
    """Jiffy 8x8 y 5x5 cuentan como el mismo cultivo (§5.8)."""
    return "Jiffy" if cultivo.startswith("Jiffy") else cultivo


def _orden_cultivo(cultivo: str) -> int:
    try:
        return CULTIVOS.index(cultivo)
    except ValueError:
        return len(CULTIVOS)


class Planificador:
    def __init__(self, s: Session):
        self.s = s
        inicio = s.get(Ajuste, "año_inicio")
        fin = s.get(Ajuste, "año_fin")
        if inicio is None:
            raise ErrorPlan("La base de datos no está preparada (falta el calendario)")
        self.calendario = Calendario(int(inicio.valor), int(fin.valor) if fin else None)

    # --- Consultas básicas ------------------------------------------------------

    def invernadero(self, nombre: str) -> Invernadero:
        clave = nombre.strip().upper().replace(" ", "").replace(".", "").replace("-", "")
        inv = self.s.scalar(select(Invernadero).where(Invernadero.nombre == clave))
        if inv is None:
            raise ErrorPlan(f"No existe el invernadero {nombre}")
        return inv

    def invernaderos(self) -> list[Invernadero]:
        return list(self.s.scalars(select(Invernadero).order_by(Invernadero.orden)))

    def calle(self, inv: Invernadero, numero: int) -> Calle:
        calle = self.s.scalar(
            select(Calle).where(Calle.invernadero_id == inv.id, Calle.numero == numero)
        )
        if calle is None:
            raise ErrorPlan(f"{inv.nombre} no tiene la calle {numero}")
        return calle

    def partida(self, codigo: str) -> Partida:
        p = self.s.get(Partida, codigo.strip().upper())
        if p is None:
            raise ErrorPlan(f"La partida {codigo} no está en el maestro")
        return p

    def dato_real(self, codigo: str) -> DatoReal:
        d = self.s.get(DatoReal, codigo.strip().upper())
        if d is None:
            raise ErrorPlan(f"La partida {codigo} no tiene dato real")
        return d

    # --- Fechas -----------------------------------------------------------------

    def fecha_tecnico(self, codigo: str) -> date | None:
        ft = self.s.get(FechaTecnico, codigo)
        return ft.fecha if ft else None

    def liberacion_efectiva(self, p: Partida) -> date:
        """La fecha técnico manda sobre F Entrega + 3 (§5.4)."""
        return self.fecha_tecnico(p.codigo) or p.liberacion

    def fechas_reales(self, d: DatoReal) -> tuple[date | None, date | None]:
        """Entrada y liberación de una partida en Real (§4.6)."""
        p = self.s.get(Partida, d.codigo)
        entrada = d.fec_entrada_real or (p.entrada if p else None)
        liberacion = (self.fecha_tecnico(d.codigo) or d.fecha_salida_real
                      or d.fecha_liberacion_ext or d.fecha_entrega_pdf
                      or (p.liberacion if p else None))
        return entrada, liberacion

    def poner_fecha_tecnico(self, codigo: str, fecha: date | None) -> int:
        """Pone (o quita, con ``None``) la fecha técnico y la aplica a lo ya asignado.

        Devuelve cuántas asignaciones han cambiado de liberación.
        """
        codigo = codigo.strip().upper()
        actual = self.s.get(FechaTecnico, codigo)
        if fecha is None:
            if actual is not None:
                self.s.delete(actual)
        elif actual is None:
            self.s.add(FechaTecnico(codigo=codigo, fecha=fecha))
        else:
            actual.fecha = fecha
        self.s.flush()

        p = self.s.get(Partida, codigo)
        d = self.s.get(DatoReal, codigo)
        nueva = {
            "bloques": self.liberacion_efectiva(p) if p else fecha,
            "partidas": self.liberacion_efectiva(p) if p else fecha,
            "real": self.fechas_reales(d)[1] if d else fecha,
        }
        cambiadas = 0
        for a in self.s.scalars(select(Asignacion).where(Asignacion.codigo == codigo)):
            if nueva[a.base] is not None and a.liberacion != nueva[a.base]:
                a.liberacion = nueva[a.base]
                cambiadas += 1
        self.s.flush()
        return cambiadas

    # --- Partidas y bloques de una semana ----------------------------------------

    def partidas_nuevas(self, semana: int, tipo_planta: str | None = None) -> list[Partida]:
        """Partidas que entran al invernadero esa semana."""
        sem = self.calendario.semana(semana)
        q = select(Partida).where(Partida.entrada >= sem.inicio, Partida.entrada <= sem.fin)
        if tipo_planta:
            q = q.where(Partida.tipo_planta == tipo_planta)
        partidas = list(self.s.scalars(q))
        partidas.sort(key=lambda p: (self.liberacion_efectiva(p), p.codigo))
        return partidas

    def bloques_de_semana(self, semana: int, tipo_planta: str = "injerto") -> list[Bloque]:
        """Bloques por cultivo + categoría + ECO/BIO (+ cliente en Jiffy e Hidro) (§10.1)."""
        grupos: dict[tuple, list[Partida]] = defaultdict(list)
        for p in self.partidas_nuevas(semana, tipo_planta):
            cliente = p.cliente if p.categoria in SEPARAR_POR_CLIENTE else ""
            grupos[(p.cultivo, p.categoria, p.bio, cliente)].append(p)
        bloques = [Bloque(*clave, partidas=ps) for clave, ps in grupos.items()]
        bloques.sort(key=lambda b: (_orden_cultivo(b.cultivo), -b.total, b.clave))
        return bloques

    def partidas_reales(self, semana: int | None = None) -> list[DatoReal]:
        """Partidas de la base Real (solo las que tienen dato real), por semana de entrada."""
        datos = list(self.s.scalars(select(DatoReal).order_by(DatoReal.codigo)))
        if semana is None:
            return datos
        return [d for d in datos
                if (e := self.fechas_reales(d)[0]) and self.calendario.semana_de(e) == semana]

    # --- Asignar ------------------------------------------------------------------

    def asignado(self, base: str, codigo: str) -> int:
        return self.s.scalar(
            select(func.coalesce(func.sum(Asignacion.bandejas), 0))
            .where(Asignacion.base == base, Asignacion.codigo == codigo)
        )

    def asignar_bloque(self, semana: int, tipo_planta: str, clave: tuple[str, str, str, str],
                       invernadero: str, bandejas: int) -> int:
        """Pone ``bandejas`` de un bloque en un invernadero (base Bloques, §10.2).

        Sustituye lo que hubiera en esa celda y reparte partida a partida en orden de
        liberación y código, sin pasar de lo que le queda libre a cada partida.
        Devuelve lo colocado.
        """
        inv = self.invernadero(invernadero)
        bloque = next((b for b in self.bloques_de_semana(semana, tipo_planta) if b.clave == clave),
                      None)
        if bloque is None:
            raise ErrorPlan(f"No hay bloque {' · '.join(filter(None, clave))} en la semana {semana}")

        cultivo, categoria, bio, cliente = clave
        celda = delete(Asignacion).where(
            Asignacion.base == "bloques", Asignacion.semana == semana,
            Asignacion.tipo_planta == tipo_planta, Asignacion.cultivo == cultivo,
            Asignacion.categoria == categoria, Asignacion.bio == bio,
            Asignacion.invernadero_id == inv.id,
        )
        if categoria in SEPARAR_POR_CLIENTE:
            celda = celda.where(Asignacion.cliente == cliente)
        self.s.execute(celda)
        return self._repartir_bloque(semana, bloque, inv, bandejas)

    def _repartir_bloque(self, semana: int, bloque: Bloque, inv: Invernadero, bandejas: int) -> int:
        """Reparte ``bandejas`` del bloque partida a partida (sin borrar nada antes)."""
        restante = max(0, int(bandejas))
        colocado = 0
        for p in bloque.partidas:
            if restante <= 0:
                break
            libre = p.bandejas - self.asignado("bloques", p.codigo)
            trozo = min(libre, restante)
            if trozo <= 0:
                continue
            self.s.add(self._nueva(base="bloques", semana=semana, p=p, inv=inv, calle=None,
                                   bandejas=trozo, entrada=p.entrada,
                                   liberacion=self.liberacion_efectiva(p)))
            self.s.flush()
            restante -= trozo
            colocado += trozo
        return colocado

    def asignar_partida(self, base: str, codigo: str, invernadero: str, bandejas: int,
                        calle: int | None = None) -> int:
        """Pone ``bandejas`` de una partida en un invernadero (y calle) (bases Partidas y Real).

        Sustituye lo que hubiera en esa celda y topa con las bandejas de la partida
        menos lo ya colocado en otras celdas. Devuelve lo colocado (0 = celda vacía).
        """
        if base not in ("partidas", "real"):
            raise ErrorPlan("Una partida suelta se asigna en la base Partidas o Real")
        inv = self.invernadero(invernadero)
        c = self.calle(inv, calle) if calle is not None else None
        codigo = codigo.strip().upper()
        if base == "real":
            self.dato_real(codigo)
        else:
            self.partida(codigo)

        self.s.execute(delete(Asignacion).where(
            Asignacion.base == base, Asignacion.codigo == codigo,
            Asignacion.invernadero_id == inv.id,
            Asignacion.calle_id.is_(None) if c is None else Asignacion.calle_id == c.id,
        ))
        return self._poner_partida(base, codigo, inv, c, bandejas)

    def _poner_partida(self, base: str, codigo: str, inv: Invernadero, c: Calle | None,
                       bandejas: int) -> int:
        """Coloca hasta ``bandejas`` de la partida (sin borrar nada antes); devuelve lo puesto."""
        if base == "real":
            d = self.dato_real(codigo)
            p = self.s.get(Partida, codigo)
            total = d.bandejas_real
            entrada, liberacion = self.fechas_reales(d)
            origen = d if not p else p
        else:
            p = self.partida(codigo)
            total = p.bandejas
            entrada, liberacion = p.entrada, self.liberacion_efectiva(p)
            origen = p
        libre = total - self.asignado(base, codigo)
        trozo = max(0, min(int(bandejas), libre))
        if trozo > 0:
            semana = self.calendario.semana_de(entrada) if entrada else None
            if semana is None:
                raise ErrorPlan(f"La entrada de {codigo} está fuera del calendario")
            self.s.add(self._nueva(base=base, semana=semana, p=origen, inv=inv, calle=c,
                                   bandejas=trozo, entrada=entrada, liberacion=liberacion))
        self.s.flush()
        return trozo

    def _nueva(self, *, base, semana, p, inv, calle, bandejas, entrada, liberacion) -> Asignacion:
        return Asignacion(
            base=base, semana=semana, codigo=p.codigo, invernadero_id=inv.id,
            calle_id=calle.id if calle else None, bandejas=bandejas,
            entrada=entrada, liberacion=liberacion,
            tipo_planta=p.tipo_planta or "injerto", cultivo=p.cultivo or "Otros",
            categoria=p.categoria or "", bio=p.bio or "", cliente=p.cliente or "",
        )

    # --- Calles -------------------------------------------------------------------

    def asignar_calles(self, base: str, codigo: str, invernadero: str,
                       reparto: dict[int, int]) -> tuple[dict[int, int], int]:
        """Reparte una partida entre las calles de un invernadero (bases Partidas y Real).

        ``reparto``: calle → bandejas. Sustituye todo lo que la partida tuviera en ese
        invernadero (con y sin calle). Lo que antes había en el invernadero y no se pone
        en calles se queda en él sin calle; lo que pase de ahí sale de las bandejas libres
        de la partida (topado). Devuelve (calle → bandejas puestas, bandejas sin calle).
        """
        if base not in ("partidas", "real"):
            raise ErrorPlan("Las calles se asignan en la base Partidas o Real")
        inv = self.invernadero(invernadero)
        if inv.especial:
            raise ErrorPlan(f"{inv.nombre} no tiene calles")
        codigo = codigo.strip().upper()
        if base == "real":
            self.dato_real(codigo)
        else:
            self.partida(codigo)
        calles = {c.numero: c for c in inv.calles}
        for n, v in reparto.items():
            if n not in calles:
                raise ErrorPlan(f"{inv.nombre} no tiene la calle {n}")
            if v < 0:
                raise ErrorPlan("Las bandejas no pueden ser negativas")

        donde = (Asignacion.base == base, Asignacion.codigo == codigo,
                 Asignacion.invernadero_id == inv.id)
        antes = self.s.scalar(select(func.coalesce(func.sum(Asignacion.bandejas), 0)).where(*donde))
        self.s.execute(delete(Asignacion).where(*donde))
        puestas: dict[int, int] = {}
        for n in sorted(reparto):
            if reparto[n] > 0:
                puesto = self._poner_partida(base, codigo, inv, calles[n], reparto[n])
                if puesto:
                    puestas[n] = puesto
        resto = antes - sum(puestas.values())
        sin_calle = self._poner_partida(base, codigo, inv, None, resto) if resto > 0 else 0
        return puestas, sin_calle

    def asignaciones_en_calles(self, base: str, inv: Invernadero) -> list[Asignacion]:
        """Lo que ocupa las calles de un invernadero en ``base``.

        En Bloques y Partidas cuenta también lo colocado en Real: lo de Real manda, y lo
        de la base solo cuenta en las partidas que no tienen nada en Real (§10.3 3b).
        """
        q = select(Asignacion).where(Asignacion.invernadero_id == inv.id,
                                     Asignacion.calle_id.is_not(None))
        propias = list(self.s.scalars(q.where(Asignacion.base == base)))
        if base == "real":
            return propias
        reales = list(self.s.scalars(q.where(Asignacion.base == "real")))
        en_real = set(self.s.scalars(select(Asignacion.codigo).where(Asignacion.base == "real")))
        return reales + [a for a in propias if a.codigo not in en_real]

    def ocupacion_calles(self, base: str, invernadero: str, desde: date, hasta: date,
                         excluir_codigo: str | None = None) -> dict[int, OcupacionCalle]:
        """Calle (número) → máximo ocupado entre ``desde`` y ``hasta`` y las partidas que hay."""
        inv = self.invernadero(invernadero)
        por_calle: dict[int, list[Asignacion]] = defaultdict(list)
        for a in self.asignaciones_en_calles(base, inv):
            if a.codigo == excluir_codigo:
                continue
            entrada = a.entrada or self.calendario.primer_dia
            liberacion = a.liberacion or self.calendario.ultimo_dia
            if entrada <= hasta and liberacion >= desde:
                por_calle[a.calle_id].append(a)
        res = {}
        for c in inv.calles:
            lista = por_calle.get(c.id, [])
            # El máximo siempre cae en el día en que entra alguna (o en ``desde``).
            puntos = {desde} | {max(a.entrada or desde, desde) for a in lista}
            pico = max((sum(a.bandejas for a in lista
                            if (a.entrada or desde) <= d <= (a.liberacion or hasta)) for d in puntos),
                       default=0)
            juntas: dict[str, int] = defaultdict(int)
            for a in lista:
                juntas[a.codigo] += a.bandejas
            res[c.numero] = OcupacionCalle(c.numero, c.zona, c.capacidad, c.estimada, pico,
                                           sorted(juntas.items()))
        return res

    def vaciar_base(self, base: str) -> int:
        if base not in BASES:
            raise ErrorPlan(f"Base desconocida: {base}")
        return self.s.execute(delete(Asignacion).where(Asignacion.base == base)).rowcount

    # --- Ocupación ----------------------------------------------------------------

    def tramos(self, base: str, incluir_ext: bool = True,
               excluir_ids: Iterable[int] = ()) -> list[Tramo]:
        """Asignaciones que cuentan para la ocupación de ``base``."""
        if base not in BASES:
            raise ErrorPlan(f"Base desconocida: {base}")
        excluir = set(excluir_ids)
        propias = [self._tramo(a) for a in self._asignaciones(base) if a.id not in excluir]
        if base == "real" or not incluir_ext:
            return propias
        reales = [self._tramo(a) for a in self._asignaciones("real")]
        return propias + self._sustituir(propias, reales)

    def _asignaciones(self, base: str) -> list[Asignacion]:
        return list(self.s.scalars(select(Asignacion).where(Asignacion.base == base)))

    def _tramo(self, a: Asignacion) -> Tramo:
        sem = self.calendario.semana(a.semana) if 1 <= a.semana <= self.calendario.n_semanas else None
        entrada = a.entrada or (sem.inicio if sem else self.calendario.primer_dia)
        liberacion = a.liberacion or (sem.fin if sem else self.calendario.ultimo_dia)
        return Tramo(a.id, a.invernadero_id, a.codigo, a.cultivo, entrada, liberacion, a.bandejas)

    @staticmethod
    def _sustituir(propias: list[Tramo], reales: list[Tramo]) -> list[Tramo]:
        """Lo asignado de una partida resta, bandeja a bandeja, lo que Real tiene de ella:
        primero en el mismo invernadero y, si sobra, en los demás (§10.3 3b)."""
        por_codigo: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        for t in propias:
            por_codigo[t.codigo][t.invernadero_id] += t.bandejas
        restos = [[t, t.bandejas] for t in reales if t.bandejas > 0]
        pendiente_total = {c: sum(v.values()) for c, v in por_codigo.items()}
        for r in restos:  # 1. mismo invernadero
            mismo = por_codigo.get(r[0].codigo, {})
            quita = min(r[1], mismo.get(r[0].invernadero_id, 0))
            if quita:
                r[1] -= quita
                mismo[r[0].invernadero_id] -= quita
                pendiente_total[r[0].codigo] -= quita
        for r in restos:  # 2. el resto, de los demás invernaderos
            quita = min(r[1], pendiente_total.get(r[0].codigo, 0))
            if quita:
                r[1] -= quita
                pendiente_total[r[0].codigo] -= quita
        return [Tramo(t.id, t.invernadero_id, t.codigo, t.cultivo, t.entrada, t.liberacion, n)
                for t, n in restos if n > 0]

    def ocupacion(self, base: str, incluir_ext: bool = True,
                  excluir_ids: Iterable[int] = ()) -> dict[int, list[int]]:
        """Invernadero (id) → bandejas ocupadas cada día del calendario."""
        cal = self.calendario
        n = cal.n_dias
        dif: dict[int, list[int]] = defaultdict(lambda: [0] * (n + 1))
        for t in self.tramos(base, incluir_ext, excluir_ids):
            desde = max(t.entrada, cal.primer_dia)
            hasta = min(t.liberacion, cal.ultimo_dia)
            if desde > hasta:
                continue
            fila = dif[t.invernadero_id]
            fila[(desde - cal.primer_dia).days] += t.bandejas
            fila[(hasta - cal.primer_dia).days + 1] -= t.bandejas
        resultado = {}
        for inv_id, fila in dif.items():
            acumulado, dias = 0, []
            for i in range(n):
                acumulado += fila[i]
                dias.append(acumulado)
            resultado[inv_id] = dias
        return resultado

    def ocupacion_dia(self, base: str, dia: date, incluir_ext: bool = True) -> dict[str, int]:
        """Invernadero (nombre) → bandejas ocupadas ese día."""
        if not self.calendario.contiene(dia):
            raise ErrorPlan(f"{dia:%d/%m/%Y} está fuera del calendario")
        i = (dia - self.calendario.primer_dia).days
        occ = self.ocupacion(base, incluir_ext)
        return {inv.nombre: occ[inv.id][i] if inv.id in occ else 0 for inv in self.invernaderos()}

    def hueco(self, base: str, invernadero: str, desde: date, hasta: date,
              incluir_ext: bool = True, excluir_ids: Iterable[int] = ()) -> int:
        """Bandejas libres en el peor día entre ``desde`` y ``hasta`` (ambos incluidos)."""
        inv = self.invernadero(invernadero)
        cal = self.calendario
        dias = self.ocupacion(base, incluir_ext, excluir_ids).get(inv.id)
        a = max(desde, cal.primer_dia)
        b = min(hasta, cal.ultimo_dia)
        pico = max(dias[(a - cal.primer_dia).days:(b - cal.primer_dia).days + 1], default=0) \
            if dias and a <= b else 0
        return max(0, inv.capacidad - pico)

    def maximo_partida(self, base: str, codigo: str, invernadero: str,
                       incluir_ext: bool = True) -> int:
        """Lo máximo que se puede poner de una partida en un invernadero sin pasar su
        capacidad ningún día de su estancia ni las bandejas que le quedan libres."""
        inv = self.invernadero(invernadero)
        codigo = codigo.strip().upper()
        if base == "real":
            d = self.dato_real(codigo)
            total = d.bandejas_real
            entrada, liberacion = self.fechas_reales(d)
        else:
            p = self.partida(codigo)
            total = p.bandejas
            entrada, liberacion = p.entrada, self.liberacion_efectiva(p)
        celda = [a for a in self._asignaciones(base)
                 if a.codigo == codigo and a.invernadero_id == inv.id and a.calle_id is None]
        libre = total - self.asignado(base, codigo) + sum(a.bandejas for a in celda)
        entrada = entrada or self.calendario.primer_dia
        liberacion = liberacion or self.calendario.ultimo_dia
        hueco = self.hueco(base, inv.nombre, entrada, liberacion, incluir_ext,
                           excluir_ids=[a.id for a in celda])
        return max(0, min(libre, hueco))

    # --- Avisos -------------------------------------------------------------------

    def excedidos(self, base: str, incluir_ext: bool = True,
                  desde: date | None = None) -> list[Exceso]:
        """Invernaderos por encima de su capacidad algún día (se permite, con aviso, §5.9)."""
        cal = self.calendario
        inicio = max(0, (desde - cal.primer_dia).days) if desde else 0
        occ = self.ocupacion(base, incluir_ext)
        res = []
        for inv in self.invernaderos():
            if inv.especial or inv.id not in occ:
                continue
            dias = occ[inv.id]
            pasados = [i for i in range(inicio, len(dias)) if dias[i] > inv.capacidad]
            if pasados:
                i_max = max(pasados, key=lambda i: dias[i])
                res.append(Exceso(inv.nombre, inv.capacidad, dias[i_max],
                                  cal.primer_dia + timedelta(days=i_max),
                                  cal.primer_dia + timedelta(days=pasados[0])))
        return res

    def mezclas_cultivo(self, base: str, incluir_ext: bool = True) -> list[Mezcla]:
        """Invernaderos donde coinciden dos cultivos algún día (solo aviso, §5.8)."""
        cal = self.calendario
        n = cal.n_dias
        dif: dict[tuple[int, str], list[int]] = defaultdict(lambda: [0] * (n + 1))
        for t in self.tramos(base, incluir_ext):
            desde = max(t.entrada, cal.primer_dia)
            hasta = min(t.liberacion, cal.ultimo_dia)
            if desde > hasta:
                continue
            fila = dif[(t.invernadero_id, cultivo_para_mezcla(t.cultivo))]
            fila[(desde - cal.primer_dia).days] += 1
            fila[(hasta - cal.primer_dia).days + 1] -= 1
        por_inv: dict[int, dict[str, list[int]]] = defaultdict(dict)
        for (inv_id, cultivo), fila in dif.items():
            acumulado, dias = 0, []
            for i in range(n):
                acumulado += fila[i]
                dias.append(acumulado)
            por_inv[inv_id][cultivo] = dias
        res = []
        for inv in self.invernaderos():
            cultivos = por_inv.get(inv.id, {})
            if inv.especial or len(cultivos) < 2:
                continue
            for i in range(n):
                presentes = tuple(sorted(c for c, dias in cultivos.items() if dias[i] > 0))
                if len(presentes) > 1:
                    res.append(Mezcla(inv.nombre, cal.primer_dia + timedelta(days=i), presentes))
                    break
        return res
