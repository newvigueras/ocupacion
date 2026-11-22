"""Lectura de los Excel de previsión (documento maestro §11).

Hoja «SALIDA DE TOMATE CON FECHA PREV» (o la primera que tenga la cabecera); la
cabecera se busca en las 15 primeras filas. Obligatorias: PARTIDA, F Siembra,
F Entrega y (Band o Pl sol).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import clasificacion as C
from .bandejas import calcular_bandejas
from .modelos import Aviso, Partida, ResultadoImportacion
from .reglas import CODIGO_CONOCIDO, DIAS_ENTRADA, DIAS_LIBERACION, TIPOS_PLANTA, Reglas

HOJA_PREFERIDA = "SALIDA DE TOMATE CON FECHA PREV"
FILAS_BUSQUEDA_CABECERA = 15

COLUMNAS: dict[str, tuple[str, ...]] = {
    "partida": ("partida", "n partida", "no partida", "n de partida"),
    "variedad": ("variedad",),
    "f_siembra": ("f siembra", "fecha siembra"),
    "f_siembra_real": ("fecha real siembra", "f real siembra"),
    "f_entrega": ("f entrega", "fecha entrega"),
    "band": ("band", "bandejas"),
    "pl_sol": ("pl sol", "plantas solicitadas"),
    "observa": ("observa", "observaciones"),
    "tipo_siembra": ("tipo de siembra", "tipo siembra"),
    "lucha": ("partida biologica",),
    "cliente": ("nombre cliente", "cliente"),
    "soporte": ("soporte de siembra", "soporte siembra"),
}


class ErrorExcel(ValueError):
    pass


# --- Conversión de celdas ---------------------------------------------------

def normalizar_cabecera(valor: Any) -> str:
    texto = C.sin_tildes(str(valor or "")).lower()
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return texto.strip()


_EPOCA_EXCEL = date(1899, 12, 30)


def a_fecha(valor: Any) -> date | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, (int, float)):
        if 1 <= valor < 200000:
            return _EPOCA_EXCEL + timedelta(days=int(valor))
        return None
    texto = str(valor).strip()
    for formato in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(texto.split(" ")[0], formato).date()
        except ValueError:
            continue
    return None


def a_numero(valor: Any) -> float | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace(" ", "").replace(" ", "")
    if not texto:
        return None
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", texto):
        texto = texto.replace(".", "")
    try:
        return float(texto)
    except ValueError:
        return None


def a_texto(valor: Any) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def a_codigo(valor: Any) -> str:
    return a_texto(valor).upper()


# --- Cabecera ---------------------------------------------------------------

def mapear_cabecera(fila: Sequence[Any]) -> dict[str, int] | None:
    normal = [normalizar_cabecera(v) for v in fila]
    mapa: dict[str, int] = {}
    for campo, alias in COLUMNAS.items():
        for i, nombre in enumerate(normal):
            if nombre in alias:
                mapa[campo] = i
                break
    obligatorias = {"partida", "f_siembra", "f_entrega"}
    if obligatorias <= mapa.keys() and ("band" in mapa or "pl_sol" in mapa):
        return mapa
    return None


def buscar_cabecera(filas: Sequence[Sequence[Any]]) -> tuple[int, dict[str, int]] | None:
    for i, fila in enumerate(filas[:FILAS_BUSQUEDA_CABECERA]):
        mapa = mapear_cabecera(fila)
        if mapa:
            return i, mapa
    return None


# --- Analizador ---------------------------------------------------------------

def analizar_filas(
    filas: Sequence[Sequence[Any]],
    *,
    reglas: Reglas | None = None,
    tipo_planta: str | None = None,
    archivo: str = "",
    hoja: str = "",
) -> ResultadoImportacion:
    """Convierte las filas de una hoja (con cabecera) en partidas.

    ``tipo_planta`` fuerza el tipo de todas las filas; si es ``None`` se deduce
    del código de cada partida.
    """
    if tipo_planta is not None and tipo_planta not in TIPOS_PLANTA:
        raise ValueError(f"Tipo de planta desconocido: {tipo_planta}")
    reglas = reglas or Reglas.por_defecto()
    encontrada = buscar_cabecera(filas)
    if not encontrada:
        raise ErrorExcel(
            "No se encuentra la cabecera (PARTIDA, F Siembra, F Entrega y Band o Pl sol) "
            f"en las {FILAS_BUSQUEDA_CABECERA} primeras filas"
        )
    idx_cab, mapa = encontrada
    res = ResultadoImportacion(archivo=archivo, hoja=hoja, fila_cabecera=idx_cab + 1)

    def celda(fila: Sequence[Any], campo: str) -> Any:
        i = mapa.get(campo)
        return fila[i] if i is not None and i < len(fila) else None

    for i in range(idx_cab + 1, len(filas)):
        fila = filas[i]
        n_fila = i + 1
        codigo = a_codigo(celda(fila, "partida"))
        if not codigo:
            continue

        def omitir(motivo: str) -> None:
            res.avisos.append(Aviso("omitida", motivo, codigo, n_fila))

        if not CODIGO_CONOCIDO.match(codigo):
            res.avisos.append(Aviso(
                "codigo_desconocido", "Código de partida desconocido: se ignora de momento",
                codigo, n_fila,
            ))
            continue

        tipo = tipo_planta or C.tipo_planta_de_codigo(codigo)
        variedad = a_texto(celda(fila, "variedad"))
        observa = a_texto(celda(fila, "observa"))
        tipo_siembra = a_texto(celda(fila, "tipo_siembra")).upper()
        siembra = a_fecha(celda(fila, "f_siembra"))
        siembra_real = a_fecha(celda(fila, "f_siembra_real"))
        entrega = a_fecha(celda(fila, "f_entrega"))
        band = a_numero(celda(fila, "band"))
        pl_sol = a_numero(celda(fila, "pl_sol"))

        siembra_efectiva = siembra_real or siembra
        if siembra_efectiva is None or entrega is None:
            omitir("Faltan fechas (F Siembra o F Entrega)")
            continue
        if siembra is not None and entrega < siembra:
            omitir(f"F Entrega ({entrega:%d/%m/%Y}) anterior a F Siembra ({siembra:%d/%m/%Y})")
            continue

        cat = C.categoria(codigo, tipo, variedad)
        calc = calcular_bandejas(
            codigo=codigo, tipo_planta=tipo, categoria=cat, tipo_siembra=tipo_siembra,
            observaciones=observa, variedad=variedad, band=band, pl_sol=pl_sol, reglas=reglas,
        )
        if calc.bandejas <= 0:
            omitir("Sin bandejas")
            continue

        entrada = siembra_efectiva + timedelta(days=DIAS_ENTRADA[tipo])
        liberacion = entrega + timedelta(days=DIAS_LIBERACION)
        if liberacion < entrada:
            omitir(f"Liberación ({liberacion:%d/%m/%Y}) anterior a la entrada ({entrada:%d/%m/%Y})")
            continue

        anterior = res.partidas.pop(codigo, None)
        if anterior is not None:
            # Gana la última fila: los avisos de cálculo de la anterior ya no valen.
            res.avisos = [
                a for a in res.avisos if not (a.codigo == codigo and a.fila == anterior.fila)
            ]

        for tipo_aviso, mensaje in calc.avisos:
            res.avisos.append(Aviso(tipo_aviso, mensaje, codigo, n_fila))

        if anterior is not None:
            res.avisos.append(Aviso(
                "repetida",
                f"Partida repetida (filas {anterior.fila} y {n_fila}); gana la última",
                codigo, n_fila,
            ))

        res.partidas[codigo] = Partida(
            codigo=codigo,
            tipo_planta=tipo,
            cliente=a_texto(celda(fila, "cliente")),
            variedad=variedad,
            categoria=cat,
            cultivo=C.cultivo(variedad, cat, calc.medida),
            medida=calc.medida,
            bio=C.bio_valor(codigo, tipo, C.es_lucha_biologica(celda(fila, "lucha"))),
            bandejas=calc.bandejas,
            entrada=entrada,
            liberacion=liberacion,
            siembra=siembra,
            siembra_real=siembra_real,
            entrega=entrega,
            formato=a_texto(celda(fila, "soporte")),
            estilo=C.estilo(observa),
            etapa=C.etapa(variedad),
            tipo_siembra=tipo_siembra,
            observaciones=observa,
            bandejas_excel=band,
            plantas=pl_sol,
            plantas_por_bandeja=calc.tasa,
            regla_bandejas=calc.regla,
            planta_desarrollada=calc.desarrollada,
            fila=n_fila,
        )
    return res


def _leer_hoja(ws) -> list[tuple]:
    return [tuple(fila) for fila in ws.iter_rows(values_only=True)]


def leer_excel(
    ruta: str | Path,
    *,
    reglas: Reglas | None = None,
    tipo_planta: str | None = None,
) -> ResultadoImportacion:
    """Lee un Excel de previsión y devuelve las partidas calculadas."""
    from zipfile import BadZipFile

    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    ruta = Path(ruta)
    if ruta.suffix.lower() == ".xls":
        raise ErrorExcel(
            f"{ruta.name}: formato .xls antiguo; ábrelo en Excel y guárdalo como .xlsx"
        )
    try:
        wb = load_workbook(ruta, read_only=True, data_only=True)
    except (InvalidFileException, BadZipFile, KeyError) as e:
        raise ErrorExcel(f"{ruta.name}: no se puede abrir como Excel .xlsx ({e})") from e
    try:
        nombres = sorted(
            wb.sheetnames,
            key=lambda n: 0 if C.mayus(n).startswith(HOJA_PREFERIDA) else 1,
        )
        for nombre in nombres:
            filas = _leer_hoja(wb[nombre])
            if buscar_cabecera(filas):
                return analizar_filas(
                    filas, reglas=reglas, tipo_planta=tipo_planta,
                    archivo=ruta.name, hoja=nombre,
                )
    finally:
        wb.close()
    raise ErrorExcel(f"{ruta.name}: ninguna hoja tiene la cabecera de previsión")


def fusionar(resultados: Iterable[ResultadoImportacion]) -> dict[str, Partida]:
    """Une varias importaciones por código; gana la última."""
    partidas: dict[str, Partida] = {}
    for r in resultados:
        partidas.update(r.partidas)
    return partidas
