"""Órdenes de consola del planificador.

    planificador importar 201126.xlsx [--tipo injerto] [--partida 26X02589] [--guardar]
    planificador preparar [--desde 2026 --hasta 2027]
    planificador semana 49 [--tipo injerto]
    planificador asignar-bloque 49 1 INV41 500
    planificador asignar-partida 26X02589 INV41 300 [--base real] [--calle 3]
    planificador cargar-extensiones EXTENSIONES.xlsx 0110.xlsx
    planificador cargar-extensiones EXTENSIONCALLES.xlsx 0110.xlsx [--sin-reparto]
    planificador cargar-pdf parte_injerto.pdf cargas_15.pdf
    planificador recalcular
    planificador reimportar 201126.xlsx PLANTA_NORMAL.xlsx [--conservar]
    planificador copias [--base real]  ·  planificador restaurar-copia 12
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


def cmd_cargar_extensiones(args: argparse.Namespace) -> int:
    from .bd.calles import cargar_por_calles
    from .bd.extensiones import cargar_extensiones, clasificar_archivos

    with _sesion(args) as s:
        try:
            arch = clasificar_archivos(args.archivos)
            if arch.calles is not None:  # el de calles manda (§10.5b)
                r = cargar_por_calles(s, arch, repartir=not args.sin_reparto, hoy=args.hoy)
            else:
                r = cargar_extensiones(s, arch)
        except (ErrorExcel, OSError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        s.commit()
    if arch.calles is not None:
        _resumen_calles(r, args.top)
        return 0
    print(f"Movimientos: {r.archivo_movimientos} ({_miles(r.movimientos)} movimientos)")
    if r.archivos_prevision:
        print(f"Previsión: {', '.join(r.archivos_prevision)}")
    print(f"  {_miles(r.partidas)} partidas con algo colocado · {_miles(r.bandejas_netas)} bandejas netas")
    print(f"  Salida: {r.salida_tecnico_o_pdf} fecha técnico o PDF · {r.salida_maestro} del maestro · "
          f"{r.salida_prevision} de la previsión · {r.salida_estimada} estimadas "
          f"({len(r.dudosas)} por fecha dudosa)")
    print(f"  Cargadas en Real: {_miles(r.bandejas_cargadas)} bandejas en "
          f"{_miles(r.asignaciones)} asignaciones ({_miles(r.partidas_cargadas)} partidas)")
    if r.no_cargadas:
        print(f"  No cargadas (invernaderos que el planificador no tiene): "
              f"{_miles(r.bandejas_no_cargadas)} bandejas")
        for etiqueta, b in sorted(r.no_cargadas.items(), key=lambda x: -x[1])[:args.top]:
            print(f"    {etiqueta:<20} {_miles(b):>8}")
    if r.conservadas:
        print(f"  No tocadas (Real con trabajo a mano): {len(r.conservadas)} partidas")
    if r.fuera_de_calendario:
        print(f"  Entrada fuera del calendario: {', '.join(r.fuera_de_calendario[:10])}")
    return 0


def _resumen_calles(r, top: int) -> None:
    print(f"Calles: {r.archivo_calles} ({_miles(r.filas)} partidas con bandejas, "
          f"{r.omitidas} filas sin bandejas)")
    if r.archivo_movimientos:
        print(f"Movimientos (solo la primera colocación): {r.archivo_movimientos}")
    if r.archivos_prevision:
        print(f"Previsión: {', '.join(r.archivos_prevision)}")
    print(f"  En calles del planificador: {_miles(r.en_calle.partidas)} partidas, "
          f"{_miles(r.en_calle.bandejas)} bandejas")
    e, sal = r.entrada, r.salida
    print(f"  Entrada: {e['movimientos']} de EXTENSIONES · {e['existente']} ya guardadas · "
          f"{e['maestro']} del maestro · {e['siembra']} por la siembra · {e['hoy']} hoy")
    print(f"  Salida: {sal['tecnico_o_pdf']} fecha técnico o PDF · {sal['maestro']} del maestro · "
          f"{sal['prevision']} de la previsión · {sal['estimada']} estimadas "
          f"({len(r.dudosas)} por fecha dudosa)")
    if r.retrasadas.partidas:
        print(f"  Siguen pasada su salida prevista (se deja hoy): {r.retrasadas.partidas} partidas, "
              f"{_miles(r.retrasadas.bandejas)} bandejas")
    print(f"  Sustituidas en Real: {r.sustituidas_ext} de EXTENSIONES sin calle, "
          f"{r.sustituidas_calles} de una carga anterior por calle, {r.sustituidas_otras} otras")
    if r.reparto:
        print(f"  Reparto entre calles contiguas (estimación): {r.reparto.repartidas} partidas "
              f"en varias calles, {_miles(r.reparto.movidas)} bandejas fuera de su calle de inicio"
              + (f", {_miles(r.reparto.sin_hueco)} no caben" if r.reparto.sin_hueco else ""))
    print(f"  Cargadas en Real: {_miles(r.bandejas_cargadas)} bandejas en "
          f"{_miles(r.asignaciones)} asignaciones ({_miles(r.partidas_cargadas)} partidas), "
          f"{r.calles_con_carga} calles de {r.invernaderos_con_carga} invernaderos")
    print("  No cargadas:")
    print(f"    invernaderos que el planificador no tiene: {_miles(r.fuera.partidas)} partidas, "
          f"{_miles(r.fuera.bandejas)} bandejas")
    for inv, b in sorted(r.fuera_por_invernadero.items(), key=lambda x: -x[1])[:top]:
        print(f"      {inv:<10} {_miles(b):>8}")
    print(f"    calle que no existe en el planificador: {_miles(r.desconocida.partidas)} partidas, "
          f"{_miles(r.desconocida.bandejas)} bandejas")
    print(f"    sin ubicación: {_miles(r.sin_ubicacion.partidas)} partidas, "
          f"{_miles(r.sin_ubicacion.bandejas)} bandejas")
    print(f"  Avisos: {r.desde_fecha} celdas Inv-Sec venían como fecha (año {r.año_ref}), "
          f"{r.ambiguas} ambiguas tomadas como invernadero 1")
    if r.calles_excedidas:
        peores = ", ".join(f"{i} calle {n} con {_miles(b)} de {_miles(c)}"
                           for i, n, b, c in r.calles_excedidas[:3])
        print(f"    {len(r.calles_excedidas)} calles por encima de su capacidad "
              f"(+{_miles(r.exceso_calles)} bandejas), p. ej. {peores}")
    if r.ext_sin_foto.partidas:
        print(f"    {r.ext_sin_foto.partidas} partidas de EXTENSIONES con salida futura no salen "
              f"en este Excel ({_miles(r.ext_sin_foto.bandejas)} bandejas): se dejan como estaban")
    if r.sin_semana:
        print(f"    Entrada fuera del calendario: {', '.join(r.sin_semana[:10])}")


def cmd_cargar_pdf(args: argparse.Namespace) -> int:
    from .bd.pdf import ErrorPdf, cargar_pdf

    errores = 0
    with _sesion(args) as s:
        for ruta in args.archivos:
            try:
                r = cargar_pdf(s, ruta, args.tipo)
            except ErrorPdf as e:  # el mensaje ya lleva el nombre del archivo
                print(f"ERROR {e}", file=sys.stderr)
                errores += 1
                continue
            except OSError as e:
                print(f"ERROR {ruta}: {e}", file=sys.stderr)
                errores += 1
                continue
            s.commit()
            fechas = ", ".join(f"{f:%d/%m/%Y}" for f in sorted(set(r.fechas))[:5])
            if r.tipo == "cargas":
                print(f"\n== {r.archivo} · parte de carga · {r.paginas} páginas · salida {fechas}")
                print(f"   {len(r.partidas)} partidas, {_miles(r.bandejas)} bandejas salidas")
            else:
                print(f"\n== {r.archivo} · parte de injerto (túneles) · {r.paginas} páginas")
                print(f"   {len(r.partidas)} partidas, {_miles(r.bandejas)} bandejas injertadas"
                      + (f" · entrada real {fechas}" if fechas else ""))
            print(f"   {len(r.nuevas)} sin dato real antes · {r.asignaciones_actualizadas} "
                  "asignaciones de Real actualizadas con las fechas nuevas")
            if r.fuera_del_maestro:
                print(f"   ⚠ {len(r.fuera_del_maestro)} no están en el maestro: "
                      + ", ".join(r.fuera_del_maestro[:10]))
            if args.detalle:
                for fila in r.detalle:
                    print("   " + " · ".join("" if v is None else str(v) for v in fila))
    return 1 if errores else 0


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


_NOMBRE_BASE = {"bloques": "Bloques (Planificación Global)", "partidas": "Partidas",
                "real": "Real (Planificación Exacta)"}


def _informe_recalculo(inf) -> None:
    print(f"\n== {_NOMBRE_BASE[inf.base]}")
    if inf.vacia:
        print("   Sin asignaciones.")
        return
    dif = inf.bandejas_despues - inf.bandejas_antes
    print(f"   Asignaciones: {_miles(inf.asignaciones_antes)} → {_miles(inf.asignaciones_despues)}"
          f" · Bandejas: {_miles(inf.bandejas_antes)} → {_miles(inf.bandejas_despues)}"
          f" ({'+' if dif >= 0 else '-'}{_miles(abs(dif))})")
    if inf.cambio_bio:
        print(f"   Cambiaron de ECO / BIO: {_miles(inf.cambio_bio)} bandejas")
    if inf.recortadas:
        print(f"   Recortadas por haber ahora menos bandejas: {_miles(inf.recortadas)}")
    if inf.otra_semana:
        print(f"   {'⚠ ' if inf.base == 'bloques' else ''}{inf.otra_semana} trozos de partidas que "
              + ("cambiaron de semana: se dejan sin tocar (vuelva a asignarlas en su semana)"
                 if inf.base == "bloques" else "cambiaron de semana: se han movido con la partida"))
    if inf.huerfanas:
        print(f"   ⚠ {inf.huerfanas} trozos de partidas que ya no existen se han dejado sin tocar")
    if inf.semanas_cambiadas:
        textos = [f"{sem}{f' ({tipo})' if tipo else ''}: {_miles(a)} → {_miles(d)}"
                  for sem, tipo, a, d in inf.semanas_cambiadas]
        print("   Semanas con cambio de total: " + " · ".join(textos[:12])
              + (f" … y {len(textos) - 12} más" if len(textos) > 12 else ""))
    else:
        print("   Ninguna semana cambió de total.")
    if inf.copia_id:
        print(f"   Copia de seguridad n.º {inf.copia_id} (planificador restaurar-copia "
              f"{inf.copia_id})")


def cmd_recalcular(args: argparse.Namespace) -> int:
    from .bd.recalcular import recalcular_todo

    with _sesion(args) as s:
        informes = recalcular_todo(s)
        s.commit()
    if all(i.vacia for i in informes):
        print("No hay asignaciones en ninguna base: no hay nada que recalcular.")
        return 0
    print("Recálculo terminado (todas las semanas, las tres bases).")
    for inf in informes:
        _informe_recalculo(inf)
    return 0


def cmd_reimportar(args: argparse.Namespace) -> int:
    from .bd.recalcular import reimportar

    try:
        with _sesion(args) as s:
            r = reimportar(s, args.archivos, conservar=args.conservar)
            s.commit()
    except (ErrorExcel, OSError) as e:
        print(f"ERROR: {e}\nNo se ha cambiado nada.", file=sys.stderr)
        return 1
    for a in r.archivos:
        tipos = ", ".join(f"{n} de {t}" for t, n in a.tipos.most_common())
        print(f"== {a.nombre}: {a.partidas} partidas ({tipos})"
              + (f", {a.omitidas} omitidas" if a.omitidas else ""))
        print(f"   Maestro: {a.guardado.nuevas} nuevas, {a.guardado.actualizadas} actualizadas, "
              f"{a.guardado.sin_cambios} sin cambios")
    if r.modo == "cero":
        print(f"\nDesde cero: quitadas {_miles(r.quitadas.get('bloques', 0))} asignaciones de "
              f"Bloques, {_miles(r.quitadas.get('partidas', 0))} de Partidas y "
              f"{_miles(r.quitadas.get('real', 0))} de Real. En Real quedan "
              f"{_miles(r.quedan_real)} (EXTENSIONES sin calle y EXTENSIONCALLES).")
        if r.copias:
            print("   Copias de seguridad: " + ", ".join(
                f"{_NOMBRE_BASE[b].split(' (')[0]} n.º {i}" for b, i in r.copias.items()))
    for inf in r.informes:
        _informe_recalculo(inf)
    return 0


def cmd_copias(args: argparse.Namespace) -> int:
    from .bd.recalcular import copias

    with _sesion(args) as s:
        lista = copias(s, args.base)
        if not lista:
            print("No hay copias de seguridad.")
        for c in lista:
            print(f"  n.º {c.id:<4} {c.creada_en:%d/%m/%Y %H:%M} · {c.base:<8} · "
                  f"{_miles(c.asignaciones):>6} asignaciones, {_miles(c.bandejas):>8} bandejas"
                  f" · {c.motivo}")
    return 0


def cmd_restaurar_copia(args: argparse.Namespace) -> int:
    from .bd.recalcular import restaurar_copia

    with _sesion(args) as s:
        c, previa = restaurar_copia(s, args.id)
        s.commit()
        print(f"Base {c.base} restaurada como estaba el {c.creada_en:%d/%m/%Y %H:%M} "
              f"({_miles(c.asignaciones)} asignaciones, {_miles(c.bandejas)} bandejas).")
        if previa:
            print(f"Lo que había ({_miles(previa.asignaciones)} asignaciones) está en la copia "
                  f"n.º {previa.id}.")
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

    ce = sub.add_parser("cargar-extensiones",
                        help="Cargar lo colocado (EXTENSIONES o EXTENSIONCALLES + previsión) en Real")
    ce.add_argument("archivos", nargs="+",
                    help="EXTENSIONES.xlsx y/o EXTENSIONCALLES.xlsx (si viene, manda y carga por "
                         "calle) y, si se quiere, Excel de previsión (p. ej. 0110.xlsx)")
    ce.add_argument("--top", type=int, default=10,
                    help="Cuántos destinos no cargados listar")
    ce.add_argument("--sin-reparto", action="store_true",
                    help="Por calle: dejar cada partida entera en su calle (sin repartir)")
    ce.add_argument("--hoy", type=_fecha, help="Por calle: día de la foto (por defecto, hoy)")
    ce.set_defaults(func=cmd_cargar_extensiones)

    cp = sub.add_parser("cargar-pdf",
                        help="Leer PDF de túneles (parte de injerto) o de cargas y guardarlos en Real")
    cp.add_argument("archivos", nargs="+")
    cp.add_argument("--tipo", choices=["tuneles", "cargas"],
                    help="Forzar el tipo (por defecto se reconoce por el contenido)")
    cp.add_argument("--detalle", action="store_true", help="Mostrar partida a partida")
    cp.set_defaults(func=cmd_cargar_pdf)

    rc = sub.add_parser("recalcular",
                        help="Recalcular las tres bases, todas las semanas, con las partidas de hoy")
    rc.set_defaults(func=cmd_recalcular)

    ri = sub.add_parser("reimportar",
                        help="Volver a leer los Excel de previsión y recalcular (por defecto, desde cero)")
    ri.add_argument("archivos", nargs="+", help="Excel de previsión (el más nuevo manda)")
    ri.add_argument("--conservar", action="store_true",
                    help="Conservar las asignaciones y recalcularlas (si no, se vacían Bloques, "
                         "Partidas y lo puesto a mano en Real)")
    ri.set_defaults(func=cmd_reimportar)

    co = sub.add_parser("copias", help="Copias de seguridad de las asignaciones")
    co.add_argument("--base", choices=list(BASES))
    co.set_defaults(func=cmd_copias)

    rs = sub.add_parser("restaurar-copia", help="Dejar una base como estaba en una copia")
    rs.add_argument("id", type=int)
    rs.set_defaults(func=cmd_restaurar_copia)

    tec = sub.add_parser("tecnico",help="Poner o quitar la fecha técnico de una partida")
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
