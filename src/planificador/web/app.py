"""Aplicación web del planificador (fase 3).

    planificador web                 → http://127.0.0.1:8000

Variables de entorno:

- ``PLANIFICADOR_BD``: base de datos (la misma que usa la consola).
- ``PLANIFICADOR_SECRETO``: clave para firmar las sesiones (si no, se crea una y se guarda
  en la base).
- ``PLANIFICADOR_HTTPS=1``: cookies solo por HTTPS (al publicarla fuera de la empresa).
"""

from __future__ import annotations

import os
import secrets
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from ..bd import BASES, ErrorPlan, Planificador, crear_motor, preparar_bd
from ..bd.tablas import ROLES, Usuario
from ..motor.reglas import TIPOS_PLANTA
from . import datos as D
from . import vistas as V
from .seguridad import (
    ErrorUsuario, admins_activos, autenticar, cambiar_clave, comprobar_clave, crear_usuario,
    hay_usuarios, puede, secreto_sesion,
)

AQUI = Path(__file__).parent
DURACION_SESION = 12 * 3600

VISTAS = [
    ("resumen", "/", "Resumen"),
    ("global", "/global", "Planificación Global"),
    ("partidas", "/partidas", "Planificación por Partida"),
    ("exacta", "/exacta", "Planificación Exacta"),
    ("invernadero", "/invernadero", "Por invernadero"),
    ("semana", "/semana", "Por semana"),
    ("hotel", "/hotel", "Ocupación Hotel"),
]


class NoAutenticado(Exception):
    pass


class SinPermiso(Exception):
    pass


