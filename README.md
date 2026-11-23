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

- **Carga de EXTENSIONES.xlsx en Real** (§10.5), con uno o varios Excel de previsión (p. ej.
  `0110.xlsx`): posición neta por partida e invernadero, salida (técnico → PDF → maestro → previsión
  + 3 → estimada por familia; más de 150 días = dudosa), mitad A/B con menos ocupación, SURINVER y
  túneles fuera. No toca lo hecho a mano en Real y cargar dos veces da lo mismo. Con
  `EXTENSIONES.xlsx` + `0110.xlsx` da las cifras del §17.3: 2.418 movimientos, 2.307 partidas,
  226.165 bandejas netas, **117.042 cargadas en 1.429 asignaciones** y 109.123 sin cargar; el
  21/11/2026 hay unas 95.000 bandejas en 38 invernaderos (INV4A 3.549/3.010, INV37 2.384/1.996).

- **Carga por calle de EXTENSIONCALLES.xlsx en Real** (§10.5b): si entre los archivos viene el
  de calles, manda. Descifra las celdas «Inv-Sec» que Excel convirtió en fecha, busca la calle
  (también con el número del invernadero delante: INV1A 101–130), toma la entrada de EXTENSIONES,
  de lo ya guardado, del maestro o de la siembra (nunca después de hoy), deja la salida en hoy si
  ya pasó, y reparte las partidas grandes entre calles contiguas (estimación; `--sin-reparto` para
  no repartir). Sustituye lo que esas partidas tuvieran en Real. Con `EXTENSIONCALLES.xlsx` +
  `0110.xlsx` cargado tras EXTENSIONES da las cifras del §17.3b: 1.115 partidas / 80.641
  bandejas en calles; 639 / 71.617 fuera del planificador; 28 / 1.707 con calle desconocida; 82 /
  4.222 sin ubicación; entrada 1.073 / 17 / 25; 71 partidas con salida de hoy; reparto de 222
  partidas (39.411 bandejas movidas, 944 sin hueco); 1.686 asignaciones en 814 calles de 36
  invernaderos; 13 calles por encima de su capacidad (+944); Real queda en **114.160 bandejas**.
  Sin reparto: 512 calles, 228 por encima (+40.355), 1.471 asignaciones.
- **PDF de túneles y de cargas** (§12.1–12.2): `planificador cargar-pdf` reconoce el tipo por el
  contenido. Túneles: bandejas reales injertadas, entrada real, calle, entrega, cliente,
  categoría y formato por el grupo, lucha biológica. Cargas: fecha real de salida. Las
  asignaciones que la partida ya tenga en Real toman las fechas nuevas. Para abrir los PDF hace
  falta `pip install -e ".[pdf]"` (pdfplumber). Comprobado con los partes reales de
  `fuentes_de_datos/` (23/11/2026): túneles del 12 al 15 de julio cuadran con el total de cada
  parte (673, 715, 401 y 1.006 bandejas) y cargas_12 / cargas_15 se leen enteros (13 y 34 líneas).
  Se rechazan los PDF sin texto (escaneados) y los listados que no son ninguno de los dos partes
  (p. ej. la hoja de control del técnico); `--tipo` fuerza la lectura.

Quedan para más adelante: «Recalcular todas las semanas» y «Reimportar desde cero / conservar»
(§10.4, §10.6), los checks de calidad (§12.3) y las migraciones de la base de datos (Alembic)
antes de ponerla en común.

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
planificador cargar-extensiones EXTENSIONES.xlsx 0110.xlsx   # lo ya colocado → base Real
planificador cargar-extensiones EXTENSIONCALLES.xlsx 0110.xlsx   # foto por calle → base Real
planificador cargar-pdf 15_julio.pdf cargas_15.pdf --detalle     # partes de túneles y de cargas
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
- `src/planificador/bd/extensiones.py` — carga de EXTENSIONES.xlsx en la base Real.
- `src/planificador/bd/calles.py` — carga por calle de EXTENSIONCALLES.xlsx en la base Real.
- `src/planificador/bd/pdf.py` — PDF de túneles (parte de injerto) y de cargas.
- `src/planificador/datos/invernaderos.json` — invernaderos y calles con su capacidad.
- `tests/` — casos del §17.1 y §17.2 del documento maestro y de la fase 2.
