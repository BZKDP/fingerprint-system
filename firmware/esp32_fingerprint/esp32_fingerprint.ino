#include <Arduino.h>
#include <ArduinoJson.h>
#include <Adafruit_Fingerprint.h>

// Universal UART fingerprint firmware for ESP32 + Adafruit_Fingerprint.
// UART2 is fixed to RX2/GPIO16 and TX2/GPIO17. D5/GPIO5 is only an input
// for optional touch/wake wiring; it is never driven as an output.
static const uint8_t FINGER_RX_PIN = 16;
static const uint8_t FINGER_TX_PIN = 17;
static const uint8_t TOUCH_PIN = 5;
static const uint32_t FINGER_BAUD = 57600;
static const uint32_t USB_BAUD = 115200;
static const uint32_t SENSOR_SCAN_BAUDS[] = {57600, 9600, 19200, 38400, 115200};
static const uint32_t SENSOR_REPORT_BAUDS[] = {9600, 19200, 38400, 57600, 115200};
static const uint32_t SENSOR_UART_CONFIGS[] = {SERIAL_8N1, SERIAL_8N2};

HardwareSerial FingerSerial(2);

// Use the Stream constructor so the library does not re-open UART2 without
// explicit RX/TX pins. We configure FingerSerial ourselves in setup().
Adafruit_Fingerprint finger((Stream *)&FingerSerial);

static bool sensorFound = false;
static String inputLine;
static uint32_t activeFingerBaud = FINGER_BAUD;
static uint32_t activeUartConfig = SERIAL_8N1;

const char *statusName(uint8_t code) {
  switch (code) {
  case FINGERPRINT_OK:
    return "ok";
  case FINGERPRINT_PACKETRECIEVEERR:
    return "packet_receive_error";
  case FINGERPRINT_NOFINGER:
    return "no_finger";
  case FINGERPRINT_IMAGEFAIL:
    return "image_fail";
  case FINGERPRINT_IMAGEMESS:
    return "image_too_messy";
  case FINGERPRINT_FEATUREFAIL:
    return "feature_fail";
  case FINGERPRINT_NOMATCH:
    return "no_match";
  case FINGERPRINT_NOTFOUND:
    return "not_found";
  case FINGERPRINT_ENROLLMISMATCH:
    return "enroll_mismatch";
  case FINGERPRINT_BADLOCATION:
    return "bad_location";
  case FINGERPRINT_DBRANGEFAIL:
    return "database_range_fail";
  case FINGERPRINT_UPLOADFEATUREFAIL:
    return "upload_feature_fail";
  case FINGERPRINT_PACKETRESPONSEFAIL:
    return "packet_response_fail";
  case FINGERPRINT_UPLOADFAIL:
    return "upload_fail";
  case FINGERPRINT_DELETEFAIL:
    return "delete_fail";
  case FINGERPRINT_DBCLEARFAIL:
    return "database_clear_fail";
  case FINGERPRINT_PASSFAIL:
    return "password_fail";
  case FINGERPRINT_INVALIDIMAGE:
    return "invalid_image";
  case FINGERPRINT_FLASHERR:
    return "flash_error";
  case FINGERPRINT_INVALIDREG:
    return "invalid_register";
  case FINGERPRINT_TIMEOUT:
    return "sensor_timeout";
  case FINGERPRINT_BADPACKET:
    return "bad_packet";
  default:
    return "unknown_sensor_error";
  }
}

void sendJson(JsonDocument &doc) {
  serializeJson(doc, Serial);
  Serial.println();
}

void sendError(const char *cmd, const char *error) {
  JsonDocument doc;
  doc["status"] = "error";
  if (cmd && cmd[0] != '\0') {
    doc["cmd"] = cmd;
  }
  doc["error"] = error;
  sendJson(doc);
}

void sendSensorError(const char *cmd, const char *operation, uint8_t code) {
  JsonDocument doc;
  doc["status"] = "error";
  doc["cmd"] = cmd;
  doc["operation"] = operation;
  doc["error"] = statusName(code);
  doc["code"] = code;
  sendJson(doc);
}

void sendProgress(const char *message) {
  JsonDocument doc;
  doc["status"] = "progress";
  doc["message"] = message;
  sendJson(doc);
}

bool refreshSensor() {
  if (!finger.verifyPassword()) {
    sensorFound = false;
    return false;
  }

  sensorFound = true;
  finger.getParameters();
  finger.getTemplateCount();
  return true;
}

const char *uartConfigName(uint32_t config) {
  return config == SERIAL_8N2 ? "8N2" : "8N1";
}