def crear_app(url_bd: str | None = None) -> FastAPI:
    motor = crear_motor(url_bd)
    preparar_bd(motor)
    with Session(motor) as s:
        secreto = os.environ.get("PLANIFICADOR_SECRETO") or secreto_sesion(s)

    app = FastAPI(title="Planificador de Ocupación", docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.motor = motor
    app.add_middleware(SessionMiddleware, secret_key=secreto, session_cookie="planificador",
                       max_age=DURACION_SESION, same_site="lax",
                       https_only=os.environ.get("PLANIFICADOR_HTTPS") == "1")
    app.mount("/estatico", StaticFiles(directory=AQUI / "estatico"), name="estatico")

    plantillas = Jinja2Templates(directory=AQUI / "plantillas")
    plantillas.env.filters["miles"] = D.miles
    plantillas.env.filters["fecha"] = lambda d: f"{d:%d/%m/%Y}" if d else ""
    plantillas.env.filters["corta"] = lambda d: f"{d:%d/%m}" if d else ""
    plantillas.env.globals.update(VISTAS=VISTAS, NOMBRE_BASE=D.NOMBRE_BASE,
                                  NOMBRE_TIPO=D.NOMBRE_TIPO, ROLES=ROLES,
                                  COLOR_CATEGORIA=V.COLOR_CATEGORIA)

    @app.middleware("http")
    async def cabeceras(request: Request, call_next):
        r = await call_next(request)
        r.headers["X-Frame-Options"] = "DENY"
        r.headers["X-Content-Type-Options"] = "nosniff"
        r.headers["Referrer-Policy"] = "same-origin"
        r.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; img-src 'self' data:; "
            # Estilos en línea solo para el largo de las barras de ocupación.
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'self'")
        return r

    @app.exception_handler(NoAutenticado)
    async def _no_autenticado(request: Request, _exc):
        if request.headers.get("x-csrf"):  # petición de la página (fetch)
            return JSONResponse({"error": "La sesión ha caducado: vuelva a entrar."}, 401)
        return RedirectResponse("/entrar?siguiente=" + quote(request.url.path), 303)

    @app.exception_handler(SinPermiso)
    async def _sin_permiso(request: Request, _exc):
        if request.headers.get("x-csrf"):
            return JSONResponse({"error": "Su perfil no permite hacer esto."}, 403)
        return HTMLResponse("<p>Su perfil no permite ver esta página. <a href='/'>Volver</a></p>",
                            403)

    # --- Dependencias -----------------------------------------------------------------

    def sesion_bd() -> Iterator[Session]:
        with Session(app.state.motor) as s:
            yield s

    def usuario_actual(request: Request, s: Session = Depends(sesion_bd)) -> Usuario:
        uid = request.session.get("uid")
        u = s.get(Usuario, uid) if uid else None
        if u is None or not u.activo:
            request.session.clear()
            raise NoAutenticado()
        return u

    def exige(accion: str):
        def _dep(u: Usuario = Depends(usuario_actual)) -> Usuario:
            if not puede(u, accion):
                raise SinPermiso()
            return u
        return _dep

    async def csrf(request: Request) -> None:
        esperado = request.session.get("csrf")
        enviado = request.headers.get("x-csrf")
        if enviado is None:
            enviado = (await request.form()).get("csrf")
        if not esperado or not isinstance(enviado, str) or not secrets.compare_digest(
                esperado, enviado):
            raise SinPermiso()

    def token(request: Request) -> str:
        if "csrf" not in request.session:
            request.session["csrf"] = secrets.token_urlsafe(32)
        return request.session["csrf"]

    def avisar(request: Request, texto: str, tipo: str = "ok") -> None:
        request.session.setdefault("mensajes", []).append([tipo, texto])

    def pagina(request: Request, plantilla: str, u: Usuario | None, vista: str = "",
               status: int = 200, **ctx) -> Response:
        ctx.update(usuario=u, csrf=token(request), vista=vista,
                   puede=(lambda accion: puede(u, accion)),
                   mensajes=request.session.pop("mensajes", []))
        return plantillas.TemplateResponse(request, plantilla, ctx, status_code=status)

    def a_entero(texto: str | None, defecto: int | None = None) -> int | None:
        try:
            return int(texto) if texto not in (None, "") else defecto
        except ValueError:
            return defecto

    def a_fecha(texto: str | None) -> date | None:
        try:
            return date.fromisoformat(texto) if texto else None
        except ValueError:
            return None

    # --- Entrar y salir ---------------------------------------------------------------

    @app.get("/entrar")
    def entrar(request: Request, s: Session = Depends(sesion_bd), siguiente: str = "/"):
        if request.session.get("uid"):
            return RedirectResponse("/", 303)
        return pagina(request, "entrar.html", None, siguiente=siguiente,
                      sin_usuarios=not hay_usuarios(s))

    @app.post("/entrar", dependencies=[Depends(csrf)])
    def entrar_post(request: Request, nombre: str = Form(""), clave: str = Form(""),
                    siguiente: str = Form("/"), s: Session = Depends(sesion_bd)):
        u = autenticar(s, nombre, clave)
        if u is None:
            time.sleep(0.5)  # frena a quien prueba contraseñas
            return pagina(request, "entrar.html", None, siguiente=siguiente, nombre=nombre,
                          error="Usuario o contraseña incorrectos.", status=401,
                          sin_usuarios=not hay_usuarios(s))
        s.commit()
        request.session.clear()
        request.session.update(uid=u.id, csrf=secrets.token_urlsafe(32))
        destino = siguiente if siguiente.startswith("/") and not siguiente.startswith("//") \
            else "/"
        return RedirectResponse(destino, 303)

    @app.post("/salir", dependencies=[Depends(csrf)])
    def salir(request: Request):
        request.session.clear()
        return RedirectResponse("/entrar", 303)

    # --- Resumen ------------------------------------------------------------------------

    def base_elegida(request: Request, base: str | None) -> str:
        """La base de las pantallas de consulta: se recuerda la última que se eligió."""
        if base in BASES:
            request.session["base"] = base
            return base
        guardada = request.session.get("base")
        return guardada if guardada in BASES else "partidas"

    @app.get("/")
    def resumen(request: Request, base: str | None = None, fecha: str | None = None,
                sala: str | None = None, inv: str | None = None,
                u: Usuario = Depends(exige("ver")), s: Session = Depends(sesion_bd)):
        plan = Planificador(s)
        base = base_elegida(request, base)
        dia = a_fecha(fecha) or D.dia_por_defecto(plan)
        if not plan.calendario.contiene(dia):
            dia = D.dia_por_defecto(plan)
        salas = D.resumen(plan, base, dia)
        detalle = None
        if inv:
            try:
                detalle = D.calles_de(plan, base, inv, dia)
            except ErrorPlan:
                detalle = None
        entran, salen = D.movimientos_del_dia(plan, dia)
        return pagina(request, "resumen.html", u, "resumen", base=base, dia=dia, salas=salas,
                      sala=sala, detalle=detalle, entran=entran, salen=salen,
                      semana=plan.calendario.semana(plan.calendario.semana_de(dia)))

    # --- Rejillas -----------------------------------------------------------------------

    def _semana(plan: Planificador, texto: str | None) -> int:
        n = a_entero(texto, D.semana_por_defecto(plan))
        return min(max(n, 1), plan.calendario.n_semanas)

    def _rejilla(plan: Planificador, vista: str, semana: int, tipo: str | None,
                 sala: str | None) -> D.Rejilla:
        if vista == "global":
            return D.rejilla_bloques(plan, semana, tipo or "injerto", sala)
        if vista == "partidas":
            return D.rejilla_partidas(plan, semana, tipo, sala)
        return D.rejilla_real(plan, semana, sala)

    def vista_rejilla(vista: str):
        def _vista(request: Request, semana: str | None = None, tipo: str | None = None,
                   sala: str | None = None, u: Usuario = Depends(exige("ver")),
                   s: Session = Depends(sesion_bd)):
            plan = Planificador(s)
            n = _semana(plan, semana)
            if vista == "global":
                tipo = tipo if tipo in TIPOS_PLANTA else "injerto"
            else:
                tipo = tipo if tipo in TIPOS_PLANTA else None
            r = _rejilla(plan, vista, n, tipo, sala or None)
            return pagina(request, "planificacion.html", u, vista, r=r, sala=sala or "",
                          salas=D.salas(plan), n_semanas=plan.calendario.n_semanas,
                          tipos=TIPOS_PLANTA, cuentas=D.cuenta_bases(plan))
        return _vista

    for _v, _ruta, _ in VISTAS[1:4]:
        app.add_api_route(_ruta, vista_rejilla(_v), methods=["GET"])

    def responder_rejilla(request: Request, plan: Planificador, vista: str, semana: int,
                          tipo: str | None, sala: str | None, mensaje: str,
                          error: bool = False) -> JSONResponse:
        r = _rejilla(plan, vista, semana, tipo, sala)
        html = plantillas.get_template("_rejilla.html").render(
            r=r, vista=vista, puede=lambda a: True, csrf=token(request))
        return JSONResponse({"html": html, "mensaje": mensaje, "error": error})

    @app.post("/celda", dependencies=[Depends(csrf)])
    async def celda(request: Request, u: Usuario = Depends(exige("asignar")),
                    s: Session = Depends(sesion_bd)):
        f = await request.form()
        vista = f.get("vista", "")
        plan = Planificador(s)
        semana = _semana(plan, f.get("semana"))
        tipo = f.get("tipo") or None
        sala = f.get("sala") or None
        valor = max(0, a_entero(f.get("valor"), 0) or 0)
        inv = f.get("invernadero", "")
        try:
            if vista == "global":
                clave = (f.get("cultivo", ""), f.get("categoria", ""), f.get("bio", ""),
                         f.get("cliente", ""))
                puesto = plan.asignar_bloque(semana, tipo or "injerto", clave, inv, valor)
                que = f"Bloque {f.get('cultivo')}"
            elif vista in ("partidas", "exacta"):
                base = "partidas" if vista == "partidas" else "real"
                puesto = plan.asignar_partida(base, f.get("codigo", ""), inv, valor)
                que = f.get("codigo", "")
            else:
                raise ErrorPlan("Pantalla desconocida")
        except ErrorPlan as e:
            s.rollback()
            return responder_rejilla(request, plan, vista, semana, tipo, sala, str(e), True)
        s.commit()
        aviso = "" if puesto == valor else f" (pedidas {D.miles(valor)}: no hay más libres)"
        return responder_rejilla(request, plan, vista, semana, tipo, sala,
                                 f"✓ {que} → {inv}: {D.miles(puesto)} bandejas{aviso}")

    @app.post("/tecnico", dependencies=[Depends(csrf)])
    async def tecnico(request: Request, u: Usuario = Depends(exige("asignar")),
                      s: Session = Depends(sesion_bd)):
        f = await request.form()
        plan = Planificador(s)
        vista = f.get("vista", "partidas")
        semana = _semana(plan, f.get("semana"))
        codigo = f.get("codigo", "")
        fecha = a_fecha(f.get("fecha"))
        n = plan.poner_fecha_tecnico(codigo, fecha)
        s.commit()
        texto = f"{fecha:%d/%m/%Y}" if fecha else "quitada"
        return responder_rejilla(request, plan, vista, semana, f.get("tipo") or None,
                                 f.get("sala") or None,
                                 f"✓ Fecha técnico de {codigo}: {texto} ({n} asignaciones)")

    # --- Reparto de una partida por calles --------------------------------------------

    BASE_DE_VISTA = {"partidas": "partidas", "exacta": "real"}

    @app.get("/calles")
    def calles(request: Request, vista: str = "", codigo: str = "", inv: str = "",
               u: Usuario = Depends(exige("asignar")), s: Session = Depends(sesion_bd)):
        plan = Planificador(s)
        try:
            if vista not in BASE_DE_VISTA:
                raise ErrorPlan("Las calles se reparten en Planificación por Partida o Exacta")
            if plan.invernadero(inv).especial:
                raise ErrorPlan(f"{inv} no tiene calles")
            r = D.reparto_calles(plan, BASE_DE_VISTA[vista], codigo, inv)
        except ErrorPlan as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        html = plantillas.get_template("_calles.html").render(r=r, NOMBRE_BASE=D.NOMBRE_BASE)
        return JSONResponse({"html": html})

    @app.post("/calles", dependencies=[Depends(csrf)])
    async def calles_post(request: Request, u: Usuario = Depends(exige("asignar")),
                          s: Session = Depends(sesion_bd)):
        f = await request.form()
        vista = f.get("vista", "")
        plan = Planificador(s)
        semana = _semana(plan, f.get("semana"))
        tipo = f.get("tipo") or None
        sala = f.get("sala") or None
        codigo = f.get("codigo", "")
        inv = f.get("invernadero", "")
        try:
            if vista not in BASE_DE_VISTA:
                raise ErrorPlan("Las calles se reparten en Planificación por Partida o Exacta")
            reparto = {}
            for k, v in f.items():
                if k.startswith("calle_") and str(v).strip():
                    n, valor = a_entero(k[6:]), a_entero(str(v).replace(".", ""))
                    if n is None or valor is None or valor < 0:
                        raise ErrorPlan("Escriba números enteros de bandejas")
                    reparto[n] = valor
            puestas, sin_calle = plan.asignar_calles(BASE_DE_VISTA[vista], codigo, inv, reparto)
        except ErrorPlan as e:
            s.rollback()
            return responder_rejilla(request, plan, vista, semana, tipo, sala, str(e), True)
        s.commit()
        pedidas = sum(reparto.values())
        en_calles = sum(puestas.values())
        texto = (f"✓ {codigo.strip().upper()} → {inv}: {D.miles(en_calles)} bandejas en "
                 f"{len(puestas)} calles")
        if sin_calle:
            texto += f" y {D.miles(sin_calle)} sin calle"
        if en_calles < pedidas:
            texto += f" (pedidas {D.miles(pedidas)}: no hay más libres)"
        return responder_rejilla(request, plan, vista, semana, tipo, sala, texto)

    # --- Consultas: Por invernadero, Por semana y Hotel -----------------------------------

    def _periodo(plan: Planificador, desde: str | None, hasta: str | None) -> V.Periodo:
        return V.periodo(plan, a_entero(desde), a_entero(hasta))

    def _inv(plan: Planificador, nombre: str | None) -> str | None:
        if not nombre:
            return None
        try:
            return plan.invernadero(nombre).nombre
        except ErrorPlan:
            return None

    @app.get("/invernadero")
    def por_invernadero(request: Request, base: str | None = None, sala: str | None = None,
                        desde: str | None = None, hasta: str | None = None,
                        abrir: str | None = None, u: Usuario = Depends(exige("ver")),
                        s: Session = Depends(sesion_bd)):
        plan = Planificador(s)
        base = base_elegida(request, base)
        p = _periodo(plan, desde, hasta)
        lista = V.graficos(plan, base, p, sala or None)
        return pagina(request, "invernadero.html", u, "invernadero", base=base, p=p,
                      sala=sala or "", salas=D.salas(plan), graficos=lista,
                      abrir=_inv(plan, abrir), semanas=plan.calendario.semanas())

    @app.get("/semana")
    def por_semana(request: Request, base: str | None = None, semana: str | None = None,
                   agrupar: str = "inv", inv: str | None = None,
                   u: Usuario = Depends(exige("ver")), s: Session = Depends(sesion_bd)):
        plan = Planificador(s)
        base = base_elegida(request, base)
        agrupar = agrupar if agrupar in ("inv", "cultivo") else "inv"
        inv = _inv(plan, inv)
        v = V.vista_semana(plan, base, _semana(plan, semana), agrupar, inv)
        return pagina(request, "semana.html", u, "semana", base=base, v=v, agrupar=agrupar,
                      inv=inv or "", n_semanas=plan.calendario.n_semanas)

    @app.get("/hotel")
    def hotel(request: Request, base: str | None = None, sala: str | None = None,
              inv: str | None = None, desde: str | None = None, hasta: str | None = None,
              fecha: str | None = None, u: Usuario = Depends(exige("ver")),
              s: Session = Depends(sesion_bd)):
        plan = Planificador(s)
        base = base_elegida(request, base)
        p = _periodo(plan, desde, hasta)
        inv = _inv(plan, inv)
        g = V.gantt(plan, base, p, sala or None, inv)
        dia = a_fecha(fecha)
        if dia is None or not p.contiene(dia):
            dia = date.today() if p.contiene(date.today()) else p.inicio
        i = (dia - p.inicio).days
        return pagina(request, "hotel.html", u, "hotel", base=base, p=p, g=g, dia=dia,
                      i_dia=i, sala=sala or "", inv=inv or "", salas=D.salas(plan),
                      semanas=plan.calendario.semanas())

    # --- Datos: cargar, recalcular, reimportar, copias --------------------------------------

    @app.get("/datos")
    def datos(request: Request, u: Usuario = Depends(exige("asignar")),
              s: Session = Depends(sesion_bd)):
        from ..bd.recalcular import copias
        plan = Planificador(s)
        return pagina(request, "datos.html", u, "datos", cuentas=D.cuenta_bases(plan),
                      copias=copias(s)[:30])

    def guardar_subidos(carpeta: str, archivos: list[UploadFile]) -> list[Path]:
        rutas = []
        for n, a in enumerate(archivos):
            nombre = Path(a.filename or f"archivo{n}").name
            ruta = Path(carpeta) / nombre
            ruta.write_bytes(a.file.read())
            # El orden en que se eligen manda (el último es «el más nuevo»).
            os.utime(ruta, (1_700_000_000 + n, 1_700_000_000 + n))
            rutas.append(ruta)
        return rutas

    @app.post("/datos/extensiones", dependencies=[Depends(csrf)])
    def datos_extensiones(request: Request, archivos: list[UploadFile],
                          u: Usuario = Depends(exige("asignar")),
                          s: Session = Depends(sesion_bd)):
        from ..bd.calles import cargar_por_calles
        from ..bd.extensiones import cargar_extensiones, clasificar_archivos
        with tempfile.TemporaryDirectory() as carpeta:
            try:
                arch = clasificar_archivos(guardar_subidos(carpeta, archivos))
                if arch.calles:
                    r = cargar_por_calles(s, arch)
                    texto = (f"Carga por calle: {D.miles(r.partidas_cargadas)} partidas, "
                             f"{D.miles(r.bandejas_cargadas)} bandejas en "
                             f"{D.miles(r.asignaciones)} asignaciones")
                else:
                    r = cargar_extensiones(s, arch)
                    texto = (f"EXTENSIONES: {D.miles(r.bandejas_cargadas)} bandejas cargadas en "
                             f"{D.miles(r.asignaciones)} asignaciones")
                s.commit()
                avisar(request, "✓ " + texto)
            except Exception as e:  # noqa: BLE001 - se informa y no se cambia nada
                s.rollback()
                avisar(request, f"No se ha cargado nada: {e}", "error")
        return RedirectResponse("/datos", 303)

    @app.post("/datos/pdf", dependencies=[Depends(csrf)])
    def datos_pdf(request: Request, archivos: list[UploadFile],
                  u: Usuario = Depends(exige("asignar")), s: Session = Depends(sesion_bd)):
        from ..bd.pdf import ErrorPdf, cargar_pdf
        with tempfile.TemporaryDirectory() as carpeta:
            for ruta in guardar_subidos(carpeta, archivos):
                try:
                    r = cargar_pdf(s, ruta)
                    s.commit()
                    que = "parte de carga" if r.tipo == "cargas" else "parte de injerto"
                    avisar(request, f"✓ {r.archivo} ({que}): {len(r.partidas)} partidas, "
                                    f"{D.miles(r.bandejas)} bandejas")
                except (ErrorPdf, OSError) as e:
                    s.rollback()
                    avisar(request, f"{ruta.name}: {e}", "error")
        return RedirectResponse("/datos", 303)

    @app.post("/datos/recalcular", dependencies=[Depends(csrf)])
    def datos_recalcular(request: Request, u: Usuario = Depends(exige("asignar")),
                         s: Session = Depends(sesion_bd)):
        from ..bd.recalcular import recalcular_todo
        informes = recalcular_todo(s)
        s.commit()
        for inf in informes:
            if inf.vacia:
                continue
            avisar(request, f"✓ {D.NOMBRE_BASE[inf.base]}: {D.miles(inf.bandejas_antes)} → "
                            f"{D.miles(inf.bandejas_despues)} bandejas"
                            + (f", {D.miles(inf.recortadas)} recortadas" if inf.recortadas else "")
                            + (f", {inf.huerfanas} trozos huérfanos" if inf.huerfanas else "")
                            + (f" (copia n.º {inf.copia_id})" if inf.copia_id else ""))
        if all(i.vacia for i in informes):
            avisar(request, "No hay asignaciones: no hay nada que recalcular.")
        return RedirectResponse("/datos", 303)

    @app.post("/datos/reimportar", dependencies=[Depends(csrf)])
    def datos_reimportar(request: Request, archivos: list[UploadFile],
                         modo: str = Form("cero"), u: Usuario = Depends(exige("administrar")),
                         s: Session = Depends(sesion_bd)):
        from ..bd.recalcular import reimportar
        from ..motor import ErrorExcel
        with tempfile.TemporaryDirectory() as carpeta:
            try:
                r = reimportar(s, guardar_subidos(carpeta, archivos),
                               conservar=(modo == "conservar"))
                s.commit()
            except (ErrorExcel, OSError) as e:
                s.rollback()
                avisar(request, f"No se ha cambiado nada: {e}", "error")
                return RedirectResponse("/datos", 303)
        for a in r.archivos:
            avisar(request, f"✓ {a.nombre}: {a.guardado.nuevas} nuevas, "
                            f"{a.guardado.actualizadas} actualizadas, "
                            f"{a.guardado.sin_cambios} sin cambios")
        if r.modo == "cero":
            avisar(request, f"Desde cero: quitadas {r.quitadas.get('bloques', 0)} de Bloques, "
                            f"{r.quitadas.get('partidas', 0)} de Partidas y "
                            f"{r.quitadas.get('real', 0)} de Real (con copia).")
        for inf in r.informes:
            avisar(request, f"✓ Recalculada {D.NOMBRE_BASE[inf.base]}: "
                            f"{D.miles(inf.bandejas_antes)} → {D.miles(inf.bandejas_despues)}")
        return RedirectResponse("/datos", 303)

    @app.post("/datos/restaurar", dependencies=[Depends(csrf)])
    def datos_restaurar(request: Request, copia: int = Form(...),
                        u: Usuario = Depends(exige("administrar")),
                        s: Session = Depends(sesion_bd)):
        from ..bd.recalcular import restaurar_copia
        try:
            c, previa = restaurar_copia(s, copia)
            s.commit()
            avisar(request, f"✓ Base {D.NOMBRE_BASE[c.base]} restaurada como estaba el "
                            f"{c.creada_en:%d/%m/%Y %H:%M}"
                            + (f" (lo que había está en la copia n.º {previa.id})" if previa else ""))
        except ErrorPlan as e:
            avisar(request, str(e), "error")
        return RedirectResponse("/datos", 303)

    # --- Usuarios y cuenta -------------------------------------------------------------------

    @app.get("/usuarios")
    def usuarios(request: Request, u: Usuario = Depends(exige("administrar")),
                 s: Session = Depends(sesion_bd)):
        lista = list(s.scalars(select(Usuario).order_by(Usuario.nombre)))
        return pagina(request, "usuarios.html", u, "usuarios", lista=lista)

    @app.post("/usuarios/nuevo", dependencies=[Depends(csrf)])
    def usuario_nuevo(request: Request, nombre: str = Form(...), clave: str = Form(...),
                      rol: str = Form("consulta"), nombre_completo: str = Form(""),
                      u: Usuario = Depends(exige("administrar")),
                      s: Session = Depends(sesion_bd)):
        try:
            nuevo = crear_usuario(s, nombre, clave, rol, nombre_completo)
            s.commit()
            avisar(request, f"✓ Usuario {nuevo.nombre} creado ({nuevo.rol})")
        except ErrorUsuario as e:
            s.rollback()
            avisar(request, str(e), "error")
        return RedirectResponse("/usuarios", 303)

    @app.post("/usuarios/{uid}", dependencies=[Depends(csrf)])
    def usuario_cambiar(request: Request, uid: int, rol: str = Form(...),
                        activo: str = Form(""), clave: str = Form(""),
                        u: Usuario = Depends(exige("administrar")),
                        s: Session = Depends(sesion_bd)):
        otro = s.get(Usuario, uid)
        if otro is None or rol not in ROLES:
            avisar(request, "Usuario o perfil desconocido", "error")
            return RedirectResponse("/usuarios", 303)
        activo_nuevo = activo == "1"
        quita_admin = otro.rol == "admin" and otro.activo and (rol != "admin" or not activo_nuevo)
        if quita_admin and admins_activos(s) <= 1:
            avisar(request, "Tiene que quedar al menos un administrador activo", "error")
            return RedirectResponse("/usuarios", 303)
        try:
            if clave:
                cambiar_clave(otro, clave)
            otro.rol, otro.activo = rol, activo_nuevo
            s.commit()
            avisar(request, f"✓ Usuario {otro.nombre} guardado"
                            + (" (contraseña cambiada)" if clave else ""))
        except ErrorUsuario as e:
            s.rollback()
            avisar(request, str(e), "error")
        return RedirectResponse("/usuarios", 303)

    @app.get("/cuenta")
    def cuenta(request: Request, u: Usuario = Depends(exige("ver"))):
        return pagina(request, "cuenta.html", u, "cuenta")

    @app.post("/cuenta", dependencies=[Depends(csrf)])
    def cuenta_post(request: Request, actual: str = Form(""), nueva: str = Form(""),
                    repetida: str = Form(""), u: Usuario = Depends(exige("ver")),
                    s: Session = Depends(sesion_bd)):
        if not comprobar_clave(actual, u.clave_hash):
            avisar(request, "La contraseña actual no es correcta", "error")
        elif nueva != repetida:
            avisar(request, "Las dos contraseñas nuevas no coinciden", "error")
        else:
            try:
                cambiar_clave(u, nueva)
                s.commit()
                avisar(request, "✓ Contraseña cambiada")
            except ErrorUsuario as e:
                avisar(request, str(e), "error")
        return RedirectResponse("/cuenta", 303)

    return app


def app_por_defecto() -> FastAPI:
    """Para ``uvicorn --factory planificador.web.app:app_por_defecto``."""
    return crear_app()
