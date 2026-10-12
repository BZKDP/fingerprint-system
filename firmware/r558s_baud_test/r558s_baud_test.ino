#include <Arduino.h>
#include <Adafruit_Fingerprint.h>

static const uint8_t FINGER_RX_PIN = 16; // ESP32 RX2
static const uint8_t FINGER_TX_PIN = 17; // ESP32 TX2
static const uint8_t TOUCH_PIN = 5;      // ESP32 D5, AS608 TCH input
static const uint32_t USB_BAUD = 115200;
static const uint32_t SENSOR_SCAN_BAUDS[] = {57600, 9600, 19200, 38400, 115200};
static const uint32_t SENSOR_UART_CONFIGS[] = {SERIAL_8N1, SERIAL_8N2};

HardwareSerial FingerSerial(2);

// Stream constructor keeps UART2 pin setup under our control.
Adafruit_Fingerprint finger((Stream *)&FingerSerial);

const char *uartConfigName(uint32_t config) {
  return config == SERIAL_8N2 ? "8N2" : "8N1";
}

bool tryUart(uint32_t baud, uint32_t uartConfig) {
  FingerSerial.end();
  delay(100);
  FingerSerial.begin(baud, uartConfig, FINGER_RX_PIN, FINGER_TX_PIN);

  // Compatible with Adafruit examples; actual ESP32 pins are configured above.
  finger.begin(baud);

  Serial.print("Trying baud ");
  Serial.print(baud);
  Serial.print(" ");
  Serial.print(uartConfigName(uartConfig));
  Serial.print(" on RX GPIO");
  Serial.print(FINGER_RX_PIN);
  Serial.print(" / TX GPIO");
  Serial.print(FINGER_TX_PIN);
  Serial.print(" / TCH D5=");
  Serial.print(digitalRead(TOUCH_PIN));
  Serial.print(" ... ");

  if (!finger.verifyPassword()) {
    Serial.println("not found");
    return false;
  }

  Serial.println("FOUND");

  uint8_t params = finger.getParameters();
  uint8_t count = finger.getTemplateCount();

  Serial.print("getParameters: ");
  Serial.println(params == FINGERPRINT_OK ? "OK" : "ERROR");
  Serial.print("getTemplateCount: ");
  Serial.println(count == FINGERPRINT_OK ? "OK" : "ERROR");
  Serial.print("Templates: ");
  Serial.println(finger.templateCount);
  Serial.print("Capacity: ");
  Serial.println(finger.capacity);
  Serial.print("Packet length: ");
  Serial.println(finger.packet_len);
  Serial.print("Sensor-reported baud: ");
  Serial.println(finger.baud_rate);
  return true;
}

void scanOnce() {
  Serial.println();
  Serial.println("=== AS608 baud scan start ===");
  Serial.println("Wiring expected: V+->3V3, VA->3V3, GND->GND, TX->RX2/GPIO16, RX->TX2/GPIO17, TCH->D5/GPIO5");
  Serial.print("TCH/D5 line at scan start: ");
  Serial.println(digitalRead(TOUCH_PIN));

  bool found = false;
  for (uint32_t uartConfig : SENSOR_UART_CONFIGS) {
    for (uint32_t baud : SENSOR_SCAN_BAUDS) {
      if (tryUart(baud, uartConfig)) {
        found = true;
        break;
      }
    }
    if (found) {
      break;
    }
  }

  if (!found) {
    Serial.println("AS608 NOT FOUND at 57600, 9600, 19200, 38400, 115200 using SERIAL_8N1 or SERIAL_8N2.");
  }
  Serial.println("=== AS608 baud scan end ===");
}

void setup() {
  pinMode(TOUCH_PIN, INPUT);
  Serial.begin(USB_BAUD);
  delay(1500);
  scanOnce();
}

void loop() {
  delay(5000);
  scanOnce();
}
