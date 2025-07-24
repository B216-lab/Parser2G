from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel

class ExportTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        label = QLabel("Здесь будет функционал экспорта данных")
        layout.addWidget(label)
