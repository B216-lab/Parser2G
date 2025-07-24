import requests
from bs4 import BeautifulSoup
import json
import os


headers = {
    "User-Agent": "Mozilla/5.0"
}

class GinfoParser:
    def __init__(self, base_url='https://irkutsk.ginfo.ru'):
        self.BASE_URL = base_url

    # --- Извлечение районов ---
    def get_districts(self, start=1):
        """
        Извлекает список районов с главной страницы сайта.

        Args:
            start (int, optional): Начальный индекс для нумерации районов. По умолчанию 1.

        Returns:
            list {name, url}: Список словарей с информацией о районах (имя и URL).
        """
        try:
            response = requests.get(self.BASE_URL, headers=headers, timeout=10,  verify=False)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            main_block = soup.find("div", class_="main_block")
            if not main_block:
                print('Не найден блок с районами')
                return

            district_links = []
            for a in main_block.find_all("a", href=True):
                href = a["href"]
                if href.startswith("/rayoni/") and href != "/rayoni/":
                    district_links.append(a)

            districts = []
            for idx, a in enumerate(district_links, start=start):
                name = a.text.strip()
                url = self.BASE_URL + a["href"]
                districts.append({"name": name, "url": url})
                print(f"[{idx}] Извлечен район: {name}")
            self.save_to_temp('districts', districts)  # Сохраняем в файл
            return districts

        except Exception as e:
            print(f"Ошибка при получении районов: {e}")
            return

    # --- Извлечение улиц ---
    def get_streets(self, district_url, start=1):
        """
        Извлекает список улиц для указанного района.

        Args:
            district_url (str): URL страницы района, для которого нужно получить список улиц.
            start (int, optional): Начальный индекс для нумерации улиц. По умолчанию 1.

        Returns:
            list: Список словарей с информацией об улицах района.
        """
        try:
            # Получаем страницу района
            response = requests.get(district_url, headers=headers, timeout=10, verify=False)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            # Находим на странице ссылку на "все улицы" и переходим по ней, если она есть
            show_all_a = soup.find("a", class_="show_all", string=lambda s: s and "все улицы" in s.lower())
            if show_all_a and show_all_a.has_attr('href'):
                show_all_href = show_all_a['href']
                url = self.BASE_URL + show_all_href
                response = requests.get(url, headers=headers, timeout=10, verify=False)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                
            # Парсим улицы 
            if start == 1: 
                self.clear_temp_file('streets')  # Очищаем временный файл перед началом
            streets = []
            for idx, a in enumerate(soup.find_all("a", class_="ulica_link", href=True), start=start):
                href = a["href"]
                if href.startswith("/ulicy/") and href != "/ulicy/":
                    street_url = self.BASE_URL + href
                    street_data = self.__parse_street_info(street_url)
                    if street_data:
                        self.save_to_temp('streets', [street_data], append=True)
                        streets.append(street_data)
                        print(f"[{idx}] Извлечена улица: {street_data.get('name')}")
            return streets

        except Exception as e:
            print(f"Ошибка при получении списка улиц: {e}")
            return []
        
    def __parse_street_info(self, street_url):
        """
        Private-function. Парсит информацию об улице со страницы
        
        Args:
            street_url (str): url страницы уилцы.

        Returns:
            dict: Словарь содеражщий информацию об улице тип, название, город, район, протяженность, коль-во строений, коль-во перекресток и список домов.
        """
        try:
            # Здесь логика парсинга отдельной страницы улицы
            response = requests.get(street_url, headers=headers, timeout=10, verify=False)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            
            info_block = soup.find("table", class_="ulica_info")
            
            type_street_th = info_block.find("th", string="Тип")
            name_th = info_block.find("th", string="Название")
            city_th = info_block.find("th", string="Город")
            district_th = info_block.find("th", string="Округ")
            length_th = info_block.find("th", string="Протяженность")
            buildings_th = info_block.find("th", string="Кол-во строений")
            crossroads_th = info_block.find("th", string="Перекрестки")

            type_street = None
            name = None
            city = None
            district = None
            length = None
            buildings = None
            crossroads = None

            if type_street_th and type_street_th.find_next_sibling("td"):
                type_street = type_street_th.find_next_sibling("td").text
            if name_th and name_th.find_next_sibling("td"):
                name = name_th.find_next_sibling("td").text
            if city_th and city_th.find_next_sibling("td"):
                city = city_th.find_next_sibling("td").text
            if district_th and district_th.find_next_sibling("td"):
                district = district_th.find_next_sibling("td").text
            if length_th and length_th.find_next_sibling("td"):
                length = length_th.find_next_sibling("td").text
            if buildings_th and buildings_th.find_next_sibling("td"):
                buildings = buildings_th.find_next_sibling("td").text
            if crossroads_th and crossroads_th.find_next_sibling("td"):
                crossroads = crossroads_th.find_next_sibling("td").text

            # Собираем все номера домов с class="dom_link"
            buildings_list = []
            for dom_a in soup.find_all("a", class_="dom_link"):
                dom_text = dom_a.text.strip()
                if dom_text:
                    buildings_list.append(dom_text)

            return {
                "type_street": type_street,
                "name": name,
                "city": city,
                "district": district,
                "length": length,
                "buildings": buildings,
                "crossroads": crossroads,
                "buildings_list": buildings_list
            }
        
        except Exception as e:
            print(f"Ошибка при парсинге страницы улицы {street_url}: {e}")
            return        

    def save_to_temp(self, name_file, data, append=False):
        """
        Сохраняет список данных в указанный JSON-файл во временной папке.
        Если append=True, данные будут добавлены к существующему списку.
        """
        name_file = f'{name_file}.json'
        temp_dir = os.path.join(os.path.dirname(__file__), '../../../data/temp/ginfo')
        os.makedirs(temp_dir, exist_ok=True)
        temp_file = os.path.join(temp_dir, name_file)
        try:
            if append and os.path.exists(temp_file):
                with open(temp_file, 'r', encoding='utf-8') as f:
                    try:
                        existing_data = json.load(f)
                    except Exception:
                        existing_data = []
                # Дозапись только для списков
                if isinstance(existing_data, list):
                    data = existing_data + data
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f'Ошибка при сохранении данных: {e}')
            
    def clear_temp_file(self, name_file):
        """
        Очищает указанный JSON-файл во временной папке (делает его пустым списком).
        """
        name_file = f'{name_file}.json'
        temp_dir = os.path.join(os.path.dirname(__file__), '../../../data/temp/ginfo')
        temp_file = os.path.join(temp_dir, name_file)
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False, indent=4)
            print(f'Файл {temp_file} очищен.')
        except Exception as e:
            print(f'Ошибка при очистке файла: {e}')

if __name__ == "__main__":
    ginfo_parser = GinfoParser()
    
    districts = ginfo_parser.get_districts()
    
    for district in districts:
        ginfo_parser.get_streets(district['url'])
    print(districts[1]['url'])
    
    