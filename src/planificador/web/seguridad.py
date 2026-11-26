"""Usuarios y contraseñas de la aplicación web.

Las contraseñas se guardan con **scrypt** (biblioteca estándar de Python), con sal propia
por usuario; nunca en claro.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..bd.tablas import ROLES, Ajuste, Usuario

_N, _R, _P, _LARGO = 2**14, 8, 1, 32
LARGO_MINIMO = 10


class ErrorUsuario(ValueError):
    pass


def cifrar_clave(clave: str) -> str:
    sal = secrets.token_bytes(16)
    h = hashlib.scrypt(clave.encode("utf-8"), salt=sal, n=_N, r=_R, p=_P, dklen=_LARGO)
    return f"scrypt${_N}${_R}${_P}${sal.hex()}${h.hex()}"


def comprobar_clave(clave: str, cifrada: str) -> bool:
    try:
        tipo, n, r, p, sal, h = cifrada.split("$")
        if tipo != "scrypt":
            return False
        calculada = hashlib.scrypt(clave.encode("utf-8"), salt=bytes.fromhex(sal), n=int(n),
                                   r=int(r), p=int(p), dklen=len(bytes.fromhex(h)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calculada.hex(), h)


#: Para gastar el mismo tiempo cuando el usuario no existe (no se nota si existe o no).
_CLAVE_FALSA = cifrar_clave(secrets.token_urlsafe(16))


def validar_clave(clave: str) -> None:
    if len(clave) < LARGO_MINIMO:
        raise ErrorUsuario(f"La contraseña debe tener al menos {LARGO_MINIMO} caracteres")


def normalizar_nombre(nombre: str) -> str:
    nombre = nombre.strip().lower()
    if not nombre or len(nombre) > 64 or not all(c.isalnum() or c in "._-" for c in nombre):
        raise ErrorUsuario("El nombre de usuario solo puede llevar letras, números, «.», «_» y «-»")
    return nombre


def crear_usuario(s: Session, nombre: str, clave: str, rol: str = "consulta",
                  nombre_completo: str = "") -> Usuario:
    nombre = normalizar_nombre(nombre)
    if rol not in ROLES:
        raise ErrorUsuario(f"Perfil desconocido: {rol} (puede ser {', '.join(ROLES)})")
    validar_clave(clave)
    if s.scalar(select(Usuario).where(Usuario.nombre == nombre)):
        raise ErrorUsuario(f"Ya existe el usuario {nombre}")
    u = Usuario(nombre=nombre, clave_hash=cifrar_clave(clave), rol=rol,
                nombre_completo=nombre_completo.strip())
    s.add(u)
    s.flush()
    return u


def cambiar_clave(u: Usuario, clave: str) -> None:
    validar_clave(clave)
    u.clave_hash = cifrar_clave(clave)


def autenticar(s: Session, nombre: str, clave: str) -> Usuario | None:
    try:
        nombre = normalizar_nombre(nombre)
    except ErrorUsuario:
        comprobar_clave(clave, _CLAVE_FALSA)
        return None
    u = s.scalar(select(Usuario).where(Usuario.nombre == nombre))
    if u is None or not u.activo:
        comprobar_clave(clave, _CLAVE_FALSA)
        return None
    if not comprobar_clave(clave, u.clave_hash):
        return None
    u.ultimo_acceso = datetime.now()
    return u


def hay_usuarios(s: Session) -> bool:
    return bool(s.scalar(select(func.count(Usuario.id))))


def admins_activos(s: Session) -> int:
    return s.scalar(select(func.count(Usuario.id)).where(Usuario.rol == "admin",
                                                          Usuario.activo.is_(True)))


def secreto_sesion(s: Session) -> str:
    """Clave para firmar las sesiones: se crea una vez y se guarda en la base."""
    a = s.get(Ajuste, "secreto_sesion")
    if a is None:
        a = Ajuste(clave="secreto_sesion", valor=secrets.token_urlsafe(48))
        s.add(a)
        s.commit()
    return a.valor


def puede(u: Usuario | None, accion: str) -> bool:
    """'ver' (todos), 'asignar' (planificador y admin) o 'administrar' (admin)."""
    if u is None or not u.activo:
        return False
    nivel = ROLES.index(u.rol) if u.rol in ROLES else 0
    return nivel >= {"ver": 0, "asignar": 1, "administrar": 2}[accion]
