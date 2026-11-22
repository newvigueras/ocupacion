![Viveros La Sala](recursos/logo-sala.png)

# Planificador de Ocupación — Viveros La Sala (versión Python)

Reconstrucción en Python del planificador descrito en el *Documento maestro* (21/11/2026).

**Arquitectura acordada (22/11/2026):** aplicación web en Python, base de datos común
para todos, accesible desde fuera de la empresa con usuario y contraseña.

## Estado

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Motor de cálculo: Excel de previsión → partidas, fechas, ECO/BIO, bandejas, calendario | ✅ con pruebas |
| 2 | Base de datos y asignaciones (Bloques, Partidas, Real), ocupación diaria y avisos | ✅ con pruebas |
| 3 | Servidor web, usuarios y pantallas | pendiente |

### Fase 2: qué incluye

- **Base de datos** con SQLAlchemy. Por defecto, un archivo SQLite (`planificador.db`); para la
  base común se apunta a PostgreSQL con la variable `PLANIFICADOR_BD`, sin cambiar el código.
- **40 invernaderos y 1.250 calles** (150.119 bandejas, §8) más el destino especial **BASURA**,
  cargados de `src/planificador/datos/invernaderos.json` (sacado del planificador HTML del 21/11).
- **Partidas** guardadas desde los Excel, fusionando por código (las que no vienen se mantienen).
- **Tres bases independientes** de asignaciones: Bloques (§10.1–10.2: el bloque se reparte partida a
  partida por liberación y código), Partidas (con o sin calle) y Real (solo partidas con dato real).
- **Fecha técnico** por partida, que manda sobre la liberación en las tres bases.
- **Ocupación diaria** (entrada y liberación incluidas) y, en Bloques y Partidas, **lo ya colocado**
  (Real), sustituido bandeja a bandeja por lo asignado de la misma partida (§10.3 3b).
- **Avisos**: invernaderos por encima de su capacidad (se permite) y cultivos distintos en el mismo
  invernadero (Jiffy 8x8 y 5x5 cuentan como uno).
- **Máximo asignable** de una partida: lo que cabe el peor día de toda su estancia.

Quedan para más adelante: cargar EXTENSIONES / EXTENSIONCALLES y los PDF en Real, «Recalcular
todas las semanas» y «Reimportar desde cero / conservar» (§10.4–10.6), y las migraciones de la
base de datos (Alembic) antes de ponerla en común.

## Decisiones nuevas respecto al documento maestro

| Fecha | Decisión |
|---|---|
| 22/11/2026 | Calabacín **CCH5** = bandeja de extensión abierta, Pl sol ÷ 84. |
| 22/11/2026 | **TIHI** sigue con la columna Band hasta confirmar la medida. |
| 22/11/2026 | **Lista editable de excepciones por partida** (código → plantas por bandeja), que manda sobre cualquier otra regla. Empieza con 26X03025 y 26X02999 a 15. |
| 22/11/2026 | Solo se importan los **códigos conocidos** (26X, 26YE, 26F, numéricos, 26EC, 26P, 26C, 26WE, 26VE). H26/…, B26/… (no van a extensión), 23A…, 26KC…, 26ZE… se ignoran de momento y quedan en los avisos. |
| 22/11/2026 | Calabacín: solo el tipo CCH5 va a 84; los demás tipos, con Band. |
| 22/11/2026 | Pepino Jiffy **PI35** = Pl sol ÷ **15** (antes 18), lleve o no «JIF» la variedad. |
| 22/11/2026 | **Calabacín** tiene línea propia en el resumen por cultivo (antes iba en «Otros»). |
| 22/11/2026 | Jiffy desarrollada (tallos/brazos) e hidro adelantada van a 15 aunque no se conozca la medida. |

## Uso

```bash
pip install -e ".[dev]"
python -m pytest
planificador importar 201126.xlsx --partida 26X02589

# Base de datos (fase 2)
planificador preparar                                # crea planificador.db con invernaderos y calles
planificador importar 201126.xlsx --guardar          # guarda las partidas
planificador semana 49                               # bloques que entran la semana 49 (49/26)
planificador asignar-bloque 49 1 INV41 500           # bloque n.º 1 → INV41 (base Bloques)
planificador asignar-partida 26X02589 INV41 300 --calle 3
planificador tecnico 26X02589 2027-01-10             # fecha técnico («quitar» para borrarla)
planificador ocupacion --base partidas --fecha 2026-12-01
```

## Estructura

- `src/planificador/motor/reglas.py` — todas las tasas y reglas confirmadas.
- `src/planificador/motor/clasificacion.py` — tipo de planta, categoría, cultivo, ECO/BIO, estilo, etapa.
- `src/planificador/motor/bandejas.py` — cálculo de bandejas.
- `src/planificador/motor/excel.py` — lectura de los Excel de previsión.
- `src/planificador/motor/calendario.py` — semanas de domingo a sábado.
- `src/planificador/bd/tablas.py` — tablas: invernaderos, calles, partidas, fechas técnico, datos reales y asignaciones.
- `src/planificador/bd/conexion.py` — conexión y preparación de la base de datos.
- `src/planificador/bd/importar.py` — guardar en la base las partidas leídas de los Excel.
- `src/planificador/bd/planificacion.py` — bloques, asignaciones, ocupación y avisos.
- `src/planificador/datos/invernaderos.json` — invernaderos y calles con su capacidad.
- `tests/` — casos del §17.1 y §17.2 del documento maestro y de la fase 2.
