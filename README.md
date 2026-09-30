# Rivendel · Payoff Lab

Laboratorio de payoffs de opciones con datos reales de mercado, corriendo entero
en el teléfono. Servidor en Python stdlib + frontend propio, enchufado a la
cadena de opciones real vía **yfinance** (con respaldo a la API pública de Yahoo).

Inspirado en el lab de payoff de Atlas (atlas-l3.vercel.app); frontend
reimplementado desde cero.

## Arranque

```bash
bash run.sh
```

y abrí `http://localhost:8765`. En Termux, si tenés `termux-api` instalado, el
navegador se abre solo.

Variantes:

```bash
PORT=9000 bash run.sh              # otro puerto
HOST=0.0.0.0 bash run.sh           # accesible desde otra máquina de la LAN
python3 server.py --port 8765      # sin el wrapper
```

## Instalación en Termux

```bash
pkg install python
pkg install numpy                  # pandas/numpy compilados: no los buildees con pip
pip install yfinance
```

Si `yfinance` no llega a instalarse (en Termux, `pandas` es el que suele pelear),
**el lab funciona igual**: el servidor cae solo a la API pública de Yahoo por
`urllib`, que no necesita ninguna dependencia. Se ve cuál proveedor contestó en
la barra de estado y en `/api/health`.

Sin red, el lab arranca en **modo manual**: subyacente en 100, strikes y primas
a mano.

## Qué hace

Panel **Mercado**: ticker (NVDA / SOXL / SOXS a un toque, o el que escribas),
vencimiento listado, spot, variación del día, IV ATM, volatilidad realizada 20d.

Con la cadena cargada:

- Los **strikes** de cada pata pasan a ser un desplegable de los strikes que
  realmente cotizan en ese vencimiento.
- La **prima** se toma de la cadena: mid entre bid y ask, o —si apretás
  `prima: te cruzan`— el ask cuando comprás y el bid cuando vendés, que es lo
  que te va a pasar de verdad.
- Debajo de cada pata aparecen bid/ask, IV, open interest y volumen del día.
  Si el strike no tiene two-sided market, lo dice.
- Los **presets** se reescalan al spot real (un "bull call spread" escrito para
  un subyacente en 100 se convierte en 215/235 sobre NVDA) y se pegan al strike
  listado más cercano.
- `×100 por contrato` pasa todos los importes de por-acción a por-contrato.
- `zona ±1σ` pone la zona esperada en spot ± IV_ATM × √(días/365).

## Black-Scholes · griegas y P&L antes del vencimiento

El payoff al vencimiento no dice nada de lo que pasa *antes*. El panel
**Black-Scholes** agrega esa capa, calculada en el navegador y recalculada en
vivo con los sliders:

- La curva **punteada ámbar** sobre el gráfico es el P&L marcado a modelo **hoy**
  (o al horizonte que elijas). La distancia contra la silueta del vencimiento es
  el valor tiempo todavía no realizado.
- **Griegas de la posición** y pata por pata: delta, gamma, theta (por día),
  vega (por punto de IV) y rho (por 1% de tasa). En `×100` van por contrato.
- La **IV de cada pata** sale por defecto de una **superficie SVI arbitrage-free**
  (Gatheral) ajustada a la cadena: se extrae el **forward implícito** por paridad
  put-call, se reinvierte la **IV americana** de los mids OTM (contra el mismo
  binomial que valúa las patas) y se calibra por el método **quasi-explicit**
  (Zeliade) con las condiciones de no-arbitraje de **Gatheral-Jacquier**; si no
  ajusta, cae a una sonrisa convexa. Suaviza el ruido y **marca los strikes con
  cotización sospechosa**. El botón vuelve a la IV cruda; el slider **Δ IV** la
  desplaza en puntos de volatilidad.
- La **tasa libre de riesgo** se interpola de la curva de rendimientos del Tesoro
  de EE.UU. (Treasury.gov) al **plazo del vencimiento** de la opción. Queda
  editable (botón `r: curva/manual`), igual que el dividendo y los días al vto.

