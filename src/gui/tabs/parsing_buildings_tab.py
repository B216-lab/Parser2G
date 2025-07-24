from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel

class ParsingBuildingsTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        label = QLabel("Здесь будет функционал парсинга зданий")
        layout.addWidget(label)
