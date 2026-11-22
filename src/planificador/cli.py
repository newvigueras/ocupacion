"""Orden de consola para probar el motor con los Excel reales.

    planificador importar 201126.xlsx [--tipo injerto] [--partida 26X02589]
"""

from __future__ import annotations

import argparse
import sys

from .motor import ErrorExcel, leer_excel


def _miles(n: float) -> str:
    return f"{n:,.0f}".replace(",", ".")


def cmd_importar(args: argparse.Namespace) -> int:
    errores = 0
    for ruta in args.archivos:
        try:
            res = leer_excel(ruta, tipo_planta=args.tipo)
        except (ErrorExcel, OSError) as e:
            # Se avisa y se sigue con los demás archivos.
            print(f"ERROR {ruta}: {e}", file=sys.stderr)
            errores += 1
            continue
        print(f"\n== {res.archivo} · hoja «{res.hoja}» · cabecera en la fila {res.fila_cabecera}")
        print(f"   {len(res.partidas)} partidas · {_miles(res.total_bandejas)} bandejas")
        for cultivo, (n, band) in res.resumen_por_cultivo().items():
            print(f"   {cultivo:<14} {n:>5} partidas {_miles(band):>9} bandejas")
        avisos = res.contar_avisos()
        if avisos:
            print("   Avisos: " + ", ".join(f"{k} {v}" for k, v in sorted(avisos.items())))
        for a in res.avisos_de("omitida"):
            print(f"   - omitida {a.codigo} (fila {a.fila}): {a.mensaje}")
        for codigo in args.partida or []:
            p = res.partidas.get(codigo.upper())
            if not p:
                print(f"   {codigo}: no está (o se omitió)")
                continue
            print(f"   {p.codigo}: {p.cultivo} · {p.categoria} · {p.bio or 'sin ECO'} · "
                  f"{p.bandejas} bandejas [{p.regla_bandejas}] · entrada {p.entrada:%d/%m/%Y} · "
                  f"liberación {p.liberacion:%d/%m/%Y}")
    return 1 if errores else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="planificador", description=__doc__)
    sub = parser.add_subparsers(dest="orden", required=True)
    imp = sub.add_parser("importar", help="Leer Excel de previsión y mostrar el resumen")
    imp.add_argument("archivos", nargs="+")
    imp.add_argument("--tipo", choices=["injerto", "normal", "componente"],
                     help="Forzar el tipo de planta (por defecto se deduce del código)")
    imp.add_argument("--partida", action="append", help="Mostrar el detalle de una partida")
    imp.set_defaults(func=cmd_importar)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
