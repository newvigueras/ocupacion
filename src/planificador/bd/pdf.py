"""PDF diarios de túneles («parte de injerto») y de cargas («parte de carga») (§12.1–12.2).

* **Túneles**: cantidad real injertada (Nº Band.), fecha real de entrada al invernadero
  (FecRealSal), calle (Localización), fecha de entrega, cliente, categoría (por el nombre
  del grupo: «TI15 - TOMATE INJ 150») y lucha biológica (cabecera «007 - BI…»). Una
  partida puede salir varias veces: se suman.
* **Cargas**: «DIA: DD/MM/AAAA» en la cabecera es la fecha real de salida de todas las
  partidas del parte; filas Hora, Cliente, Partida, Variedad, Nº Band, Invernadero-Calle-Carro.

El texto se reconstruye por la **posición** de cada palabra (como el planificador HTML):
las columnas quedan separadas por dos o más espacios. Para abrir los PDF hace falta
``pdfplumber`` (``pip install -e ".[pdf]"``); el análisis del texto no lo necesita.

Lo leído se guarda en los datos reales de cada partida y las asignaciones que ya tenga
en Real se actualizan con las fechas nuevas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..motor import clasificacion as C
from .calles import redondear
from .planificacion import Planificador
from .tablas import Asignacion, DatoReal, Partida

FUENTE_TUNELES = "PDF_TUNELES"
FUENTE_CARGAS = "PDF_CARGAS"


class ErrorPdf(ValueError):
    pass


# --- Texto con columnas ----------------------------------------------------------------

@dataclass(frozen=True)
class Palabra:
    x0: float
    x1: float
    top: float
    texto: str


def texto_de_palabras(palabras: Iterable[Palabra], tolerancia: float = 2) -> str:
    """Reconstruye las líneas de una página por la posición de cada palabra.

    Un hueco de más de 1,6 caracteres (de ancho medio de la palabra) se convierte en
    varios espacios (separador de columna); uno menor, en un espacio.
    """
    por_y: dict[int, list[Palabra]] = {}
    for p in palabras:
        if p.texto.strip():
            por_y.setdefault(round(p.top), []).append(p)
    lineas: list[list[Palabra]] = []
    ultima_y = None
    for y in sorted(por_y):
        if ultima_y is not None and abs(y - ultima_y) <= tolerancia:
            lineas[-1].extend(por_y[y])
        else:
            lineas.append(list(por_y[y]))
            ultima_y = y
    texto = []
    for linea in lineas:
        partes, fin = [], None
        for p in sorted(linea, key=lambda p: p.x0):
            ancho = (p.x1 - p.x0) / len(p.texto) if p.texto and p.x1 > p.x0 else 5
            if fin is not None:
                hueco = p.x0 - fin
                if hueco > ancho * 1.6:
                    partes.append(" " * min(40, max(2, round(hueco / ancho))))
                elif hueco > ancho * 0.3:
                    partes.append(" ")
            partes.append(p.texto)
            fin = p.x1 if p.x1 > p.x0 else p.x0 + len(p.texto) * 5
        texto.append("".join(partes))
    return "\n".join(texto)


def texto_de_pdf(ruta: str | Path) -> tuple[str, int]:
    """Texto de todas las páginas (con columnas) y número de páginas."""
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover - depende de la instalación
        raise ErrorPdf('Para leer PDF hace falta pdfplumber: pip install -e ".[pdf]"') from e
    ruta = Path(ruta)
    try:
        with pdfplumber.open(ruta) as pdf:
            paginas = []
            for pagina in pdf.pages:
                palabras = [Palabra(w["x0"], w["x1"], w["top"], w["text"])
                            for w in pagina.extract_words(keep_blank_chars=False)]
                paginas.append(texto_de_palabras(palabras))
            return "\n".join(paginas), len(pdf.pages)
    except ErrorPdf:
        raise
    except Exception as e:  # pdfminer lanza excepciones de varios tipos
        raise ErrorPdf(f"{ruta.name}: no se puede leer como PDF ({e})") from e


def tipo_de_texto(texto: str) -> str:
    """'cargas' o 'tuneles' según el contenido."""
    if "PARTE DE CARGA" in texto.upper() or re.search(r"DIA:\s*\d{2}/\d{2}/\d{4}", texto):
        return "cargas"
    return "tuneles"


def _fecha_dmy(texto: str | None) -> date | None:
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", texto or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _numero_inicial(texto: str) -> float:
    """Como parseFloat de JavaScript: el número del principio, o 0."""
    m = re.match(r"^\s*[+-]?(\d+\.?\d*|\.\d+)", texto or "")
    return float(m.group(0)) if m else 0.0


# --- Parte de injerto (túneles) --------------------------------------------------------

@dataclass
class RegistroTunel:
    partida: str
    fecha_entrega: str
    band_sol: int | None
    cliente: str
    bandejas: float
    localizacion: str
    fec_entrada: str
    fec_real_sal: str
    categoria_codigo: str | None
    categoria_nombre: str | None
    bio: str | None
    observaciones: str


_FECHA = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_PARTE = re.compile(r"^\d{6,}$")
_SUBTOTAL = re.compile(r"^[\d.,]+\s+[\d.,]+$")
_COLUMNAS = re.compile(r"\s{2,}")


def leer_parte_injerto(texto: str) -> list[RegistroTunel]:
    lineas = texto.split("\n")
    registros: list[RegistroTunel] = []
    cat_codigo = cat_nombre = bio = None
    i = 0
    while i < len(lineas):
        t = lineas[i].strip()
        if (not t or t.startswith(("Empresa", "NºParte"))
                or ("Cliente" in t and "FecPrevSal" in t)):
            i += 1
            continue
        if m := re.match(r"^([A-Za-z]+\d+)\s*-\s*(.+)$", t):
            cat_codigo, cat_nombre, bio = m.group(1), m.group(2), None
            i += 1
            continue
        if m := re.match(r"^(\d+)\s*-\s*(.+)$", t):
            bio = m.group(2)
            i += 1
            continue
        if t == "No definido":
            bio = t
            i += 1
            continue

        cols = _COLUMNAS.split(t)
        if not (len(cols) >= 3 and _PARTE.match(cols[0]) and _FECHA.match(cols[1])):
            i += 1
            continue
        partida = cols[2].strip().upper()
        idx, band_sol = 3, None
        if idx < len(cols):
            # Un número pegado al texto siguiente («228RUSTICA LA OLIVA SL») se separa.
            if g := re.match(r"^(\d{1,6})([A-ZÑÁÉÍÓÚ].+)$", cols[idx]):
                band_sol, cols[idx] = int(g.group(1)), g.group(2)
            elif re.match(r"^\d{1,6}$", cols[idx]):
                band_sol = int(cols[idx])
                idx += 1
        cliente = cols[idx] if idx < len(cols) else ""
        idx += 1
        if band_sol is None and (g := re.match(r"^(\d{1,6})\s+([A-ZÑ].+)$", cliente)):
            band_sol, cliente = int(g.group(1)), g.group(2)
        idx += 1  # Nº Plantas
        bandejas = _numero_inicial(cols[idx]) if idx < len(cols) else 0.0
        idx += 1
        localizacion = cols[idx] if idx < len(cols) else ""
        idx += 1
        fec_entrada = cols[idx] if idx < len(cols) and _FECHA.match(cols[idx]) else ""
        idx += 1
        resto = cols[idx:]
        if resto and _FECHA.match(resto[-1]):
            fec_real_sal, obs = resto[-1], " ".join(resto[:-1])
        else:
            fec_real_sal, obs = "", " ".join(resto)

        # Las observaciones siguen en las líneas de abajo hasta el separador, un salto de
        # página, un subtotal o la siguiente fila de datos.
        j, extra = i + 1, []
        while j < len(lineas) and lineas[j].strip() != "_________":
            u = lineas[j].strip()
            if u.startswith("Empresa") or _SUBTOTAL.match(u) or _PARTE.match(_COLUMNAS.split(u)[0]):
                break
            if u:
                extra.append(u)
            j += 1
        registros.append(RegistroTunel(
            partida=partida, fecha_entrega=cols[1], band_sol=band_sol, cliente=cliente,
            bandejas=bandejas, localizacion=localizacion, fec_entrada=fec_entrada,
            fec_real_sal=fec_real_sal, categoria_codigo=cat_codigo, categoria_nombre=cat_nombre,
            bio=bio, observaciones=(obs + " " + " ".join(extra)).strip(),
        ))
        i = j + 1 if j < len(lineas) and lineas[j].strip() == "_________" else j
    return registros


def categoria_de_grupo(nombre: str | None) -> str:
    n = C.mayus(nombre)
    for palabra, categoria in (("JIFFY", "Jiffy"), ("COT", "Cotiledon"), ("PINZ", "Pinzado"),
                               ("HIDRO", "Hidro")):
        if palabra in n:
            return categoria
    return "Normal"


def formato_de_grupo(nombre: str | None) -> str:
    """«TOMATE INJ 150» → '150'; «… 104 REPICADO» → '104'; «JIFFY 8x8» → ''."""
    n = (nombre or "").strip()
    if re.search(r"\d+x\d+$", n, re.I):
        return ""
    m = re.search(r"(\d+)(?:\s+REPICADO)?$", n, re.I)
    return m.group(1) if m else ""


# --- Parte de carga ---------------------------------------------------------------------

@dataclass
class RegistroCarga:
    partida: str
    cliente: str
    variedad: str
    bandejas: int
    invernadero_carro: str
    dia: str | None


def leer_linea_cargas(linea: str) -> RegistroCarga | None:
    t = linea.strip()
    if not t or t == "***" or t.startswith("CAMION"):
        return None
    t = re.sub(r"^(\d{2}:\d{2}|PTE)\s+", "", t)
    cols = _COLUMNAS.split(t)
    if len(cols) < 3:
        return None
    m = re.match(r"^(\d+)\s+([\d-]+)$", cols[-1])
    if not m:
        return None
    resto = " ".join(cols[:-2])
    c = re.match(r"^(.*?)\s*(\d{2}[A-Z]?\d{4,6}(?:-[A-Z])?)$", resto)
    if not c:
        return None
    return RegistroCarga(partida=c.group(2), cliente=c.group(1).strip(), variedad=cols[-2],
                         bandejas=int(m.group(1)), invernadero_carro=m.group(2), dia=None)


def leer_parte_cargas(texto: str) -> list[RegistroCarga]:
    registros, dia = [], None
    for linea in texto.split("\n"):
        if m := re.search(r"DIA:\s*(\d{2}/\d{2}/\d{4})", linea):
            dia = m.group(1)
        if not linea.strip():
            continue
        if "HORA" in linea and "CLIENTE" in linea and "PARTIDA" in linea:
            continue
        if ("Grupo Semilleros" in linea or "PARTE DE CARGA" in linea
                or re.search(r"gina\s*\d", linea)):
            continue
        if r := leer_linea_cargas(linea):
            r.dia = dia
            registros.append(r)
    return registros


# --- Guardar en la base de datos ---------------------------------------------------------

@dataclass
class ResumenPdf:
    archivo: str
    tipo: str  # 'tuneles' o 'cargas'
    paginas: int = 0
    registros: int = 0
    partidas: list[str] = field(default_factory=list)
    nuevas: list[str] = field(default_factory=list)  # sin dato real antes
    fuera_del_maestro: list[str] = field(default_factory=list)
    bandejas: float = 0
    fechas: list[date] = field(default_factory=list)  # entrada (túneles) o salida (cargas)
    asignaciones_actualizadas: int = 0
    detalle: list[tuple] = field(default_factory=list)


def refrescar_fechas_reales(s: Session, codigos: Iterable[str]) -> int:
    """Pone en las asignaciones de Real la entrada y liberación más recientes (§12.1)."""
    plan = Planificador(s)
    codigos = set(codigos)
    if not codigos:
        return 0
    cambios = 0
    datos = {d.codigo: d for d in s.scalars(select(DatoReal).where(DatoReal.codigo.in_(codigos)))}
    for a in s.scalars(select(Asignacion).where(Asignacion.base == "real",
                                                Asignacion.codigo.in_(codigos))):
        d = datos.get(a.codigo)
        if d is None:
            continue
        entrada, liberacion = plan.fechas_reales(d)
        entrada, liberacion = entrada or a.entrada, liberacion or a.liberacion
        if (a.entrada, a.liberacion) != (entrada, liberacion):
            a.entrada, a.liberacion = entrada, liberacion
            semana = plan.calendario.semana_de(entrada) if entrada else None
            if semana is not None:
                a.semana = semana
            cambios += 1
    s.flush()
    return cambios


def _dato(s: Session, codigo: str, fuente: str, res: ResumenPdf) -> DatoReal:
    d = s.get(DatoReal, codigo)
    if d is None:
        d = DatoReal(codigo=codigo, bandejas_real=0, fuente=fuente)
        p = s.get(Partida, codigo)
        if p is not None:
            d.tipo_planta, d.cliente, d.variedad = p.tipo_planta, p.cliente, p.variedad
            d.categoria, d.cultivo, d.bio = p.categoria, p.cultivo, p.bio
            d.estilo, d.formato = p.estilo, p.formato
        else:
            d.tipo_planta = C.tipo_planta_de_codigo(codigo)
            res.fuera_del_maestro.append(codigo)
        s.add(d)
        res.nuevas.append(codigo)
    return d


def guardar_tuneles(s: Session, registros: Sequence[RegistroTunel], res: ResumenPdf) -> ResumenPdf:
    por_partida: dict[str, list[RegistroTunel]] = {}
    for r in registros:
        por_partida.setdefault(r.partida, []).append(r)
    res.registros = len(registros)
    for codigo, rs in por_partida.items():
        d = _dato(s, codigo, FUENTE_TUNELES, res)
        primero = rs[0]
        bandejas = sum(r.bandejas for r in rs)
        entradas = list(dict.fromkeys(r.fec_real_sal for r in rs if r.fec_real_sal))
        calles = list(dict.fromkeys(r.localizacion for r in rs if r.localizacion))
        obs = " | ".join(r.observaciones for r in rs if r.observaciones)
        d.bandejas_real = redondear(bandejas)
        if entradas and (f := _fecha_dmy(entradas[0])):
            d.fec_entrada_real = f
            res.fechas.append(f)
        d.localizacion = ", ".join(calles)
        d.observaciones = obs
        d.estilo = C.estilo(obs)
        d.cliente = primero.cliente or d.cliente
        d.categoria = categoria_de_grupo(primero.categoria_nombre)
        tipo = d.tipo_planta or C.tipo_planta_de_codigo(codigo)
        d.bio = C.bio_valor(codigo, tipo, C.mayus(primero.bio).startswith("BI"))
        d.formato = formato_de_grupo(primero.categoria_nombre) or d.formato
        if not d.cultivo:
            d.cultivo = C.cultivo(primero.categoria_nombre or "", d.categoria)
        d.fecha_entrega_pdf = _fecha_dmy(primero.fecha_entrega)
        res.partidas.append(codigo)
        res.bandejas += bandejas
        band_sol = next((r.band_sol for r in reversed(rs) if r.band_sol is not None), None)
        res.detalle.append((codigo, d.cliente, band_sol, redondear(bandejas),
                            ", ".join(entradas), d.localizacion))
    s.flush()
    res.asignaciones_actualizadas = refrescar_fechas_reales(s, res.partidas)
    return res


def guardar_cargas(s: Session, registros: Sequence[RegistroCarga], res: ResumenPdf) -> ResumenPdf:
    por_partida: dict[str, list[RegistroCarga]] = {}
    for r in registros:
        por_partida.setdefault(r.partida, []).append(r)
    res.registros = len(registros)
    for codigo, rs in por_partida.items():
        dia = _fecha_dmy(rs[0].dia)
        if dia is None:
            continue
        d = _dato(s, codigo, FUENTE_CARGAS, res)
        d.fecha_salida_real = dia
        if not d.cliente:
            d.cliente = rs[0].cliente
        bandejas = sum(r.bandejas for r in rs)
        res.partidas.append(codigo)
        res.bandejas += bandejas
        if dia not in res.fechas:
            res.fechas.append(dia)
        res.detalle.append((codigo, rs[0].cliente, rs[0].variedad, bandejas))
    s.flush()
    res.asignaciones_actualizadas = refrescar_fechas_reales(s, res.partidas)
    return res


def cargar_texto(s: Session, texto: str, archivo: str = "", tipo: str | None = None) -> ResumenPdf:
    tipo = tipo or tipo_de_texto(texto)
    res = ResumenPdf(archivo=archivo, tipo=tipo)
    if tipo == "cargas":
        return guardar_cargas(s, leer_parte_cargas(texto), res)
    return guardar_tuneles(s, leer_parte_injerto(texto), res)


def cargar_pdf(s: Session, ruta: str | Path, tipo: str | None = None) -> ResumenPdf:
    """Lee un PDF de túneles o de cargas (el tipo se reconoce solo) y lo guarda en Real."""
    texto, paginas = texto_de_pdf(ruta)
    res = cargar_texto(s, texto, Path(ruta).name, tipo)
    res.paginas = paginas
    if not res.partidas:
        raise ErrorPdf(f"{Path(ruta).name}: no encuentro ninguna partida "
                       f"(¿es un parte de {'carga' if res.tipo == 'cargas' else 'injerto'}?)")
    return res