bool configureFingerUart(uint32_t baud, uint32_t uartConfig) {
  FingerSerial.end();
  delay(50);
  FingerSerial.begin(baud, uartConfig, FINGER_RX_PIN, FINGER_TX_PIN);

  activeFingerBaud = baud;
  activeUartConfig = uartConfig;

  // With the Stream constructor this call mainly gives the sensor boot delay
  // used by the Adafruit examples. The actual UART pins are set above.
  finger.begin(baud);
  if (!finger.verifyPassword()) {
    sensorFound = false;
    return false;
  }

  sensorFound = true;
  finger.getParameters();
  finger.getTemplateCount();
  return true;
}

bool scanSensorBauds() {
  for (uint32_t uartConfig : SENSOR_UART_CONFIGS) {
    for (uint32_t baud : SENSOR_SCAN_BAUDS) {
      if (configureFingerUart(baud, uartConfig)) {
        return true;
      }
    }
  }

  FingerSerial.end();
  delay(50);
  FingerSerial.begin(FINGER_BAUD, SERIAL_8N1, FINGER_RX_PIN, FINGER_TX_PIN);
  activeFingerBaud = FINGER_BAUD;
  activeUartConfig = SERIAL_8N1;
  sensorFound = false;
  return false;
}

bool ensureSensor(const char *cmd) {
  if (refreshSensor() || scanSensorBauds()) {
    return true;
  }
  sendError(cmd, "sensor_not_found");
  return false;
}

uint8_t waitForFingerImage(uint32_t timeoutMs) {
  const uint32_t start = millis();
  while (millis() - start < timeoutMs) {
    uint8_t p = finger.getImage();
    if (p == FINGERPRINT_OK) {
      return p;
    }
    if (p != FINGERPRINT_NOFINGER) {
      return p;
    }
    delay(50);
  }
  return FINGERPRINT_TIMEOUT;
}

uint8_t waitForNoFinger(uint32_t timeoutMs) {
  const uint32_t start = millis();
  while (millis() - start < timeoutMs) {
    uint8_t p = finger.getImage();
    if (p == FINGERPRINT_NOFINGER) {
      return FINGERPRINT_OK;
    }
    if (p != FINGERPRINT_OK) {
      return p;
    }
    delay(50);
  }
  return FINGERPRINT_TIMEOUT;
}

bool validId(uint16_t id) {
  uint16_t capacity = finger.capacity;
  if (capacity == 0) {
    capacity = 200;
  }
  return id >= 1 && id <= capacity;
}

void handlePing() {
  JsonDocument doc;
  doc["status"] = "ok";
  doc["device"] = "esp32-fingerprint";
  sendJson(doc);
}

void handleInfo() {
  bool found = scanSensorBauds();

  JsonDocument doc;
  doc["sensor_found"] = found;

  if (found) {
    doc["status"] = "ok";
    doc["baud"] = activeFingerBaud;
    doc["uart"] = uartConfigName(activeUartConfig);
    doc["templates"] = finger.templateCount;
    doc["capacity"] = finger.capacity;
  } else {
    doc["status"] = "error";
    JsonArray triedBauds = doc["tried_bauds"].to<JsonArray>();
    for (uint32_t baud : SENSOR_REPORT_BAUDS) {
      triedBauds.add(baud);
    }
    JsonArray triedUart = doc["tried_uart"].to<JsonArray>();
    triedUart.add("8N1");
    triedUart.add("8N2");
  }
  sendJson(doc);
}

void handleCount() {
  if (!ensureSensor("count")) {
    return;
  }

  uint8_t p = finger.getTemplateCount();
  if (p != FINGERPRINT_OK) {
    sendSensorError("count", "get_template_count", p);
    return;
  }

  JsonDocument doc;
  doc["status"] = "ok";
  doc["cmd"] = "count";
  doc["templates"] = finger.templateCount;
  sendJson(doc);
}

void handleDelete(JsonDocument &in) {
  if (!ensureSensor("delete")) {
    return;
  }

  uint16_t id = in["id"] | 0;
  if (!validId(id)) {
    sendError("delete", "invalid_id");
    return;
  }

  uint8_t p = finger.deleteModel(id);
  if (p != FINGERPRINT_OK) {
    sendSensorError("delete", "delete_model", p);
    return;
  }

  JsonDocument doc;
  doc["status"] = "ok";
  doc["cmd"] = "delete";
  doc["id"] = id;
  sendJson(doc);
}

void handleEmpty() {
  if (!ensureSensor("empty")) {
    return;
  }

  uint8_t p = finger.emptyDatabase();
  if (p != FINGERPRINT_OK) {
    sendSensorError("empty", "empty_database", p);
    return;
  }

  JsonDocument doc;
  doc["status"] = "ok";
  doc["cmd"] = "empty";
  sendJson(doc);
}

