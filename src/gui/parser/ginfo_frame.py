import threading
import tkinter as tk
from src.parsers.ginfo.get_districts import get_districts
from src.parsers.ginfo.get_streets_district import get_street_links
from src.parsers.ginfo.get_houses_coords_district import get_addresses
from scripts.parse_2gis import get_build_orgs

class GinfoFrame(tk.LabelFrame):
    def __init__(self, parent, log_func):
        super().__init__(parent, text="GINFO (Для агломерации Иркутска)", padx=10, pady=10)
        self.log = log_func
        self.get_districts_func = get_districts
        self.parse_streets_func = get_street_links
        self.parse_addresses_func = get_addresses

        self.districts = []
        self.streets = []
        self.addresses = []
        self.selected_district = tk.StringVar()
        self.selected_district.set("Выберите округ")
        self.url_selected_district = ''  # тут будет url выбранного района

        # Кнопка получить районы
        self.get_district_button = tk.Button(self, text="🗺️ Получить округи", command=self.run_districts)
        self.get_district_button.pack(side="left", padx=5)

        # Выпадающий список для выбора района
        self.district_menu = tk.OptionMenu(self, self.selected_district, () )
        self.district_menu.config(state="disabled")
        self.district_menu.pack(side="left", padx=5)

        # Кнопки парсинга улиц и адресов
        self.streets_button = tk.Button(self, text="📍 Получить улицы", command=self.run_streets, state="disabled")
        self.streets_button.pack(side="left", padx=5)

        self.addresses_button = tk.Button(self, text="🏠 Получить адреса", command=self.run_addresses, state="disabled")
        self.addresses_button.pack(side="left", padx=5)

        self.builds_orgs_button = tk.Button(self, text="🏢 Парсинг зданий и организаций", command=self.run_builds_orgs, state="disabled")
        self.builds_orgs_button.pack(side="left", padx=5)

    def _on_district_selected(self, name):
        self.selected_district.set(name)
        self.url_selected_district = self._district_name_to_url.get(name, '')
        self.log(f"ℹ️ Выбран округ: {name}, url: {self.url_selected_district}")
        self.streets_button.config(state="normal")

    def run_districts(self):
        self.log("🔄 GINFO: Получение округов города...")

        # Блокируем кнопки и выпадающий список на время загрузки
        self.get_district_button.config(state="disabled")
        self.district_menu.config(state="disabled")
        self.streets_button.config(state="disabled")
        self.addresses_button.config(state="disabled")

        def task():
            try:
                self.districts = self.get_districts_func()

                # Обновление меню — нужно делать в основном потоке UI!
                def update_menu():
                    menu = self.district_menu["menu"]
                    menu.delete(0, "end")
                    self._district_name_to_url = {}

                    for district in self.districts:
                        name = district['name']
                        url = district.get('url', '')
                        self._district_name_to_url[name] = url

                        menu.add_command(
                            label=name,
                            command=lambda n=name: self._on_district_selected(n)
                        )

                    if self.districts:
                        first_name = self.districts[0]['name']
                        self.selected_district.set(first_name)
                        self.url_selected_district = self._district_name_to_url.get(first_name, '')

                    for district in self.districts:
                        self.log(f"{district['name']}")

                    self.log(f"✅ Найдено округов: {len(self.districts)}")

                    # Разблокируем кнопки и выпадающий список после загрузки
                    self.get_district_button.config(state="normal")
                    self.district_menu.config(state="normal")
                    # self.streets_button.config(state="normal")
                    # self.addresses_button.config(state="normal")

                # В Tkinter обновлять UI из другого потока нельзя — используем after
                self.district_menu.after(0, update_menu)

            except Exception as e:
                self.log(f"❌ Ошибка при получении округов: {e}")

                # Разблокируем кнопки и выпадающий список в случае ошибки
                self.get_district_button.config(state="normal")
                self.district_menu.config(state="normal")
                self.streets_button.config(state="normal")
                self.addresses_button.config(state="normal")

        threading.Thread(target=task, daemon=True).start()

    def run_streets(self):
        district = self.selected_district.get()
        if district == "Выберите округ":
            self.log("⚠️ Пожалуйста, выберите округ перед парсингом улиц.")
            return
        self.log(f"🔄 GINFO: Запуск парсинга улиц района: {district} (URL: {self.url_selected_district})...")

        def task():
            try:
                if self.parse_streets_func:
                    self.streets = self.parse_streets_func(self.url_selected_district, self.log, mock=True)
                    self.log(f"✅ Парсинг завершён. Найдено улиц: {len(self.streets)}")
                    self.log(f"✅ {len(self.streets)} улиц записано в базу данных")
                    self.addresses_button.config(state="normal")
                else:
                    self.log("⚠️ Функция парсинга улиц не задана.")
            except Exception as e:
                self.log(f"❌ Ошибка при парсинге улиц: {e}")

        threading.Thread(target=task, daemon=True).start()

    def run_addresses(self):
        district = self.selected_district.get()
        if district == "Выберите район":
            self.log("⚠️ Пожалуйста, выберите район перед парсингом адресов.")
            return
        self.log(f"🔄 GINFO: Запуск парсинга адресов района: {district} (URL: {self.url_selected_district})...")

        def task():
            try:
                if self.parse_addresses_func:
                    self.addresses = self.parse_addresses_func(self.streets, self.log, mock=True)
                    self.log(f"✅ Парсинг завершён. Найдено адресов: {len(self.addresses)}")
                    self.log(f"✅ {len(self.addresses)} адресов записано в базу данных")
                    self.builds_orgs_button.config(state="normal")
                else:
                    self.log("⚠️ Функция парсинга адресов не задана.")
            except Exception as e:
                self.log(f"❌ Ошибка при парсинге адресов: {e}")

        threading.Thread(target=task, daemon=True).start()

    def run_builds_orgs(self):
        def task():
            try:
                get_build_orgs(self.log)
                self.log(f"✅ Парсинг завершён")

            except Exception as e:
                self.log(f"❌ Ошибка при парсинге зданий и организаций: {e}")

        threading.Thread(target=task, daemon=True).start()
