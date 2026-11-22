"""Órdenes de consola del planificador.

    planificador importar 201126.xlsx [--tipo injerto] [--partida 26X02589] [--guardar]
    planificador preparar [--desde 2026 --hasta 2027]
    planificador semana 49 [--tipo injerto]
    planificador asignar-bloque 49 1 INV41 500
    planificador asignar-partida 26X02589 INV41 300 [--base real] [--calle 3]
    planificador tecnico 26X02589 2027-01-10        (o «quitar»)
    planificador ocupacion [--base partidas] [--fecha 2026-12-01] [--sin-ext]

La base de datos se elige con --bd o con la variable PLANIFICADOR_BD
(por defecto, el archivo planificador.db de la carpeta actual).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from sqlalchemy.orm import Session

from .bd import BASES, ErrorPlan, Planificador, crear_motor, guardar_partidas, preparar_bd
from .motor import ErrorExcel, leer_excel


def _miles(n: float) -> str:
    return f"{n:,.0f}".replace(",", ".")


def _sesion(args: argparse.Namespace) -> Session:
    motor = crear_motor(args.bd)
    preparar_bd(motor)
    return Session(motor)


def _fecha(texto: str) -> date:
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Fecha no válida (AAAA-MM-DD): {texto}")


def cmd_importar(args: argparse.Namespace) -> int:
    errores = 0
    sesion = _sesion(args) if args.guardar else None
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
        if sesion is not None:
            r = guardar_partidas(sesion, res)
            sesion.commit()
            print(f"   Guardado en la base de datos: {r.nuevas} nuevas, "
                  f"{r.actualizadas} actualizadas, {r.sin_cambios} sin cambios")
    if sesion is not None:
        sesion.close()
    return 1 if errores else 0


def cmd_preparar(args: argparse.Namespace) -> int:
    motor = crear_motor(args.bd)
    preparar_bd(motor, args.desde, args.hasta)
    with Session(motor) as s:
        plan = Planificador(s)
        invs = [i for i in plan.invernaderos() if not i.especial]
        cal = plan.calendario
        print(f"Base de datos lista: {len(invs)} invernaderos, "
              f"{sum(len(i.calles) for i in invs)} calles, "
              f"{_miles(sum(i.capacidad for i in invs))} bandejas de capacidad; "
              f"calendario {cal.año_inicio}–{cal.año_fin} ({cal.n_semanas} semanas)")
    return 0


def cmd_semana(args: argparse.Namespace) -> int:
    with _sesion(args) as s:
        plan = Planificador(s)
        sem = plan.calendario.semana(args.semana)
        bloques = plan.bloques_de_semana(args.semana, args.tipo)
        print(f"Semana {sem.etiqueta} ({sem.inicio:%d/%m} – {sem.fin:%d/%m/%Y}) · {args.tipo}: "
              f"{len(bloques)} bloques, {_miles(sum(b.total for b in bloques))} bandejas")
        for n, b in enumerate(bloques, 1):
            puesto = sum(plan.asignado("bloques", p.codigo) for p in b.partidas)
            nombre = " · ".join(filter(None, b.clave))
            print(f"  {n:>3}. {nombre:<55} {len(b.partidas):>4} part. "
                  f"{_miles(b.total):>7} band. · asignadas {_miles(puesto)}")
    return 0


def cmd_asignar_bloque(args: argparse.Namespace) -> int:
    with _sesion(args) as s:
        plan = Planificador(s)
        bloques = plan.bloques_de_semana(args.semana, args.tipo)
        if not 1 <= args.bloque <= len(bloques):
            print(f"ERROR: la semana {args.semana} tiene {len(bloques)} bloques", file=sys.stderr)
            return 1
        b = bloques[args.bloque - 1]
        puesto = plan.asignar_bloque(args.semana, args.tipo, b.clave, args.invernadero,
                                     args.bandejas)
        s.commit()
        print(f"{' · '.join(filter(None, b.clave))} → {args.invernadero}: {_miles(puesto)} "
              f"de {_miles(args.bandejas)} bandejas")
        _avisos(plan, "bloques")
    return 0


def cmd_asignar_partida(args: argparse.Namespace) -> int:
    with _sesion(args) as s:
        plan = Planificador(s)
        puesto = plan.asignar_partida(args.base, args.codigo, args.invernadero, args.bandejas,
                                      calle=args.calle)
        s.commit()
        donde = args.invernadero + (f" calle {args.calle}" if args.calle is not None else "")
        print(f"{args.codigo.upper()} → {donde} ({args.base}): {_miles(puesto)} bandejas")
        _avisos(plan, args.base)
    return 0


def cmd_tecnico(args: argparse.Namespace) -> int:
    fecha = None if args.fecha.lower() == "quitar" else _fecha(args.fecha)
    with _sesion(args) as s:
        n = Planificador(s).poner_fecha_tecnico(args.codigo, fecha)
        s.commit()
    texto = f"{fecha:%d/%m/%Y}" if fecha else "quitada"
    print(f"Fecha técnico de {args.codigo.upper()}: {texto} ({n} asignaciones actualizadas)")
    return 0


def cmd_ocupacion(args: argparse.Namespace) -> int:
    with _sesion(args) as s:
        plan = Planificador(s)
        dia = args.fecha or min(max(date.today(), plan.calendario.primer_dia),
                                plan.calendario.ultimo_dia)
        ext = not args.sin_ext
        occ = plan.ocupacion_dia(args.base, dia, incluir_ext=ext)
        print(f"Ocupación el {dia:%d/%m/%Y} · base {args.base}"
              + (" + lo ya colocado (Real)" if ext and args.base != "real" else ""))
        sala_actual = None
        for inv in plan.invernaderos():
            if inv.especial and not occ.get(inv.nombre):
                continue
            if inv.sala != sala_actual:
                sala_actual = inv.sala
                print(f"  {sala_actual or 'Otros destinos'}")
            n = occ.get(inv.nombre, 0)
            pct = "" if inv.especial else f"{100 * n / inv.capacidad:5.1f} %"
            cap = "" if inv.especial else f"/ {_miles(inv.capacidad):>6}"
            print(f"    {inv.nombre:<8} {_miles(n):>7} {cap} {pct}")
        _avisos(plan, args.base, ext)
    return 0


def _avisos(plan: Planificador, base: str, ext: bool = True) -> None:
    desde = date.today() if ext and base != "real" else None
    for e in plan.excedidos(base, ext, desde=desde):
        print(f"  ! {e.invernadero} pasa su capacidad desde el {e.primer_dia:%d/%m/%Y} "
              f"(máximo {_miles(e.maximo)} de {_miles(e.capacidad)} el {e.dia_maximo:%d/%m/%Y})")
    for m in plan.mezclas_cultivo(base, ext):
        print(f"  ~ {m.invernadero}: coinciden {', '.join(m.cultivos)} "
              f"desde el {m.primer_dia:%d/%m/%Y}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="planificador", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bd", help="Dirección de la base de datos (por defecto PLANIFICADOR_BD "
                                     "o sqlite:///planificador.db)")
    sub = parser.add_subparsers(dest="orden", required=True)

    imp = sub.add_parser("importar", help="Leer Excel de previsión y mostrar el resumen")
    imp.add_argument("archivos", nargs="+")
    imp.add_argument("--tipo", choices=["injerto", "normal", "componente"],
                     help="Forzar el tipo de planta (por defecto se deduce del código)")
    imp.add_argument("--partida", action="append", help="Mostrar el detalle de una partida")
    imp.add_argument("--guardar", action="store_true",
                     help="Guardar las partidas en la base de datos (fusiona por código)")
    imp.set_defaults(func=cmd_importar)

    pre = sub.add_parser("preparar", help="Crear la base de datos con invernaderos y calles")
    pre.add_argument("--desde", type=int, default=2026, help="Primer año del calendario")
    pre.add_argument("--hasta", type=int, default=2027, help="Último año del calendario")
    pre.set_defaults(func=cmd_preparar)

    tipos = ["injerto", "normal", "componente"]
    sem = sub.add_parser("semana", help="Bloques de partidas que entran una semana")
    sem.add_argument("semana", type=int, help="Número de semana continuo (1…105)")
    sem.add_argument("--tipo", choices=tipos, default="injerto")
    sem.set_defaults(func=cmd_semana)

    ab = sub.add_parser("asignar-bloque", help="Poner un bloque en un invernadero (base Bloques)")
    ab.add_argument("semana", type=int)
    ab.add_argument("bloque", type=int, help="Número del bloque en «planificador semana»")
    ab.add_argument("invernadero")
    ab.add_argument("bandejas", type=int, help="Bandejas en esa celda (0 = vaciarla)")
    ab.add_argument("--tipo", choices=tipos, default="injerto")
    ab.set_defaults(func=cmd_asignar_bloque)

    ap = sub.add_parser("asignar-partida", help="Poner una partida en un invernadero")
    ap.add_argument("codigo")
    ap.add_argument("invernadero")
    ap.add_argument("bandejas", type=int, help="Bandejas en esa celda (0 = vaciarla)")
    ap.add_argument("--base", choices=["partidas", "real"], default="partidas")
    ap.add_argument("--calle", type=int)
    ap.set_defaults(func=cmd_asignar_partida)

    tec = sub.add_parser("tecnico", help="Poner o quitar la fecha técnico de una partida")
    tec.add_argument("codigo")
    tec.add_argument("fecha", help="AAAA-MM-DD o «quitar»")
    tec.set_defaults(func=cmd_tecnico)

    oc = sub.add_parser("ocupacion", help="Ocupación de cada invernadero un día")
    oc.add_argument("--base", choices=list(BASES), default="partidas")
    oc.add_argument("--fecha", type=_fecha, help="AAAA-MM-DD (por defecto, hoy)")
    oc.add_argument("--sin-ext", action="store_true",
                    help="No contar lo ya colocado (base Real) en Bloques o Partidas")
    oc.set_defaults(func=cmd_ocupacion)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ErrorPlan as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
