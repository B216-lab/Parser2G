# parsers/ginfo/ginfo_parser.py
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import json
import os
import warnings

# В исходном коде использовалось verify=False; оставляем возможность, но предупреждаем
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; GinfoBot/1.0)"}

class GinfoParser:
    def __init__(self, log, base_url="https://irkutsk.ginfo.ru", timeout=10, verify=False):
        """
        log: callable(str) - куда писать логи
        base_url: базовый URL (например https://irkutsk.ginfo.ru)
        timeout: тайм-аут для запросов
        verify: verify для requests (по умолчанию False, как в исходнике)
        """
        self.log = log or (lambda s: None)
        self.BASE_URL = base_url.rstrip("/") if base_url else "https://irkutsk.ginfo.ru"
        self.timeout = timeout
        self.verify = verify

        # Настройка Session с Retry и пулом соединений
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

        retry_strategy = Retry(
            total=3,
            backoff_factor=0.4,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    # --- Извлечение районов ---
    def get_districts(self):
        """
        Returns:
            List[Dict('name', 'url')]: список словарей с названием и url района
        """
        try:
            self.log("Поиск районов")
            url = f"{self.BASE_URL}/rayoni"
            self.log(f"GET {url}")
            response = self.session.get(url, timeout=self.timeout, verify=self.verify)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            districts = []
            for idx, a in enumerate(soup.find_all("a", class_="rayon_link"), start=1):
                name = a.text.strip()
                href = a.get("href", "")
                # если href абсолютный — используем как есть, иначе присоединяем базовый
                if href.startswith("http"):
                    url_full = href
                else:
                    url_full = self.BASE_URL + href
                districts.append({"name": name, "url": url_full})
                self.log(f"[{idx}] Найден район: {name} -> {url_full}")
            # сохраняем (совместимость с прежним API)
            self.save_to_temp("districts", districts)
            self.log(f"Найдено районов: {len(districts)}")
            return districts
        except Exception as e:
            self.log(f"Ошибка при получении районов: {e}")
            return []  # возвращаем список, чтобы не ломать код

    # --- Извлечение url улиц ---
    def get_streets(self, district_url):
        """
        Args:
            district_url (str): URL страницы района
        Returns:
            list(str): Список url улиц
        """
        try:
            self.log(f"GET {district_url}")
            response = self.session.get(district_url, timeout=self.timeout, verify=self.verify)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            # Находим ссылку на "все улицы"
            show_all_a = soup.find(
                "a",
                class_="show_all",
                string=lambda s: s and "все улицы" in s.lower()
            )
            if show_all_a and show_all_a.get("href"):
                href = show_all_a["href"]
                if href.startswith("http"):
                    url = href
                else:
                    url = self.BASE_URL + href
                self.log(f"Переход на все улицы: {url}")
                response = self.session.get(url, timeout=self.timeout, verify=self.verify)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")

            streets_links = []
            for idx, a in enumerate(soup.find_all("a", class_="ulica_link"), start=1):
                href = a.get("href", "")
                if href.startswith("http"):
                    url = href
                else:
                    url = self.BASE_URL + href
                streets_links.append(url)
                self.log(f"[{idx}] Найдена улица: {url}")
            return streets_links
        except Exception as e:
            self.log(f"Ошибка при получении url улиц: {e}")
            return []

    def get_street_info(self, street_url):
        """
        Args:
            street_url (str): url страницы улицы.

        Returns:
            Dict: Словарь с информацией по улице, включая 'numbers_houses' — список домов.
        """
        try:
            self.log(f"GET {street_url}")
            response = self.session.get(street_url, timeout=self.timeout, verify=self.verify)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            info_block = soup.find("table", class_="ulica_info")
            # безопасно достаём значения
            def get_td_text(th_text):
                th = info_block.find("th", string=th_text) if info_block else None
                if th and th.find_next_sibling("td"):
                    return th.find_next_sibling("td").text.strip()
                return None

            type_street = get_td_text("Тип")
            name = get_td_text("Название")
            city = get_td_text("Город")
            district = get_td_text("Округ")
            length = get_td_text("Протяженность")
            count_buildings = get_td_text("Кол-во строений")
            crossroads = get_td_text("Перекрестки")

            numbers_houses = []
            for dom_a in soup.find_all("a", class_="dom_link"):
                dom_text = dom_a.text.strip()
                if dom_text:
                    numbers_houses.append(dom_text)

            info = {
                "type_street": type_street,
                "name": name,
                "city": city,
                "district": district,
                "length": length,
                "count_buildings": count_buildings,
                "crossroads": crossroads,
                "numbers_houses": numbers_houses,
            }
            return info
        except Exception as e:
            self.log(f"Ошибка при парсинге страницы улицы {street_url}: {e}")
            return {}

    def save_to_temp(self, name_file, data, append=False):
        """
        Сохраняет список данных в JSON-файл во временной папке.
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
                    except Exception:
                        existing_data = []
                if isinstance(existing_data, list):
                    data = existing_data + data
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            self.log(f"Ошибка при сохранении данных: {e}")

    def clear_temp_file(self, name_file):
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
    # небольшая опроба
    def _log(x): print(x)
    gp = GinfoParser(log=_log)
    d = gp.get_districts()
    print("districts:", len(d))
