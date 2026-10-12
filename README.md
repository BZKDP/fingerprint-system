# Fingerprint access control (ESP32 + desktop)

Система биометрического контроля доступа: сотрудник прикладывает палец, десктоп-приложение находит его в базе и подтверждает вход (СКУД). Состоит из прошивки ESP32 и настольного приложения на Python.

## Железо

- ESP32 DevKit + UART-сканер отпечатков AS608 или R558-S (библиотека `Adafruit_Fingerprint`).
- Распиновка UART: сканер TX -> ESP32 RX2/GPIO16, сканер RX -> ESP32 TX2/GPIO17, общий GND, питание 3V3. Линия TCH -> GPIO5 только на вход.

## Прошивка (`firmware/esp32_fingerprint`)

Общается с десктопом по USB-Serial (115200) построчным JSON-протоколом: команды `ping`, `info`, `enroll`, `identify`, `delete`, `empty`, `count`. При старте и в `info` выполняет автоопределение скорости сканера (перебор 57600/9600/19200/38400/115200 при 8N1 и 8N2). В `firmware/` также лежат тестовые прошивки перебора baud для AS608 и R558-S.

## Десктоп-приложение (`desktop`)

PySide6-интерфейс: регистрация отпечатков (enroll с именем, пальцем, комментарием и ID), постоянная идентификация на главном экране, управление записями, логи. Локальная база — SQLite (`desktop/fingerprints.db`), создаётся автоматически при первом запуске; в репозиторий не коммитится (биометрия).

## Запуск

```powershell
cd desktop
python -m pip install -r requirements.txt
python main.py
```

Прошивка собирается и заливается через Arduino CLI (ESP32 core + `Adafruit Fingerprint Sensor Library` + `ArduinoJson`); подробности и диагностика в `desktop/README.md`.

## Зависимости

Python: `pyserial`, `PySide6`. Прошивка: ESP32 Arduino core, `Adafruit_Fingerprint`, `ArduinoJson`.