El modelo por defecto es **americano** (binomial CRR con ejercicio anticipado):
las opciones sobre acciones y ETFs de EE.UU. son americanas. El botón `modelo`
pasa a europeo (Black-Scholes) para comparar, y la línea de estado muestra la
**prima de ejercicio anticipado** (americano − europeo). Delta/gamma/theta salen
de los nodos del árbol; vega/rho por diferencia central. Los **dividendos** se
modelan **discretos** (método escrow sobre un calendario de ex-div estimado por
cadencia, `/api/div`), que es cuando el ejercicio anticipado de un call de
verdad se juega.

## Estructura

```
server.py            servidor stdlib + capa de datos (yfinance → Yahoo HTTP)
run.sh               arranque para Termux
requirements.txt     yfinance
web/index.html       el lab (mercado + griegas americano/europeo)
web/rivendel.css     identidad visual propia
tests/               suite: golden-master del motor JS vs BS de alta precisión + server
run_tests.sh         corre la suite (pytest si está; si no, unittest de la stdlib)
pyproject.toml       packaging + configuración de pytest
.github/workflows/   CI (GitHub Actions): pytest + cross-check con py_vollib
```

## API

| endpoint | qué devuelve |
|---|---|
| `GET /api/health` | proveedor activo, versión de yfinance, versión de Python |
| `GET /api/load?t=NVDA` | spot, cierre previo, variación, moneda, vencimientos, vol. realizada 20d/60d, tasa de referencia (`riskFree`, ~3 meses de la curva) |
| `GET /api/curve` | curva de rendimientos del Tesoro de EE.UU. por tenor (`points`: pares `[años, tasa]`), fuente y fecha |
| `GET /api/div?t=NVDA` | calendario de ex-dividendos **estimado** (proyección de la cadencia histórica): `divs` = `[{date, amount}]` |
| `GET /api/chain?t=NVDA&e=2026-09-18` | cadena: strike, bid, ask, mid, último, IV, OI, volumen · más IV ATM y 1σ |

Cachés en memoria: 60 s el precio, 300 s la cadena. La cadena se recorta a los
90 strikes más cercanos al spot por lado y el JSON informa cuántos de cuántos
mandó.

## Tests

```bash
bash run_tests.sh        # golden-master del motor + server
```

`tests/` valida el **motor de pricing JS** contra una referencia **independiente**:
Black-Scholes de alta precisión en Python (`math.erf`) para las griegas europeas,
anclas americanas de literatura, propiedades (americano ≥ europeo; el dividendo
discreto baja el call) y la sintaxis del `<script>`. Corre con **pytest** o, sin
instalar nada, con `unittest` (stdlib). En CI se suma el cross-check contra
**py_vollib**. El golden se regenera con `python3 tests/gen_reference.py`.

## Lo que hay que tener en la cabeza

- **Los datos de Yahoo son diferidos** (típicamente ~15 minutos en acciones y
  ETFs de EE.UU.) y no son una fuente oficial. Fuera del horario de mercado, los
  bid/ask son los del cierre. Esto es para estudiar estructuras, no para operar.
- **`mid` no es un precio ejecutable.** En alas ilíquidas el spread se come
  parte del payoff: para eso está el toggle `prima: te cruzan`.
- El payoff es **al vencimiento**, sin valor tiempo, sin costo de financiamiento
  y sin ejercicio anticipado. Las opciones sobre acciones/ETFs de EE.UU. son
  americanas: una pata corta ITM te la pueden ejercer antes.
- **SOXL y SOXS rebalancean 3× por día.** Su volatilidad implícita no es la del
  índice por tres, y no son inversos exactos en horizontes largos: en un mercado
  que va y vuelve, las dos pueden perder.
- La IV que muestra el panel es la que reporta Yahoo por contrato, promediada
  sobre los cuatro strikes más cercanos al spot. Los dividendos del calendario
  son **proyectados** por cadencia histórica, no oficiales.

## Licencia

Código propio. Los datos de mercado pertenecen a sus proveedores (Yahoo Finance,
U.S. Treasury).
