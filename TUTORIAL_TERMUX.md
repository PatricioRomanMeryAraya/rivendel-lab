# Tutorial: instalar Termux en Android

Guía para dejar Termux funcionando en tu teléfono y correr proyectos como Rivendel (`python3 server.py` → `http://localhost:8765`).

## 1. Requisitos

- Android 7.0 o superior (la mayoría de los teléfonos actuales).
- Conexión a internet.
- ~500 MB libres para Termux + paquetes básicos.

## 2. Instala F-Droid

Termux **no** se instala desde Google Play: esa versión está congelada desde 2022 y no recibe actualizaciones. La versión oficial y mantenida está en F-Droid.

1. En el navegador del teléfono, abre **f-droid.org**.
2. Toca **Descargar F-Droid** y descarga el APK.
3. Android pedirá permiso para instalar apps de origen desconocido: **Ajustes → Apps → [tu navegador] → Instalar apps desconocidas → Permitir**.
4. Instala y abre F-Droid. En el primer inicio actualiza su índice de repositorios (tarda un minuto).

## 3. Instala Termux (y Termux:API)

1. En F-Droid, busca **Termux** e instálalo (desarrollador: Fredrik Fornwall).
2. Busca también **Termux:API** e instálalo. Permite funciones extra como mantener el teléfono despierto mientras corre un servidor (wake-lock).
3. Abre Termux y espera a que termine la instalación inicial. Verás el prompt `$`: listo.

> ⚠️ Si ya tenías Termux de Google Play, desinstálalo primero. No se puede migrar la configuración automáticamente (vive en los datos de la app).

## 4. Configuración inicial

Ejecuta estos comandos en Termux, uno por uno:

```bash
termux-setup-storage
```
Toca **Permitir** en el diálogo de Android. Esto crea `~/storage` con acceso a tus archivos.

```bash
pkg update && pkg upgrade -y
```
Actualiza el gestor de paquetes. Acepta con `Y` si lo pide.

```bash
pkg install python -y
```
Instala Python 3. Verifica con:
```bash
python3 --version
```

## 5. Prueba rápida

```bash
cd ~
python3 -m http.server 8000
```
Desde el navegador del teléfono abre **http://localhost:8000**. Si ves el listado de archivos, todo funciona. Detén el servidor con `Ctrl+C` (en Termux: sube el volumen + `C`, o toca la tecla `CTRL` del teclado extra).

## 6. Consejos útiles

- **Teclado extra**: mantén presionado el volumen + `Q` o desliza desde la izquierda para mostrar teclas `CTRL`, `ESC`, `TAB`, flechas. Esencial para usar la terminal.
- **Wake-lock** (para que Android no mate tu servidor): con Termux:API instalado,
  ```bash
  pkg install termux-api -y
  termux-wake-lock
  ```
  Para soltarlo: `termux-wake-unlock`.
- **Correr en segundo plano**: Android 12+ puede matar procesos en segundo plano (PhantomProcessKiller). El wake-lock ayuda; para uso prolongado considera desactivar la optimización de batería para Termux en **Ajustes → Apps → Termux → Batería → Sin restricciones**.
- **Actualizar**: cada cierto tiempo, `pkg update && pkg upgrade -y`.

## 7. Correr Rivendel

Con el proyecto en el teléfono (por ejemplo en `~/rivendel`):

```bash
cd ~/rivendel
python3 server.py
```

Abre **http://localhost:8765** en el navegador del teléfono.

## Problemas comunes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `pkg` falla o paquetes no instalan | Termux de Google Play | Desinstalar e instalar desde F-Droid |
| El servidor se detiene solo | Android mata el proceso | `termux-wake-lock` + batería sin restricciones |
| `Permission denied` al acceder a archivos | Falta permiso de almacenamiento | Ejecutar `termux-setup-storage` y permitir |
| No aparece el teclado completo | Teclado extra oculto | Volumen + `Q` o deslizar desde la izquierda |
