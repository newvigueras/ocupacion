"""Aplicación web (fase 3): entrar, permisos, pantallas y edición de las rejillas."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from planificador.bd import Planificador, crear_motor, guardar_partidas, preparar_bd
from planificador.cli import main
from planificador.motor import leer_excel
from planificador.web.app import crear_app
from planificador.web.seguridad import (
    ErrorUsuario, autenticar, cifrar_clave, comprobar_clave, crear_usuario,
)

CLAVE = "clave-de-prueba-123"  # solo para estas pruebas


@pytest.fixture
def url(tmp_path, excel_ejemplo):
    url = f"sqlite:///{tmp_path / 'web.db'}"
    motor = crear_motor(url)
    preparar_bd(motor)
    with Session(motor) as s:
        guardar_partidas(s, leer_excel(excel_ejemplo))
        for nombre, rol in (("ana", "admin"), ("pepe", "planificador"), ("luis", "consulta")):
            crear_usuario(s, nombre, CLAVE, rol)
        s.commit()
    return url


@pytest.fixture
def app(url):
    return crear_app(url)


def csrf_de(html: str) -> str:
    return re.search(r'name="csrf" (?:content|value)="([^"]+)"', html).group(1)


def entrar(app, nombre: str) -> tuple[TestClient, str]:
    c = TestClient(app)
    token = csrf_de(c.get("/entrar").text)
    r = c.post("/entrar", data={"nombre": nombre, "clave": CLAVE, "csrf": token},
               follow_redirects=False)
    assert r.status_code == 303
    return c, csrf_de(c.get("/").text)


def test_claves_cifradas():
    h = cifrar_clave("una clave larga")
    assert h.startswith("scrypt$") and "una clave" not in h
    assert comprobar_clave("una clave larga", h) and not comprobar_clave("otra", h)
    assert not comprobar_clave("x", "basura")


def test_usuarios(url):
    with Session(crear_motor(url)) as s:
        with pytest.raises(ErrorUsuario, match="10 caracteres"):
            crear_usuario(s, "corto", "123")
        with pytest.raises(ErrorUsuario, match="Ya existe"):
            crear_usuario(s, "ANA", CLAVE)
        with pytest.raises(ErrorUsuario, match="letras"):
            crear_usuario(s, "con espacio", CLAVE)
        assert autenticar(s, "Ana", CLAVE).rol == "admin"
        assert autenticar(s, "ana", "mala") is None
        assert autenticar(s, "nadie", CLAVE) is None


def test_hay_que_entrar(app):
    c = TestClient(app)
    r = c.get("/global", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/entrar?siguiente=/global"
    pagina = c.get("/entrar")
    assert "logo-sala.png" in pagina.text
    assert pagina.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in pagina.headers["content-security-policy"]
    token = csrf_de(pagina.text)
    mal = c.post("/entrar", data={"nombre": "ana", "clave": "mala", "csrf": token})
    assert mal.status_code == 401 and "incorrectos" in mal.text
    sin_token = c.post("/entrar", data={"nombre": "ana", "clave": CLAVE})
    assert sin_token.status_code == 403
    r = c.post("/entrar", data={"nombre": "ana", "clave": CLAVE, "csrf": token,
                                "siguiente": "//otra-web.com"}, follow_redirects=False)
    assert r.headers["location"] == "/"  # no se va a otra web


def test_resumen_y_rejillas(app):
    c, _ = entrar(app, "luis")
    r = c.get("/?base=partidas&fecha=2026-12-01")
    assert r.status_code == 200 and "Sala 1" in r.text and "Entran el 01/12/2026" in r.text
    assert "INV41" in c.get("/?sala=sala3&inv=INV41&fecha=2026-12-01").text
    g = c.get("/global?semana=49&tipo=injerto")
    assert "Jiffy 8x8" in g.text and "INV41" in g.text and "data-editable=\"\"" in g.text
    p = c.get("/partidas?semana=49")
    assert "26X02589" in p.text
    assert "No hay partidas" in c.get("/exacta?semana=49").text
    assert c.get("/hotel").status_code == 200
    assert c.get("/datos").status_code == 403  # consulta no carga datos


def test_editar_celdas(app, url):
    c, token = entrar(app, "pepe")
    cab = {"X-CSRF": token}
    datos = {"vista": "partidas", "semana": "49", "codigo": "26X02589",
             "invernadero": "INV41", "valor": "5000"}
    r = c.post("/celda", data=datos, headers=cab)
    j = r.json()
    assert r.status_code == 200 and "2.028 bandejas" in j["mensaje"] and "no hay más" in j["mensaje"]
    assert "26X02589" in j["html"]
    with Session(crear_motor(url)) as s:
        assert Planificador(s).asignado("partidas", "26X02589") == 2028

    bloque = {"vista": "global", "semana": "49", "tipo": "injerto", "cultivo": "Jiffy 8x8",
              "categoria": "Jiffy", "bio": "No BIO", "cliente": "CLIENTE PRUEBA",
              "invernadero": "INV42", "valor": "100"}
    assert "100 bandejas" in c.post("/celda", data=bloque, headers=cab).json()["mensaje"]

    t = c.post("/tecnico", data={"vista": "partidas", "semana": "49", "codigo": "26X02589",
                                 "fecha": "2027-01-10"}, headers=cab).json()
    assert "10/01/2027" in t["mensaje"]

    error = c.post("/celda", data={**datos, "invernadero": "INV999"}, headers=cab).json()
    assert error["error"] and "INV999" in error["mensaje"]
    assert c.post("/celda", data=datos).status_code == 403  # sin token
    assert c.post("/celda", data=datos, headers={"X-CSRF": "otro"}).status_code == 403

    luis, token_luis = entrar(app, "luis")
    r = luis.post("/celda", data=datos, headers={"X-CSRF": token_luis})
    assert r.status_code == 403 and "perfil" in r.json()["error"]


def test_datos_y_usuarios(app, tmp_path, excel_ejemplo):
    pepe, token = entrar(app, "pepe")
    assert pepe.get("/datos").status_code == 200
    r = pepe.post("/datos/recalcular", data={"csrf": token})
    assert "nada que recalcular" in r.text
    with open(excel_ejemplo, "rb") as f:
        r = pepe.post("/datos/reimportar", data={"csrf": token, "modo": "cero"},
                      files={"archivos": ("prevision.xlsx", f)})
    assert r.status_code == 403  # reimportar es de admin
    assert pepe.get("/usuarios").status_code == 403

    ana, token = entrar(app, "ana")
    with open(excel_ejemplo, "rb") as f:
        r = ana.post("/datos/reimportar", data={"csrf": token, "modo": "conservar"},
                     files={"archivos": ("prevision.xlsx", f)})
    assert "sin cambios" in r.text
    roto = tmp_path / "roto.pdf"
    roto.write_bytes(b"no es un pdf")
    with open(roto, "rb") as f:
        r = ana.post("/datos/pdf", data={"csrf": token}, files={"archivos": ("roto.pdf", f)})
    assert "roto.pdf" in r.text and "mensaje error" in r.text

    r = ana.post("/usuarios/nuevo", data={"csrf": token, "nombre": "marta", "clave": CLAVE,
                                          "rol": "planificador"})
    assert "Usuario marta creado" in r.text
    r = ana.post("/usuarios/1", data={"csrf": token, "rol": "consulta", "activo": "1"})
    assert "al menos un administrador" in r.text  # ana es la única admin
    r = ana.post("/usuarios/3", data={"csrf": token, "rol": "consulta", "activo": ""})
    assert "luis guardado" in r.text
    luis = TestClient(app)
    t = csrf_de(luis.get("/entrar").text)
    assert luis.post("/entrar", data={"nombre": "luis", "clave": CLAVE, "csrf": t}).status_code == 401


def test_cambiar_mi_clave(app):
    c, token = entrar(app, "luis")
    r = c.post("/cuenta", data={"csrf": token, "actual": "mala", "nueva": "x" * 12,
                                "repetida": "x" * 12})
    assert "no es correcta" in r.text
    r = c.post("/cuenta", data={"csrf": token, "actual": CLAVE, "nueva": "nueva-clave-456",
                                "repetida": "nueva-clave-456"})
    assert "Contraseña cambiada" in r.text
    c.post("/salir", data={"csrf": token})
    assert c.get("/", follow_redirects=False).status_code == 303


def test_cli_usuario(tmp_path, monkeypatch, capsys):
    bd = ["--bd", f"sqlite:///{tmp_path / 'u.db'}"]
    monkeypatch.setenv("PLANIFICADOR_CLAVE", CLAVE)
    assert main(bd + ["usuario", "crear", "Jefe", "--rol", "admin", "--completo", "El Jefe"]) == 0
    assert "Usuario jefe creado (perfil admin)" in capsys.readouterr().out
    assert main(bd + ["usuario", "crear", "jefe"]) == 1
    assert main(bd + ["usuario", "clave", "jefe"]) == 0
    assert main(bd + ["usuario", "lista"]) == 0
    assert "jefe" in capsys.readouterr().out
