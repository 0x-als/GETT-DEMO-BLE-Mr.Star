# Lumen

**English** | [Русский](#lumen-на-русском)

Lumen is a desktop application for controlling Bluetooth LED strips from a PC. It is a PC alternative to the **Mr.Star** app and was written as a **clean-room implementation**: the code and assets were created independently, without copying anything from the original application.

> Lumen is an independent project and is not affiliated with, endorsed by, or connected to the authors of Mr.Star. All product names and trademarks belong to their respective owners.

---

## Table of contents

- [Features](#features)
- [Quick start (ready-made .exe)](#quick-start-ready-made-exe)
- [Running from source](#running-from-source)
- [Development](#development)
- [How the application works](#how-the-application-works)
- [Project structure](#project-structure)
- [Configuration and files](#configuration-and-files)
- [BLE protocol](#ble-protocol)
- [Troubleshooting](#troubleshooting)

---

## Features

- Scan for nearby Bluetooth LE devices and pick your LED strip (strips that advertise the `fff0` service are highlighted as "looks like an LED strip").
- Color wheel: angle = hue, distance from the center = saturation. Dragging near the center snaps to white.
- 9 color presets and a gradient brightness slider.
- Power button with a glow in the current color.
- Automatic connection to the saved strip and automatic reconnection with growing delays (3, 5, 8, 15, 30 seconds).
- Restores the saved color, brightness and on/off state after every (re)connection.
- System tray icon (the ring takes the strip's color, and turns grey when the strip is off) with a menu: open, on/off, color presets, exit.
- Autostart with Windows, launching minimized to the tray.
- Turns the strip off when Windows shuts down or restarts. The saved state is not changed, so the color comes back on the next launch.
- Protection against running two copies: launching the app again simply shows the already running window.
- Settings and logs are stored in `%APPDATA%\Lumen`.

---

## Quick start (ready-made .exe)

A ready-to-run build is already included in the repository:

```
dist/
└── main.exe
```

1. Open the `dist` folder.
2. Run `main.exe`.
3. On the first launch the device picker opens automatically. Turn your LED strip on, wait for it to appear in the list and press **Select**. The app remembers the device.

No Python installation is required to use the `.exe`.

---

## Running from source

```bash
pip install -r requirements.txt
python main.py
```

To start minimized to the tray (like autostart does):

```bash
python main.py --minimized
```

---

## Development

If you want to contribute or modify the app, install the dependencies with:

```bash
pip install -r requirements.txt
```

**Python version used in development: 3.12.10.**

Main dependencies:

| Package | Purpose |
|---|---|
| `customtkinter` | Modern-looking UI on top of tkinter |
| `bleak` | Bluetooth LE scanning and connection |
| `Pillow` | Rendering the color wheel, slider track and tray icon |
| `pystray` | System tray icon (optional; without it the tray features are disabled) |

Useful constants at the top of `main.py`:

| Constant | Meaning |
|---|---|
| `WRITE_UUID` | GATT characteristic used for writing commands |
| `HUE_SCALE` | Hue scale in the color packet: `360` (degrees) or `1000` |
| `BRIGHT_MAX` | Maximum brightness value in the packet (controller scale 0..1000) |
| `COLOR_AFTER_BRIGHTNESS` | Set to `True` if the color "drifts" after changing brightness |
| `RETRY_DELAYS` | Pauses between reconnection attempts, in seconds |
| `INSTANCE_PORT` | Local port used to prevent a second instance |

---

## How the application works

Lumen is a single-file application (`main.py`) built around three ideas: a UI thread, a background BLE thread, and a queue connecting them.

```
┌─────────────────────────┐        commands         ┌──────────────────────────┐
│  UI thread (tkinter)    │ ──────────────────────► │  BLE thread (asyncio)    │
│  App, widgets, tray     │   run_coroutine_        │  BLEWorker + bleak       │
│                         │   threadsafe            │                          │
│  _poll_events() every   │ ◄────────────────────── │  connect / write / scan  │
│  80 ms                  │   queue.Queue (events)  │                          │
└─────────────────────────┘                         └──────────────────────────┘
        ▲                                                       │
        │ events: "tray", "restore"                             ▼
┌───────┴───────────────┐                              Bluetooth LED strip
│ pystray thread        │                              (GATT, no response)
│ InstanceGuard thread  │
│ ShutdownWatcher thread│
└───────────────────────┘
```

### Threads

1. **UI thread**: the `App` window (`customtkinter`). It never blocks on Bluetooth. It sends commands to the BLE thread and reads events from the queue.
2. **BLE thread**: `BLEWorker` owns its own `asyncio` event loop. All Bluetooth operations (`connect`, `write`, `scan`, `power_off_now`) run there.
3. **Tray thread**: `pystray` runs detached; menu clicks are turned into events and put into the queue.
4. **Instance guard thread**: listens on a local TCP port.
5. **Shutdown watcher thread**: an invisible Win32 window that receives Windows shutdown messages.

### Event flow

The BLE thread and helper threads never touch the UI directly. They put `(kind, data)` tuples into a `queue.Queue`, and `App._poll_events()` handles them every 80 ms:

| Event | Meaning |
|---|---|
| `connecting`, `connected`, `disconnected` | Connection state, updates the status pill |
| `connect_error`, `write_error` | Errors, shown in the debug area; connection errors schedule a retry |
| `sent` | A packet was sent (the last 3 are shown in settings) |
| `scan_done`, `scan_error` | Results of the device search for the picker window |
| `tray` | An action from the tray menu or from a second app launch |
| `restore` | Shutdown was cancelled: re-apply the saved state |

### Sending commands

- Moving the wheel or slider calls `_changed()`, which marks the color and/or brightness as "dirty" and schedules `_flush()` after 80 ms. This limits traffic to about 12 updates per second while dragging.
- If the strip is logically off and the user changes color or brightness, the app automatically turns it on.
- `BLEWorker.write()` sends packets one after another under a lock with a 25 ms pause between them, using write-without-response.

### Connecting and reconnecting

1. On start, if a device address is saved, `_connect()` is called.
2. On failure or disconnection, `_schedule_retry()` plans the next attempt with the delays from `RETRY_DELAYS`.
3. On success, `_apply_saved_state()` sends the stored on/off state, color and brightness to the strip.
4. Clicking the status pill triggers a manual reconnect.

### Saving state

State (hue, saturation, brightness, on/off, device, switches) is saved to `config.json`. Saving is debounced by 500 ms and done atomically (a temporary file followed by `os.replace`).

### Windows integration

- **Autostart**: a value in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. For source runs it uses `pythonw.exe` (no console window); for the `.exe` build it uses `main.exe --minimized`. If the program file was moved, the path is refreshed on the next launch.
- **Shutdown handling**: `ShutdownWatcher` creates a hidden window and handles `WM_QUERYENDSESSION` / `WM_ENDSESSION`. Bluetooth is still available at that moment, so the app sends the power-off command twice. Logoff is ignored (only real shutdown or restart counts). If shutdown is cancelled, the saved state is restored. The saved color and "on" state are not modified, so they return after the next start.

---

## Project structure

`main.py` is divided into sections:

| Section | Contents |
|---|---|
| Settings | BLE UUID, power packets, timing, paths |
| Theme | Colors and fonts |
| Color utilities | `hex_to_rgb`, `rgb_to_hex`, `mix`, `hsv_hex` |
| Command packets | `make_color()`, `make_brightness()`, `POWER_ON`, `POWER_OFF` |
| Config | `load_config()`, `save_config()`, `DEFAULT_CONFIG` |
| Autostart | `launch_command()`, `autostart_value()`, `set_autostart()` |
| `InstanceGuard` | Single-instance protection via a local socket |
| `ShutdownWatcher` | Windows shutdown detection (ctypes + Win32) |
| `BLEWorker` | Background asyncio loop, connection, writing, scanning |
| Custom widgets | `PowerButton`, `ColorWheel`, `GradientSlider`, `make_tray_icon()` |
| `DevicePicker` | Window for searching and choosing the strip |
| `App` | Main window, tray, event handling, state logic |
| `main()` | Logging setup, instance check, app start |

---

## Configuration and files

Everything is stored in `%APPDATA%\Lumen` (or `~/.config/Lumen` on non-Windows systems):

| File | Purpose |
|---|---|
| `config.json` | Saved device, color, brightness, state and switches |
| `lumen.log` | Application log (useful for bug reports) |

Fields of `config.json`:

| Field | Description |
|---|---|
| `address`, `name` | MAC address and name of the selected strip |
| `hue`, `sat`, `bright` | Color (0..360, 0..100) and brightness (1..100) |
| `on` | Last known power state |
| `configured` | The user has already set the strip state at least once |
| `close_to_tray` | The close button hides the window to the tray |
| `off_on_shutdown` | Turn the strip off when the PC shuts down |

---

## BLE protocol

Commands are written to the characteristic `0000fff3-0000-1000-8000-00805f9b34fb` without response.

| Command | Packet |
|---|---|
| Power on | `BC 01 01 01 55` |
| Power off | `BC 01 01 00 55` |
| Color | `BC 04 06 [HUE:2] [SAT:2] 00 00 55` (saturation 0..1000) |
| Brightness | `BC 05 06 [VALUE:2] 00 00 00 00 55` (value 0..`BRIGHT_MAX`) |

Multi-byte values are big-endian.

---

## Troubleshooting

- **The strip is not in the list**: make sure it is powered on, not connected to another app (for example, a phone), and that Bluetooth is enabled on the PC. Tick "Без названия" ("Unnamed") to show devices without a name.
- **Colors or brightness behave strangely**: try changing `HUE_SCALE` to `1000`, or set `COLOR_AFTER_BRIGHTNESS = True`.
- **No tray icon**: install `pystray` (`pip install pystray`).
- **Something else went wrong**: check `%APPDATA%\Lumen\lumen.log`.

---
---

# Lumen на русском

[English](#lumen) | **Русский**

Lumen — приложение для управления Bluetooth LED-лентой с компьютера. Это аналог приложения **Mr.Star** для ПК, написанный по принципу **«чистой комнаты» (clean-room)**: код и ресурсы созданы независимо, без копирования чего-либо из оригинального приложения.

> Lumen — независимый проект, не связанный с авторами Mr.Star и не одобренный ими. Все названия продуктов и товарные знаки принадлежат их владельцам.

---

## Содержание

- [Возможности](#возможности)
- [Быстрый старт (готовый .exe)](#быстрый-старт-готовый-exe)
- [Запуск из исходников](#запуск-из-исходников)
- [Разработка](#разработка)
- [Как работает приложение](#как-работает-приложение)
- [Структура проекта](#структура-проекта)
- [Настройки и файлы](#настройки-и-файлы)
- [BLE-протокол](#ble-протокол)
- [Если что-то не работает](#если-что-то-не-работает)

---

## Возможности

- Поиск Bluetooth LE устройств поблизости и выбор своей ленты (устройства с сервисом `fff0` помечаются как «Похоже на LED-ленту»).
- Цветовой круг: угол — оттенок, расстояние от центра — насыщенность. Возле центра выбор «прилипает» к белому.
- 9 готовых цветов и слайдер яркости с градиентом.
- Кнопка питания со свечением цвета ленты.
- Автоподключение к сохранённой ленте и автоматическое переподключение с растущими паузами (3, 5, 8, 15, 30 секунд).
- После каждого (пере)подключения ленте возвращаются сохранённые цвет, яркость и состояние вкл/выкл.
- Иконка в трее (кольцо цвета ленты, серое — если лента выключена) с меню: открыть, вкл/выкл, цвета, выход.
- Автозапуск вместе с Windows сразу в трей.
- Выключение ленты при завершении работы или перезагрузке Windows. Сохранённое состояние не меняется, поэтому цвет вернётся после следующего запуска.
- Защита от двойного запуска: повторный запуск просто открывает уже работающее окно.
- Настройки и лог хранятся в `%APPDATA%\Lumen`.

---

## Быстрый старт (готовый .exe)

В репозитории уже лежит готовая собранная версия:

```
dist/
└── main.exe
```

1. Откройте папку `dist`.
2. Запустите `main.exe`.
3. При первом запуске автоматически откроется окно выбора устройства. Включите ленту, дождитесь, когда она появится в списке, и нажмите **«Выбрать»**. Приложение запомнит устройство.

Для использования `.exe` устанавливать Python не нужно.

---

## Запуск из исходников

```bash
pip install -r requirements.txt
python main.py
```

Запуск свёрнутым в трей (так работает автозапуск):

```bash
python main.py --minimized
```

---

## Разработка

Если вы хотите внести доработки, установите зависимости командой:

```bash
pip install -r requirements.txt
```

**Версия Python, использованная при разработке: 3.12.10.**

Основные зависимости:

| Пакет | Назначение |
|---|---|
| `customtkinter` | Современный интерфейс поверх tkinter |
| `bleak` | Поиск и подключение по Bluetooth LE |
| `Pillow` | Отрисовка цветового круга, дорожки слайдера и иконки трея |
| `pystray` | Иконка в системном трее (необязательно; без неё функции трея отключены) |

Полезные константы в начале `main.py`:

| Константа | Значение |
|---|---|
| `WRITE_UUID` | GATT-характеристика для записи команд |
| `HUE_SCALE` | Шкала оттенка в пакете цвета: `360` (градусы) или `1000` |
| `BRIGHT_MAX` | Максимум яркости в пакете (шкала контроллера 0..1000) |
| `COLOR_AFTER_BRIGHTNESS` | Поставьте `True`, если после смены яркости цвет «плывёт» |
| `RETRY_DELAYS` | Паузы между попытками переподключения, в секундах |
| `INSTANCE_PORT` | Локальный порт для защиты от двойного запуска |

---

## Как работает приложение

Lumen — однофайловое приложение (`main.py`), построенное на трёх идеях: поток интерфейса, фоновый поток Bluetooth и очередь, которая их связывает.

```
┌─────────────────────────┐        команды          ┌──────────────────────────┐
│  Поток UI (tkinter)     │ ──────────────────────► │  Поток BLE (asyncio)     │
│  App, виджеты, трей     │   run_coroutine_        │  BLEWorker + bleak       │
│                         │   threadsafe            │                          │
│  _poll_events() каждые  │ ◄────────────────────── │  connect / write / scan  │
│  80 мс                  │   queue.Queue (события) │                          │
└─────────────────────────┘                         └──────────────────────────┘
        ▲                                                       │
        │ события: "tray", "restore"                            ▼
┌───────┴───────────────┐                              Bluetooth LED-лента
│ Поток pystray         │                              (GATT, без ответа)
│ Поток InstanceGuard   │
│ Поток ShutdownWatcher │
└───────────────────────┘
```

### Потоки

1. **Поток интерфейса**: окно `App` (`customtkinter`). Он никогда не ждёт Bluetooth: отправляет команды в поток BLE и читает события из очереди.
2. **Поток BLE**: `BLEWorker` держит собственный цикл `asyncio`. Все операции Bluetooth (`connect`, `write`, `scan`, `power_off_now`) выполняются в нём.
3. **Поток трея**: `pystray` работает отдельно; клики по меню превращаются в события и кладутся в очередь.
4. **Поток защиты от двойного запуска**: слушает локальный TCP-порт.
5. **Поток слежения за выключением**: невидимое окно Win32, получающее сообщения о завершении работы Windows.

### Поток событий

Поток BLE и вспомогательные потоки не трогают интерфейс напрямую. Они кладут кортежи `(kind, data)` в `queue.Queue`, а `App._poll_events()` разбирает их каждые 80 мс:

| Событие | Значение |
|---|---|
| `connecting`, `connected`, `disconnected` | Состояние соединения, обновляет индикатор статуса |
| `connect_error`, `write_error` | Ошибки, показываются в отладочной области; ошибка подключения планирует повтор |
| `sent` | Пакет отправлен (последние 3 видны в настройках) |
| `scan_done`, `scan_error` | Результаты поиска устройств для окна выбора |
| `tray` | Действие из меню трея или повторного запуска приложения |
| `restore` | Выключение ПК отменено: вернуть сохранённое состояние |

### Отправка команд

- Движение по кругу или слайдеру вызывает `_changed()`, который помечает цвет и/или яркость как «изменённые» и планирует `_flush()` через 80 мс. Так трафик ограничен примерно 12 обновлениями в секунду при перетаскивании.
- Если лента логически выключена, а пользователь меняет цвет или яркость, приложение автоматически её включает.
- `BLEWorker.write()` отправляет пакеты по очереди под блокировкой с паузой 25 мс между ними, в режиме записи без ответа.

### Подключение и переподключение

1. При запуске, если сохранён адрес устройства, вызывается `_connect()`.
2. При неудаче или потере связи `_schedule_retry()` планирует следующую попытку с паузами из `RETRY_DELAYS`.
3. При успехе `_apply_saved_state()` отправляет ленте сохранённые состояние вкл/выкл, цвет и яркость.
4. Клик по индикатору статуса запускает ручное переподключение.

### Сохранение состояния

Состояние (оттенок, насыщенность, яркость, вкл/выкл, устройство, переключатели) сохраняется в `config.json`. Сохранение откладывается на 500 мс и выполняется атомарно (временный файл, затем `os.replace`).

### Интеграция с Windows

- **Автозапуск**: значение в `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. При запуске из исходников используется `pythonw.exe` (без окна консоли); для сборки `.exe` — `main.exe --minimized`. Если файл программы переместили, путь обновится при следующем запуске.
- **Обработка выключения**: `ShutdownWatcher` создаёт скрытое окно и обрабатывает `WM_QUERYENDSESSION` / `WM_ENDSESSION`. Bluetooth в этот момент ещё работает, поэтому приложение отправляет команду выключения дважды. Выход из учётной записи игнорируется (учитывается только настоящее выключение или перезагрузка). Если выключение отменили, сохранённое состояние возвращается. Сохранённые цвет и состояние «включено» не меняются, поэтому они вернутся после следующего запуска.

---

## Структура проекта

`main.py` разделён на секции:

| Секция | Содержимое |
|---|---|
| Настройки | BLE UUID, пакеты питания, тайминги, пути |
| Тема | Цвета и шрифты |
| Цвет-утилиты | `hex_to_rgb`, `rgb_to_hex`, `mix`, `hsv_hex` |
| Пакеты команд | `make_color()`, `make_brightness()`, `POWER_ON`, `POWER_OFF` |
| Настройки (конфиг) | `load_config()`, `save_config()`, `DEFAULT_CONFIG` |
| Автозапуск | `launch_command()`, `autostart_value()`, `set_autostart()` |
| `InstanceGuard` | Защита от двойного запуска через локальный сокет |
| `ShutdownWatcher` | Слежение за выключением Windows (ctypes + Win32) |
| `BLEWorker` | Фоновый цикл asyncio, подключение, запись, сканирование |
| Кастомные виджеты | `PowerButton`, `ColorWheel`, `GradientSlider`, `make_tray_icon()` |
| `DevicePicker` | Окно поиска и выбора ленты |
| `App` | Главное окно, трей, обработка событий, логика состояния |
| `main()` | Настройка логов, проверка копии, запуск приложения |

---

## Настройки и файлы

Всё хранится в `%APPDATA%\Lumen` (или `~/.config/Lumen` вне Windows):

| Файл | Назначение |
|---|---|
| `config.json` | Сохранённые устройство, цвет, яркость, состояние и переключатели |
| `lumen.log` | Лог приложения (полезен при сообщениях об ошибках) |

Поля `config.json`:

| Поле | Описание |
|---|---|
| `address`, `name` | MAC-адрес и имя выбранной ленты |
| `hue`, `sat`, `bright` | Цвет (0..360, 0..100) и яркость (1..100) |
| `on` | Последнее известное состояние питания |
| `configured` | Пользователь уже хотя бы раз задавал состояние ленты |
| `close_to_tray` | Крестик сворачивает окно в трей |
| `off_on_shutdown` | Выключать ленту при завершении работы ПК |

---

## BLE-протокол

Команды записываются в характеристику `0000fff3-0000-1000-8000-00805f9b34fb` без ответа.

| Команда | Пакет |
|---|---|
| Включить | `BC 01 01 01 55` |
| Выключить | `BC 01 01 00 55` |
| Цвет | `BC 04 06 [HUE:2] [SAT:2] 00 00 55` (насыщенность 0..1000) |
| Яркость | `BC 05 06 [VALUE:2] 00 00 00 00 55` (значение 0..`BRIGHT_MAX`) |

Многобайтовые значения передаются в порядке big-endian.

---

## Если что-то не работает

- **Ленты нет в списке**: убедитесь, что она включена, не подключена к другому приложению (например, к телефону) и что Bluetooth на ПК включён. Отметьте «Без названия», чтобы показать устройства без имени.
- **Странное поведение цвета или яркости**: попробуйте поставить `HUE_SCALE = 1000` или `COLOR_AFTER_BRIGHTNESS = True`.
- **Нет иконки в трее**: установите `pystray` (`pip install pystray`).
- **Что-то ещё пошло не так**: посмотрите `%APPDATA%\Lumen\lumen.log`.