import tkinter as tk
from tkinter import filedialog, scrolledtext
from src.core.export_logic import export_data
from src.database.db_scripts.gets.get_cities_db import get_all_cities
from src.database.db_scripts.gets.get_districts_db import get_districts_for_city  # новый импорт

class ExportApp:
    def __init__(self, root):
        self.root = root
        self.root.title("📤 Экспорт данных из БД")
        self.root.geometry("600x450")

        # Формат файла
        self.format_var = tk.StringVar(value="json")

        radio_frame = tk.Frame(root)
        radio_frame.pack(pady=10, anchor="w", padx=10)
        tk.Label(radio_frame, text="Формат выгрузки:").pack(side="left")
        tk.Radiobutton(radio_frame, text="JSON", variable=self.format_var, value="json").pack(side="left", padx=5)
        tk.Radiobutton(radio_frame, text="CSV", variable=self.format_var, value="csv").pack(side="left", padx=5)

        # Фильтр по городу
        filter_frame = tk.Frame(root)
        filter_frame.pack(pady=10, anchor="w", padx=10)
        tk.Label(filter_frame, text="Фильтр по городу:").pack(side="left")

        self.city_var = tk.StringVar(value="Все")
        cities = ["Все"] + get_all_cities()
        self.city_menu = tk.OptionMenu(filter_frame, self.city_var, *cities, command=self.on_city_change)
        self.city_menu.pack(side="left", padx=5)

        # Фильтр по району
        district_frame = tk.Frame(root)
        district_frame.pack(pady=10, anchor="w", padx=10)
        tk.Label(district_frame, text="Фильтр по району:").pack(side="left")

        self.district_var = tk.StringVar(value="Все")
        self.district_menu = tk.OptionMenu(district_frame, self.district_var, "Все")
        self.district_menu.pack(side="left", padx=5)

        # Кнопка экспорта
        export_button = tk.Button(root, text="💾 Экспортировать", command=self.export_data)
        export_button.pack(pady=10)

        # Окно логов
        self.log_box = scrolledtext.ScrolledText(root, wrap=tk.WORD, height=10)
        self.log_box.pack(fill="both", expand=True, padx=10, pady=10)

    def on_city_change(self, selected_city):
        # При выборе города обновляем список районов
        if selected_city == "Все":
            districts = ["Все"]
        else:
            districts = ["Все"] + get_districts_for_city(selected_city)

        self.district_var.set("Все")

        menu = self.district_menu["menu"]
        menu.delete(0, "end")

        for d in districts:
            menu.add_command(label=d, command=lambda value=d: self.district_var.set(value))

    def export_data(self):
        fmt = self.format_var.get()
        city = self.city_var.get()
        district = self.district_var.get()
        filters = {}

        if city != "Все":
            filters["city"] = city
        if district != "Все":
            filters["district"] = district

        filetypes = [("JSON files", "*.json")] if fmt == "json" else [("CSV files", "*.csv")]
        extension = "json" if fmt == "json" else "csv"

        file_path = filedialog.asksaveasfilename(defaultextension=f".{extension}", filetypes=filetypes)
        if not file_path:
            self.log("⚠️ Отменено пользователем.")
            return

        try:
            count = export_data(file_path, fmt, filters)
            self.log(f"✅ Успешно выгружено {count} записей в {file_path}")
        except Exception as e:
            self.log(f"❌ Ошибка при экспорте: {e}")

    def log(self, message):
        self.log_box.insert(tk.END, message + "\n")
        self.log_box.see(tk.END)

def main():
    root = tk.Tk()
    app = ExportApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
