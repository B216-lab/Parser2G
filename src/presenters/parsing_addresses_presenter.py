from PyQt6.QtCore import QObject, QThread, pyqtSignal

class ParsingAddressesPresenter:
    def __init__(self, view, logic):
        self.view = view
        self.logic = logic
        
        # Подключаем обработчики
        self.view.on_parse_districts = self.parse_districts
        self.view.on_parse_streets = self.parse_streets
        self.view.on_reset_data = self.reset_data
        
        self.update_counters()

    def parse_districts(self):
        self.logic.parse_districts()
        self.update_counters()

    def parse_streets(self):
        self.logic.parse_streets()
        self.update_counters()
        
    def reset_data(self):
        self.logic.reset_data()
        self.view.update_counters(
            count_districts=0,
            count_streets=0,
            count_addresses=0
        )
        self.view.log_box.append("Данные сброшены")
    
    def update_counters(self):
        self.logic.update_counters()
        self.view.update_counters(
            count_districts=self.logic.count_districts,
            count_streets=self.logic.count_streets,
            count_addresses=self.logic.count_addresses
        )
        