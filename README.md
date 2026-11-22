![Viveros La Sala](recursos/logo-sala.png)

# Planificador de Ocupación — Viveros La Sala (versión Python)

Reconstrucción en Python del planificador descrito en el *Documento maestro* (21/11/2026).

**Arquitectura acordada (22/11/2026):** aplicación web en Python, base de datos común
para todos, accesible desde fuera de la empresa con usuario y contraseña.

## Estado

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Motor de cálculo: Excel de previsión → partidas, fechas, ECO/BIO, bandejas, calendario | ✅ con pruebas |
| 2 | Base de datos y asignaciones (Bloques, Partidas, Real) | pendiente |
| 3 | Servidor web, usuarios y pantallas | pendiente |

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
```

## Estructura

- `src/planificador/motor/reglas.py` — todas las tasas y reglas confirmadas.
- `src/planificador/motor/clasificacion.py` — tipo de planta, categoría, cultivo, ECO/BIO, estilo, etapa.
- `src/planificador/motor/bandejas.py` — cálculo de bandejas.
- `src/planificador/motor/excel.py` — lectura de los Excel de previsión.
- `src/planificador/motor/calendario.py` — semanas de domingo a sábado.
- `tests/` — casos del §17.1 del documento maestro.
