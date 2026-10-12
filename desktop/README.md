# ESP32 Fingerprint System

Проект состоит из универсальной прошивки ESP32 и desktop-приложения на Python/PySide6. Прошивка работает с UART-сканерами семейства AS608/R558S через библиотеку `Adafruit_Fingerprint`.

## Подключение AS608 к ESP32

| AS608 пин | ESP32 DevKit пин |
| --- | --- |
| V+ | 3V3 |
| VA | 3V3 |
| GND | GND |
| TX | RX2 / GPIO16 |
| RX | TX2 / GPIO17 |
| TCH | D5 / GPIO5 |
| D+ | не подключать |
| D- | не подключать |

`D5/GPIO5` используется только как `INPUT`; прошивка не управляет линией `TCH` как выходом.

## Подключение R558S к ESP32

Если возвращаете R558S, подключайте не по старой схеме, а по реальным пинам модуля:

| R558S пин | ESP32 DevKit пин |
| --- | --- |
| VCC_3.3V | 3V3 |
| VCC_MCU | 3V3 |
| GND | GND |
| UART_TX | RX2 / GPIO16 |
| UART_RX | TX2 / GPIO17 |
| TOUCH_OUT | D5 / GPIO5 |

`D5/GPIO5` также используется только как `INPUT`.

## Arduino IDE / Arduino CLI

Нужны:

- ESP32 core by Espressif
- `Adafruit Fingerprint Sensor Library`
- `ArduinoJson`

Через Arduino CLI:

```powershell
arduino-cli core install esp32:esp32
arduino-cli lib install "Adafruit Fingerprint Sensor Library"
arduino-cli lib install "ArduinoJson"
```

Сборка:

```powershell
arduino-cli compile --fqbn esp32:esp32:esp32 firmware\esp32_fingerprint
```

Прошивка, пример для `COM9`:

```powershell
arduino-cli upload -p COM9 --fqbn esp32:esp32:esp32 firmware\esp32_fingerprint
```

Если загрузка зависает на подключении, зажмите кнопку `BOOT` на ESP32 в момент начала загрузки и отпустите после появления процесса записи.

## Python desktop-приложение

Установка зависимостей:

```powershell
cd desktop
python -m pip install -r requirements.txt
```

Запуск:

```powershell
python main.py
```

База SQLite создаётся рядом с приложением: `desktop/fingerprints.db`.

В приложении:

- главный экран постоянно ждёт палец и показывает, кто найден;
- если ID найден в сканере, но не привязан в SQLite, показывается ID отпечатка;
- если совпадений нет, показывается новый/неизвестный палец;
- при добавлении можно указать имя, конкретный палец, комментарий и ID отпечатка;
- одному человеку можно добавить несколько пальцев отдельными записями с разными ID;
- после успешного добавления имя остаётся в поле, чтобы быстро добавить следующий палец того же человека.

## Если сканер не найден

1. Проверьте питание: `V+` и `VA` AS608 должны быть на `3V3`, `GND` на `GND`.
2. Проверьте общий GND между сканером и ESP32.
3. Проверьте UART:
   - `TX` AS608 -> `RX2` / GPIO16 ESP32;
   - `RX` AS608 -> `TX2` / GPIO17 ESP32.
4. Если `sensor_found=false`, попробуйте поменять местами `TX` и `RX`.
5. Текущая основная прошивка сначала использует `SERIAL_8N1`, затем `SERIAL_8N2`, и перебирает скорости `57600`, `9600`, `19200`, `38400`, `115200` на пинах `RX2/GPIO16` и `TX2/GPIO17`.
6. USB Serial ESP32 работает на `115200 baud`.

## Тестовая прошивка AS608

Отдельная тестовая прошивка находится в `firmware/as608_baud_test/as608_baud_test.ino`. Она не принимает JSON-команды, а только перебирает baud rates и печатает результат в Serial Monitor.

Сборка:

```powershell
arduino-cli compile --fqbn esp32:esp32:esp32 firmware\as608_baud_test
```

Прошивка:

```powershell
arduino-cli upload -p COM9 --fqbn esp32:esp32:esp32 firmware\as608_baud_test
```

После теста верните основную прошивку:

```powershell
arduino-cli upload -p COM9 --fqbn esp32:esp32:esp32 firmware\esp32_fingerprint
```

## Если ESP32 не появляется как COM-порт

На неоригинальных ESP32 DevKit часто стоят USB-UART чипы CP2102/CP2102N, CH340/CH341 или CH9102. Нужен драйвер именно для вашего чипа.

Для CP2102 нужен Silicon Labs CP210x VCP driver. После установки в диспетчере устройств должен появиться порт вида `Silicon Labs CP210x USB to UART Bridge (COMx)`.
