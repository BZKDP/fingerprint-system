import json
import queue
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import serial
from serial.tools import list_ports

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "fingerprints.db"
USB_BAUD = 115200


PROGRESS_TEXT = {
    "place_finger_1": "Приложите палец первый раз",
    "remove_finger": "Уберите палец",
    "place_finger_2": "Приложите палец второй раз",
    "saving": "Сохраняю",
}

FINGER_LABELS = [
    "",
    "Правый большой",
    "Правый указательный",
    "Правый средний",
    "Правый безымянный",
    "Правый мизинец",
    "Левый большой",
    "Левый указательный",
    "Левый средний",
    "Левый безымянный",
    "Левый мизинец",
]


class FingerprintDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                finger_id INTEGER UNIQUE NOT NULL,
                name TEXT NOT NULL,
                finger_label TEXT,
                comment TEXT,
                created_at TEXT
            )
            """
        )
        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(people)").fetchall()
        }
        if "finger_label" not in columns:
            self.conn.execute("ALTER TABLE people ADD COLUMN finger_label TEXT")
        self.conn.commit()

    def list_people(self):
        rows = self.conn.execute(
            """
            SELECT id, finger_id, name, COALESCE(finger_label, '') AS finger_label,
                   comment, created_at
            FROM people
            ORDER BY name COLLATE NOCASE, finger_id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def get_by_finger_id(self, finger_id: int):
        row = self.conn.execute(
            """
            SELECT id, finger_id, name, COALESCE(finger_label, '') AS finger_label,
                   comment, created_at
            FROM people
            WHERE finger_id = ?
            """,
            (finger_id,),
        ).fetchone()
        return dict(row) if row else None

    def add_person(self, finger_id: int, name: str, finger_label: str, comment: str):
        self.conn.execute(
            """
            INSERT INTO people (finger_id, name, finger_label, comment, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                finger_id,
                name,
                finger_label,
                comment,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()

    def list_names(self):
        rows = self.conn.execute(
            "SELECT DISTINCT name FROM people ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [str(row["name"]) for row in rows]

    def delete_by_finger_id(self, finger_id: int):
        self.conn.execute("DELETE FROM people WHERE finger_id = ?", (finger_id,))
        self.conn.commit()

    def clear(self):
        self.conn.execute("DELETE FROM people")
        self.conn.commit()

    def used_finger_ids(self):
        rows = self.conn.execute("SELECT finger_id FROM people ORDER BY finger_id").fetchall()
        return {int(row["finger_id"]) for row in rows}

    def next_free_finger_id(self, capacity: int = 200):
        used = self.used_finger_ids()
        for finger_id in range(1, max(1, capacity) + 1):
            if finger_id not in used:
                return finger_id
        return max(used or {0}) + 1


class SerialController(QObject):
    connectedChanged = Signal(bool, str)
    messageReceived = Signal(dict, str)
    progressReceived = Signal(str)
    logLine = Signal(str)

    def __init__(self):
        super().__init__()
        self._commands = queue.Queue()
        self._stop = threading.Event()
        self._identify_enabled = threading.Event()
        self._thread = None
        self._serial = None

    def connect_port(self, port: str):
        self.disconnect_port()
        self._commands = queue.Queue()
        self._stop.clear()
        self._identify_enabled.clear()
        self._thread = threading.Thread(target=self._run, args=(port,), daemon=True)
        self._thread.start()

    def disconnect_port(self):
        self._stop.set()
        self._identify_enabled.clear()
        try:
            if self._serial and self._serial.is_open:
                self._serial.close()
        except serial.SerialException:
            pass

    def send_command(self, command: dict, context: str):
        self._commands.put((command, context))

    def set_identify_enabled(self, enabled: bool):
        if enabled:
            self._identify_enabled.set()
        else:
            self._identify_enabled.clear()

    def _run(self, port: str):
        try:
            self._serial = serial.Serial(port, USB_BAUD, timeout=0.2, write_timeout=2)
            time.sleep(3.0)
            self._serial.reset_input_buffer()
            self.connectedChanged.emit(True, port)
            self.logLine.emit(f"Connected to {port} at {USB_BAUD}")
        except serial.SerialException as exc:
            self.connectedChanged.emit(False, f"{port}: {exc}")
            return

        next_identify_at = time.monotonic() + 1.0
        try:
            while not self._stop.is_set():
                try:
                    command, context = self._commands.get(timeout=0.05)
                except queue.Empty:
                    if self._identify_enabled.is_set() and time.monotonic() >= next_identify_at:
                        command = {"cmd": "identify", "timeout_ms": 1500}
                        context = "identify"
                    else:
                        continue

                self._execute(command, context)
                if context == "identify":
                    next_identify_at = time.monotonic() + 0.5
        finally:
            try:
                if self._serial and self._serial.is_open:
                    self._serial.close()
            finally:
                self.connectedChanged.emit(False, "disconnected")
                self.logLine.emit("Disconnected")

    def _execute(self, command: dict, context: str):
        if not self._serial or not self._serial.is_open:
            self.messageReceived.emit(
                {"status": "error", "cmd": command.get("cmd", ""), "error": "serial_not_open"},
                context,
            )
            return

        line = json.dumps(command, ensure_ascii=False, separators=(",", ":"))
        try:
            self._serial.reset_input_buffer()
            self._serial.write((line + "\n").encode("utf-8"))
            self._serial.flush()
            self.logLine.emit(f"> {line}")
        except serial.SerialException as exc:
            self.messageReceived.emit(
                {"status": "error", "cmd": command.get("cmd", ""), "error": str(exc)}, context
            )
            self._stop.set()
            return

        deadline = time.monotonic() + self._deadline_seconds(command)
        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                raw = self._serial.readline()
            except serial.SerialException as exc:
                self.messageReceived.emit(
                    {"status": "error", "cmd": command.get("cmd", ""), "error": str(exc)},
                    context,
                )
                self._stop.set()
                return

            if not raw:
                continue

            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            self.logLine.emit(f"< {text}")

            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                self.logLine.emit(f"Invalid JSON from device: {text}")
                continue

            if payload.get("status") == "progress":
                self.progressReceived.emit(str(payload.get("message", "")))
                continue

            self.messageReceived.emit(payload, context)
            return

        self.messageReceived.emit(
            {"status": "error", "cmd": command.get("cmd", ""), "error": "response_timeout"},
            context,
        )

    @staticmethod
    def _deadline_seconds(command: dict):
        cmd = command.get("cmd")
        if cmd == "identify":
            return max(3.0, float(command.get("timeout_ms", 1500)) / 1000.0 + 3.0)
        if cmd == "enroll":
            return 95.0
        if cmd == "ping":
            return 18.0
        if cmd == "info":
            return 45.0
        return 8.0


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = FingerprintDatabase(DB_PATH)
        self.serial = SerialController()
        self.capacity = 200
        self.pending_enroll = None
        self.pending_delete_id = None
        self.pending_empty = False

        self.setWindowTitle("ESP32 Fingerprint System")
        self.resize(980, 680)

        self._build_ui()
        self._connect_signals()
        self.refresh_ports()
        self.refresh_people()
        self._choose_next_id()

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)

        connection_box = QGroupBox("Подключение")
        connection_layout = QHBoxLayout(connection_box)
        self.port_combo = QComboBox()
        self.refresh_ports_button = QPushButton("Обновить")
        self.connect_button = QPushButton("Подключиться")
        self.connection_status = QLabel("Не подключено")
        self.sensor_status = QLabel("Сканер: неизвестно")
        connection_layout.addWidget(QLabel("COM-порт"))
        connection_layout.addWidget(self.port_combo, 1)
        connection_layout.addWidget(self.refresh_ports_button)
        connection_layout.addWidget(self.connect_button)
        connection_layout.addWidget(self.connection_status)
        connection_layout.addWidget(self.sensor_status)
        layout.addWidget(connection_box)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_main_tab(), "Главный экран")
        self.tabs.addTab(self._build_add_tab(), "Добавление")
        self.tabs.addTab(self._build_people_tab(), "Управление")
        self.tabs.addTab(self._build_log_tab(), "Логи")
        layout.addWidget(self.tabs, 1)

        self.setCentralWidget(root)

    def _build_main_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignTop)

        self.main_status = QLabel("Подключите ESP32")
        font = self.main_status.font()
        font.setPointSize(24)
        font.setBold(True)
        self.main_status.setFont(font)
        self.main_status.setAlignment(Qt.AlignCenter)
        self.main_status.setWordWrap(True)

        self.result_label = QLabel("")
        result_font = self.result_label.font()
        result_font.setPointSize(16)
        self.result_label.setFont(result_font)
        self.result_label.setAlignment(Qt.AlignCenter)
        self.result_label.setWordWrap(True)

        self.id_label = QLabel("")
        self.id_label.setAlignment(Qt.AlignCenter)

        layout.addStretch(1)
        layout.addWidget(self.main_status)
        layout.addWidget(self.result_label)
        layout.addWidget(self.id_label)
        layout.addStretch(2)
        return page

    def _build_add_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        form_box = QGroupBox("Новый отпечаток")
        form = QFormLayout(form_box)
        self.name_edit = QComboBox()
        self.name_edit.setEditable(True)
        self.name_edit.setInsertPolicy(QComboBox.NoInsert)
        self.finger_label_combo = QComboBox()
        self.finger_label_combo.setEditable(True)
        self.finger_label_combo.addItems(FINGER_LABELS)
        self.comment_edit = QTextEdit()
        self.comment_edit.setFixedHeight(90)
        self.finger_id_spin = QSpinBox()
        self.finger_id_spin.setRange(1, self.capacity)
        self.next_id_button = QPushButton("Авто ID")
        id_row = QHBoxLayout()
        id_row.addWidget(self.finger_id_spin)
        id_row.addWidget(self.next_id_button)
        form.addRow("Имя", self.name_edit)
        form.addRow("Палец", self.finger_label_combo)
        form.addRow("Комментарий", self.comment_edit)
        form.addRow("ID отпечатка", id_row)

        self.enroll_button = QPushButton("Добавить отпечаток")
        self.enroll_status = QLabel("")
        self.enroll_status.setWordWrap(True)

        layout.addWidget(form_box)
        layout.addWidget(self.enroll_button)
        layout.addWidget(self.enroll_status)
        layout.addStretch(1)
        return page

    def _build_people_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.people_table = QTableWidget(0, 6)
        self.people_table.setHorizontalHeaderLabels(
            ["ID записи", "ID отпечатка", "Имя", "Палец", "Комментарий", "Создано"]
        )
        self.people_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.people_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.people_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.people_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        buttons = QHBoxLayout()
        self.delete_button = QPushButton("Удалить человека и отпечаток")
        self.empty_button = QPushButton("Очистить все отпечатки")
        buttons.addWidget(self.delete_button)
        buttons.addWidget(self.empty_button)
        buttons.addStretch(1)

        layout.addWidget(self.people_table, 1)
        layout.addLayout(buttons)
        return page

    def _build_log_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        layout.addWidget(self.log_edit)
        return page

    def _connect_signals(self):
        self.refresh_ports_button.clicked.connect(self.refresh_ports)
        self.connect_button.clicked.connect(self.toggle_connection)
        self.next_id_button.clicked.connect(self._choose_next_id)
        self.enroll_button.clicked.connect(self.start_enroll)
        self.delete_button.clicked.connect(self.delete_selected)
        self.empty_button.clicked.connect(self.empty_all)

        self.serial.connectedChanged.connect(self.on_connected_changed)
        self.serial.messageReceived.connect(self.on_message)
        self.serial.progressReceived.connect(self.on_progress)
        self.serial.logLine.connect(self.append_log)

    def refresh_ports(self):
        current = self.port_combo.currentData()
        self.port_combo.clear()
        ports = list(list_ports.comports())
        for port in ports:
            label = f"{port.device} - {port.description}"
            self.port_combo.addItem(label, port.device)

        preferred = 0
        for idx, port in enumerate(ports):
            text = f"{port.description} {port.hwid}".lower()
            if any(marker in text for marker in ("cp210", "ch340", "ch341", "ch910", "usb")):
                preferred = idx
                break
        if current:
            for idx in range(self.port_combo.count()):
                if self.port_combo.itemData(idx) == current:
                    preferred = idx
                    break
        if self.port_combo.count():
            self.port_combo.setCurrentIndex(preferred)

    def toggle_connection(self):
        if self.connect_button.text() == "Отключиться":
            self.serial.disconnect_port()
            return

        port = self.port_combo.currentData()
        if not port:
            QMessageBox.warning(self, "COM-порт", "COM-порт не выбран.")
            return

        self.connect_button.setEnabled(False)
        self.connection_status.setText(f"Подключение к {port}...")
        self.serial.connect_port(port)

    def on_connected_changed(self, connected: bool, message: str):
        self.connect_button.setEnabled(True)
        if connected:
            self.connection_status.setText(f"Подключено: {message}")
            self.connect_button.setText("Отключиться")
            self.main_status.setText("Приложите палец")
            self.serial.send_command({"cmd": "ping"}, "ping")
            self.serial.send_command({"cmd": "info"}, "info")
        else:
            self.connection_status.setText("Не подключено")
            self.connect_button.setText("Подключиться")
            self.sensor_status.setText("Сканер: неизвестно")
            self.main_status.setText("Подключите ESP32")
            self.result_label.setText("")
            self.id_label.setText("")
            self.serial.set_identify_enabled(False)

    def on_message(self, payload: dict, context: str):
        status = payload.get("status")
        cmd = payload.get("cmd", context)

        if context == "ping":
            if status == "ok":
                self.append_log("Ping OK")
            else:
                self.append_log(f"Ping error: {payload}")
            return

        if context == "info":
            self.handle_info(payload)
            return

        if context == "identify":
            self.handle_identify(payload)
            return

        if context == "enroll":
            self.handle_enroll_result(payload)
            return

        if context == "delete":
            self.handle_delete_result(payload)
            return

        if context == "empty":
            self.handle_empty_result(payload)
            return

        self.append_log(f"{cmd}: {payload}")

    def handle_info(self, payload: dict):
        if payload.get("status") != "ok":
            if payload.get("sensor_found") is False:
                tried = payload.get("tried_bauds") or []
                tried_text = ", ".join(str(item) for item in tried)
                self.sensor_status.setText("Сканер не найден")
                self.main_status.setText("Сканер не найден")
                self.result_label.setText(f"Проверены скорости: {tried_text}")
                self.id_label.setText("")
                self.serial.set_identify_enabled(False)
                self.append_log(f"Scanner not found. Tried bauds: {tried_text}")
                return
            self.sensor_status.setText("Сканер: ошибка")
            self.append_log(f"Info error: {payload}")
            return

        found = bool(payload.get("sensor_found"))
        templates = int(payload.get("templates") or 0)
        capacity = int(payload.get("capacity") or self.capacity)
        if capacity > 0:
            self.capacity = capacity
            self.finger_id_spin.setMaximum(capacity)

        if found:
            baud = payload.get("baud")
            suffix = f", baud {baud}" if baud else ""
            self.sensor_status.setText(f"Сканер найден: {templates}/{self.capacity}{suffix}")
            self.main_status.setText("Приложите палец")
            self.serial.set_identify_enabled(True)
        else:
            self.sensor_status.setText("Сканер не найден")
            self.main_status.setText("Сканер не найден")
            self.serial.set_identify_enabled(False)

        self._choose_next_id()

    def handle_identify(self, payload: dict):
        if payload.get("status") == "timeout":
            self.main_status.setText("Приложите палец")
            self.result_label.setText("")
            self.id_label.setText("")
            return

        if payload.get("status") != "ok":
            self.main_status.setText("Ошибка сканера")
            self.result_label.setText(str(payload.get("error", payload)))
            self.id_label.setText("")
            return

        if payload.get("found") is True:
            finger_id = int(payload.get("id"))
            confidence = int(payload.get("confidence") or 0)
            person = self.db.get_by_finger_id(finger_id)
            if person:
                self.main_status.setText(f"Найден: {person['name']}")
                finger_label = person.get("finger_label") or "палец не указан"
                self.result_label.setText(f"Палец: {finger_label}\nID отпечатка: {finger_id}")
            else:
                self.main_status.setText(f"Отпечаток найден, но человек не привязан. ID: {finger_id}")
                self.result_label.setText(f"ID отпечатка: {finger_id}")
            self.id_label.setText(f"Уверенность: {confidence}")
        else:
            self.main_status.setText("Новый/неизвестный палец")
            self.result_label.setText("Совпадений в сканере нет")
            self.id_label.setText("")

    def start_enroll(self):
        name = self.name_edit.currentText().strip()
        if not name:
            QMessageBox.warning(self, "Имя", "Введите имя.")
            return

        finger_label = self.finger_label_combo.currentText().strip()
        finger_id = int(self.finger_id_spin.value())
        if self.db.get_by_finger_id(finger_id):
            QMessageBox.warning(self, "ID отпечатка", "Этот ID уже есть в локальной базе.")
            return

        self.pending_enroll = {
            "finger_id": finger_id,
            "name": name,
            "finger_label": finger_label,
            "comment": self.comment_edit.toPlainText().strip(),
        }
        self.enroll_button.setEnabled(False)
        self.serial.set_identify_enabled(False)
        self.enroll_status.setText("Начинаю регистрацию...")
        self.serial.send_command({"cmd": "enroll", "id": finger_id}, "enroll")

    def on_progress(self, message: str):
        text = PROGRESS_TEXT.get(message, message)
        self.enroll_status.setText(text)
        self.main_status.setText(text)

    def handle_enroll_result(self, payload: dict):
        self.enroll_button.setEnabled(True)
        self.serial.set_identify_enabled(True)

        if payload.get("status") != "ok":
            error = payload.get("error", "unknown_error")
            self.enroll_status.setText(f"Ошибка регистрации: {error}")
            QMessageBox.warning(self, "Регистрация", f"Ошибка регистрации: {error}")
            self.pending_enroll = None
            return

        enrolled_name = ""
        if self.pending_enroll:
            enrolled_name = self.pending_enroll["name"]
            try:
                self.db.add_person(
                    self.pending_enroll["finger_id"],
                    self.pending_enroll["name"],
                    self.pending_enroll["finger_label"],
                    self.pending_enroll["comment"],
                )
            except sqlite3.IntegrityError:
                QMessageBox.warning(
                    self,
                    "SQLite",
                    "Отпечаток сохранён в сканере, но этот ID уже есть в локальной базе.",
                )
            self.pending_enroll = None

        self.enroll_status.setText("Отпечаток добавлен")
        self.comment_edit.clear()
        self.refresh_people()
        self.name_edit.setEditText(enrolled_name)
        self.finger_label_combo.setEditText("")
        self._choose_next_id()
        self.serial.send_command({"cmd": "info"}, "info")

    def delete_selected(self):
        row = self.people_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Удаление", "Выберите запись в таблице.")
            return

        finger_id_item = self.people_table.item(row, 1)
        if not finger_id_item:
            return
        finger_id = int(finger_id_item.text())
        name = self.people_table.item(row, 2).text()
        finger_label = self.people_table.item(row, 3).text()
        title = f"{name} ({finger_label})" if finger_label else name

        answer = QMessageBox.question(
            self,
            "Удаление",
            f"Удалить {title} и отпечаток ID {finger_id}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        self.pending_delete_id = finger_id
        self.serial.set_identify_enabled(False)
        self.serial.send_command({"cmd": "delete", "id": finger_id}, "delete")

    def handle_delete_result(self, payload: dict):
        self.serial.set_identify_enabled(True)
        if payload.get("status") != "ok":
            QMessageBox.warning(self, "Удаление", f"Ошибка удаления: {payload.get('error')}")
            self.pending_delete_id = None
            return

        if self.pending_delete_id is not None:
            self.db.delete_by_finger_id(self.pending_delete_id)
        self.pending_delete_id = None
        self.refresh_people()
        self._choose_next_id()
        self.serial.send_command({"cmd": "info"}, "info")

    def empty_all(self):
        answer = QMessageBox.question(
            self,
            "Очистить все",
            "Удалить все отпечатки из сканера и очистить SQLite?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        self.pending_empty = True
        self.serial.set_identify_enabled(False)
        self.serial.send_command({"cmd": "empty"}, "empty")

    def handle_empty_result(self, payload: dict):
        self.serial.set_identify_enabled(True)
        if payload.get("status") != "ok":
            QMessageBox.warning(self, "Очистка", f"Ошибка очистки: {payload.get('error')}")
            self.pending_empty = False
            return

        self.db.clear()
        self.pending_empty = False
        self.refresh_people()
        self._choose_next_id()
        self.serial.send_command({"cmd": "info"}, "info")

    def refresh_people(self):
        people = self.db.list_people()
        self.people_table.setRowCount(len(people))
        for row, person in enumerate(people):
            values = [
                person["id"],
                person["finger_id"],
                person["name"],
                person.get("finger_label") or "",
                person.get("comment") or "",
                person.get("created_at") or "",
            ]
            for col, value in enumerate(values):
                self.people_table.setItem(row, col, QTableWidgetItem(str(value)))
        self._refresh_name_suggestions()

    def _choose_next_id(self):
        next_id = self.db.next_free_finger_id(self.capacity)
        next_id = min(max(1, next_id), self.capacity)
        self.finger_id_spin.setValue(next_id)

    def _refresh_name_suggestions(self):
        if not hasattr(self, "name_edit"):
            return
        current = self.name_edit.currentText()
        self.name_edit.blockSignals(True)
        self.name_edit.clear()
        self.name_edit.addItems(self.db.list_names())
        self.name_edit.setEditText(current)
        self.name_edit.blockSignals(False)

    def append_log(self, text: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_edit.appendPlainText(f"[{timestamp}] {text}")

    def closeEvent(self, event):
        self.serial.disconnect_port()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
