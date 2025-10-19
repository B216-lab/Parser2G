import requests
from bs4 import BeautifulSoup
import json
import os
import urllib3
from typing import List, Dict, Optional

# Отключаем предупреждения о неверифицированных SSL сертификатах
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

headers = {"User-Agent": "Mozilla/5.0"}


class GinfoParser:
    def __init__(self, log=None, base_url="https://irkutsk.ginfo.ru"):
        self.log = log if log else print
        self.BASE_URL = base_url
        self.session = requests.Session()
        self.session.headers.update(headers)
        self.session.verify = False

    def _make_request(self, url: str) -> Optional[BeautifulSoup]:
        """Универсальный метод для выполнения HTTP запросов"""
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
        except requests.exceptions.RequestException as e:
            self.log(f"Ошибка при запросе {url}: {e}")
            return None

    # --- Извлечение районов ---
    def get_districts(self) -> List[Dict[str, str]]:
        """
        Returns:
            List[Dict[str, str]]: Список словарей с названием и url района
        """
        self.log("Поиск районов")
        url = f"{self.BASE_URL}/rayoni"
        self.log(f"Запрос: {url}")
        
        soup = self._make_request(url)
        if not soup:
            return []

        districts = []
        for idx, a in enumerate(soup.find_all("a", class_="rayon_link"), start=1):
            name = a.text.strip()
            url = self.BASE_URL + a["href"]
            districts.append({"name": name, "url": url})
            self.log(f"[{idx}] Найден район: {name}")
        
        self.save_to_temp("districts", districts)
        self.log(f"Найдено районов: {len(districts)}")
        return districts

    # --- Извлечение url улиц ---
    def get_streets_links(self, district_url: str) -> List[str]:
        """
        Args:
            district_url (str): URL страницы района

        Returns:
            List[str]: Список URL улиц
        """
        soup = self._make_request(district_url)
        if not soup:
            return []

        # Ищем ссылку "все улицы"
        show_all_a = soup.find(
            "a",
            class_="show_all",
            string=lambda s: s and "все улицы" in s.lower() if s else False
        )
        
        if not show_all_a:
            self.log(f"Ссылка 'все улицы' не найдена для района: {district_url}")
            return []

        streets_url = self.BASE_URL + show_all_a["href"]
        soup_streets = self._make_request(streets_url)
        if not soup_streets:
            return []

        streets_links = []
        for idx, a in enumerate(soup_streets.find_all("a", class_="ulica_link"), start=1):
            url = self.BASE_URL + a["href"]
            streets_links.append(url)
        
        self.log(f"Найдено улиц: {len(streets_links)}")
        return streets_links

    def get_street_info(self, street_url: str) -> Optional[Dict]:
        """
        Args:
            street_url (str): URL страницы улицы

        Returns:
            Dict: Информация об улице
        """
        soup = self._make_request(street_url)
        if not soup:
            return None

        info_block = soup.find("table", class_="ulica_info")
        if not info_block:
            self.log(f"Информационный блок не найден для улицы: {street_url}")
            return None

        # Функция для извлечения данных из таблицы
        def get_table_value(th_text: str) -> Optional[str]:
            th = info_block.find("th", string=th_text)
            if th and th.find_next_sibling("td"):
                return th.find_next_sibling("td").text.strip()
            return None

        # Извлекаем данные
        type_street = get_table_value("Тип")
        name = get_table_value("Название")
        city = get_table_value("Город")
        district = get_table_value("Округ")
        length = get_table_value("Протяженность")
        count_buildings = get_table_value("Кол-во строений")
        crossroads = get_table_value("Перекрестки")

        # Собираем номера домов
        numbers_houses = []
        for dom_a in soup.find_all("a", class_="dom_link"):
            dom_text = dom_a.text.strip()
            if dom_text:
                numbers_houses.append(dom_text)

        street_info = {
            "type_street": type_street,
            "name": name,
            "city": city,
            "district": district,
            "length": length,
            "count_buildings": count_buildings,
            "crossroads": crossroads,
            "numbers_houses": numbers_houses,
            "url": street_url  # Добавляем URL для отслеживания
        }

        self.log(f"Обработана улица: {name}")
        return street_info

    def get_all_streets_info(self) -> List[Dict]:
        """Получить информацию о всех улицах всех районов"""
        districts = self.get_districts()
        all_streets_info = []
        
        for district in districts:
            self.log(f"Обрабатывается район: {district['name']}")
            streets_links = self.get_streets_links(district["url"])
            
            for street_url in streets_links:
                street_info = self.get_street_info(street_url)
                if street_info:
                    all_streets_info.append(street_info)
            
            # Сохраняем промежуточные результаты после каждого района
            self.save_to_temp(f"streets_{district['name']}", all_streets_info)
        
        # Сохраняем финальный результат
        self.save_to_temp("all_streets", all_streets_info)
        self.log(f"Всего обработано улиц: {len(all_streets_info)}")
        return all_streets_info

    def save_to_temp(self, name_file: str, data, append: bool = False):
        """
        Сохраняет данные в JSON-файл во временной папке.
        """
        name_file = f"{name_file}.json"
        temp_dir = os.path.join(os.path.dirname(__file__), "../../../data/temp/ginfo")
        os.makedirs(temp_dir, exist_ok=True)
        temp_file = os.path.join(temp_dir, name_file)
        
        try:
            if append and os.path.exists(temp_file):
                with open(temp_file, "r", encoding="utf-8") as f:
                    try:
                        existing_data = json.load(f)
                    except json.JSONDecodeError:
                        existing_data = []
                
                if isinstance(existing_data, list) and isinstance(data, list):
                    data = existing_data + data
            
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
                
            self.log(f"Данные сохранены в: {temp_file}")
            
        except Exception as e:
            self.log(f"Ошибка при сохранении данных: {e}")

    def clear_temp_file(self, name_file: str):
        """
        Очищает указанный JSON-файл
        """
        name_file = f"{name_file}.json"
        temp_dir = os.path.join(os.path.dirname(__file__), "../../../data/temp/ginfo")
        temp_file = os.path.join(temp_dir, name_file)
        
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=4)
            self.log(f"Файл {temp_file} очищен.")
        except Exception as e:
            self.log(f"Ошибка при очистке файла: {e}")


if __name__ == "__main__":
    ginfo_parser = GinfoParser()
    
    # Получаем всю информацию об улицах
    all_streets = ginfo_parser.get_all_streets_info()
    
    # Или можно работать поэтапно:
    # districts = ginfo_parser.get_districts()
    # for district in districts:
    #     streets_links = ginfo_parser.get_streets_links(district["url"])
    #     for street_url in streets_links:
    #         street_info = ginfo_parser.get_street_info(street_url)