void handleIdentify(JsonDocument &in) {
  if (!ensureSensor("identify")) {
    return;
  }

  uint32_t timeoutMs = in["timeout_ms"] | 10000;
  timeoutMs = constrain(timeoutMs, 100UL, 60000UL);

  uint8_t p = waitForFingerImage(timeoutMs);
  if (p == FINGERPRINT_TIMEOUT) {
    JsonDocument doc;
    doc["status"] = "timeout";
    doc["cmd"] = "identify";
    sendJson(doc);
    return;
  }
  if (p != FINGERPRINT_OK) {
    sendSensorError("identify", "get_image", p);
    return;
  }

  p = finger.image2Tz(1);
  if (p != FINGERPRINT_OK) {
    sendSensorError("identify", "image_to_template", p);
    return;
  }

  p = finger.fingerFastSearch();
  if (p == FINGERPRINT_OK) {
    JsonDocument doc;
    doc["status"] = "ok";
    doc["cmd"] = "identify";
    doc["found"] = true;
    doc["id"] = finger.fingerID;
    doc["confidence"] = finger.confidence;
    sendJson(doc);
    return;
  }

  if (p == FINGERPRINT_NOTFOUND) {
    JsonDocument doc;
    doc["status"] = "ok";
    doc["cmd"] = "identify";
    doc["found"] = false;
    sendJson(doc);
    return;
  }

  sendSensorError("identify", "search", p);
}

void handleEnroll(JsonDocument &in) {
  if (!ensureSensor("enroll")) {
    return;
  }

  uint16_t id = in["id"] | 0;
  if (!validId(id)) {
    sendError("enroll", "invalid_id");
    return;
  }

  sendProgress("place_finger_1");
  uint8_t p = waitForFingerImage(30000);
  if (p == FINGERPRINT_TIMEOUT) {
    sendError("enroll", "timeout_waiting_finger_1");
    return;
  }
  if (p != FINGERPRINT_OK) {
    sendSensorError("enroll", "get_image_1", p);
    return;
  }

  p = finger.image2Tz(1);
  if (p != FINGERPRINT_OK) {
    sendSensorError("enroll", "image_to_template_1", p);
    return;
  }

  sendProgress("remove_finger");
  p = waitForNoFinger(15000);
  if (p == FINGERPRINT_TIMEOUT) {
    sendError("enroll", "timeout_waiting_remove_finger");
    return;
  }
  if (p != FINGERPRINT_OK) {
    sendSensorError("enroll", "wait_remove_finger", p);
    return;
  }

  delay(600);
  sendProgress("place_finger_2");
  p = waitForFingerImage(30000);
  if (p == FINGERPRINT_TIMEOUT) {
    sendError("enroll", "timeout_waiting_finger_2");
    return;
  }
  if (p != FINGERPRINT_OK) {
    sendSensorError("enroll", "get_image_2", p);
    return;
  }

  p = finger.image2Tz(2);
  if (p != FINGERPRINT_OK) {
    sendSensorError("enroll", "image_to_template_2", p);
    return;
  }

  p = finger.createModel();
  if (p != FINGERPRINT_OK) {
    sendSensorError("enroll", "create_model", p);
    return;
  }

  sendProgress("saving");
  p = finger.storeModel(id);
  if (p != FINGERPRINT_OK) {
    sendSensorError("enroll", "store_model", p);
    return;
  }

  JsonDocument doc;
  doc["status"] = "ok";
  doc["cmd"] = "enroll";
  doc["id"] = id;
  sendJson(doc);
}

void processLine(const String &line) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, line);
  if (err) {
    sendError("", "invalid_json");
    return;
  }

  const char *cmd = doc["cmd"] | "";
  if (strcmp(cmd, "ping") == 0) {
    handlePing();
  } else if (strcmp(cmd, "info") == 0) {
    handleInfo();
  } else if (strcmp(cmd, "enroll") == 0) {
    handleEnroll(doc);
  } else if (strcmp(cmd, "identify") == 0) {
    handleIdentify(doc);
  } else if (strcmp(cmd, "delete") == 0) {
    handleDelete(doc);
  } else if (strcmp(cmd, "empty") == 0) {
    handleEmpty();
  } else if (strcmp(cmd, "count") == 0) {
    handleCount();
  } else {
    sendError(cmd, "unknown_command");
  }
}

void readUsbSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') {
      continue;
    }

    if (c == '\n') {
      inputLine.trim();
      if (inputLine.length() > 0) {
        processLine(inputLine);
      }
      inputLine = "";
      continue;
    }

    if (inputLine.length() < 512) {
      inputLine += c;
    } else {
      inputLine = "";
      sendError("", "line_too_long");
    }
  }
}

void setup() {
  pinMode(TOUCH_PIN, INPUT);

  Serial.begin(USB_BAUD);
  Serial.setTimeout(100);

  FingerSerial.begin(FINGER_BAUD, SERIAL_8N1, FINGER_RX_PIN, FINGER_TX_PIN);
  sensorFound = scanSensorBauds();
}

void loop() {
  readUsbSerial();
  delay(2);
}
