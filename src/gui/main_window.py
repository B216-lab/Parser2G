import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QTabWidget
from tabs.parsing_addresses_tab import ParsingAddressesTab
from tabs.parsing_buildings_tab import ParsingBuildingsTab
from tabs.export_tab import ExportTab
from tabs.settings_tab import SettingsTab

from logic.parsing_addresses import ParsingAddresses
from presenters.parsing_addresses_presenter import ParsingAddressesPresenter


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Парсер и Экспорт данных")
        self.resize(900, 600)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Вкладка Парсинг адресов
        self.parsing_addresses_view = ParsingAddressesTab()
        self.parsing_addresses_model = ParsingAddresses(
            log=self.parsing_addresses_view.log_box.append
        )
        self.parsing_addresses_presenter = ParsingAddressesPresenter(
            logic=self.parsing_addresses_model, view=self.parsing_addresses_view
        )

        self.tabs.addTab(self.parsing_addresses_view, "Парсинг адресов")
        self.tabs.addTab(ParsingBuildingsTab(), "Парсинг зданий")
        self.tabs.addTab(ExportTab(), "Экспорт")
        self.tabs.addTab(SettingsTab(), "Настройки")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
