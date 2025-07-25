from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QHBoxLayout,
    QLineEdit,
)
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
from presenters.parsing_addresses_presenter import ParsingAddressesPresenter


class ParsingAddressesTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)

        # Поле для ввода url города и кнопка "Проверить"
        layout_city = QHBoxLayout()
        self.field_city = QLineEdit()
        self.field_city.setPlaceholderText("Введите url города с Ginfo")
        self.btn_check_city = QPushButton("Проверить")
        self.btn_save_city = QPushButton("Сохранить город в БД")
        layout_city.addWidget(self.field_city)
        layout_city.addWidget(self.btn_check_city)
        layout_city.addWidget(self.btn_save_city)
        layout.addLayout(layout_city)

        # Кнопки для парсинга
        layout_parsing = QHBoxLayout()
        self.btn_parse_districts = QPushButton("Парсить районы")
        self.btn_parse_streets = QPushButton("Парсить улицы")
        layout_parsing.addWidget(self.btn_parse_districts)
        layout_parsing.addWidget(self.btn_parse_streets)
        layout.addLayout(layout_parsing)

        # Текстовые поля для отображения количества
        layout_counters = QHBoxLayout()
        self.count_districts = QLineEdit()
        self.count_districts.setReadOnly(True)
        self.count_districts.setPlaceholderText("Получено районов")
        self.count_streets = QLineEdit()
        self.count_streets.setReadOnly(True)
        self.count_streets.setPlaceholderText("Получено улиц")
        self.count_addresses = QLineEdit()
        self.count_addresses.setReadOnly(True)
        self.count_addresses.setPlaceholderText("Получено домов")
        layout_counters.addWidget(self.count_districts)
        layout_counters.addWidget(self.count_streets)
        layout_counters.addWidget(self.count_addresses)
        layout.addLayout(layout_counters)

        # Кнопки для сохранения и сброса данных
        layout_data = QHBoxLayout()
        self.btn_save_data = QPushButton("Записать полученные данные в БД")
        self.btn_reset_data = QPushButton("Сбросить данные")
        layout_data.addWidget(self.btn_save_data)
        layout_data.addWidget(self.btn_reset_data)
        layout.addLayout(layout_data)

        # Окно логов
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        layout.addWidget(self.log_box)

        # Подключение обработчиков
        self.on_parse_districts = None
        self.on_parse_streets = None
        self.on_reset_data = None
        self.btn_parse_districts.clicked.connect(self._handle_parse_districts)
        self.btn_parse_streets.clicked.connect(self._handle_parse_streets)
        self.btn_reset_data.clicked.connect(self._handle_reset_data)

    def _handle_reset_data(self):
        from PyQt6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "Подтверждение сброса",
            "Точно хотите сбросить данные?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            pass

    def _handle_parse_districts(self):
        if self.on_parse_districts:
            self.on_parse_districts()

    def _handle_parse_streets(self):
        if self.on_parse_streets:
            self.on_parse_streets()

    def update_counters(self, count_districts=None, count_streets=None, count_addresses=None):
        if count_districts is not None:
            self.count_districts.setText(f"Получено районов: {count_districts}")
        if count_streets is not None:
            self.count_streets.setText(f"Получено улиц: {count_streets}")
        if count_addresses is not None:
            self.count_addresses.setText(f"Получено домов: {count_addresses}")
