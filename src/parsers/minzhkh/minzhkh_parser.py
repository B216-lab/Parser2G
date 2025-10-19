import os
import sys
import time
import logging
import re
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

# Настройка логирования
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "minzhkh_parser.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

class MinzhkhParser:
    def __init__(self, headless=False):
        self.headless = headless
        self.driver = None
        self.address = None
        self.info = None
        self.stats = {
            "total": 0,
            "found_buildings": 0,
            "not_found": 0,
            "errors": 0,
        }
        self.init_browser()

    def configure_browser_options(self):
        options = webdriver.ChromeOptions()
        
        # Полностью отключаем GPU и все логи
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-webgl")
        options.add_argument("--disable-features=VizDisplayCompositor")
        
        # Отключаем все возможные логи
        options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
        options.add_argument("--log-level=0")
        options.add_argument("--silent")
        options.add_argument("--disable-logging")
        options.add_argument("--disable-dev-tools")
        options.add_argument("--no-crash-upload")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-plugins")
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--disable-features=TranslateUI,BlinkGenPropertyTrees")
        
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--remote-debugging-port=0")
        
        return options

    def init_browser(self):
        logging.info("Инициализация браузера")
        
        # Полностью отключаем логи webdriver-manager
        os.environ['WDM_LOG'] = '0'
        os.environ['WDM_LOG_LEVEL'] = '0'
        os.environ['WDM_PRINT_FIRST_LINE'] = 'False'
        os.environ['WDM_LOCAL'] = '1'
        
        # Отключаем логи ChromeDriver
        os.environ['CHROME_LOG_FILE'] = 'NUL'
        
        options = self.configure_browser_options()
        
        try:
            # Настраиваем сервис с полным отключением логов
            service = Service(
                ChromeDriverManager().install(),
                service_args=['--silent', '--disable-build-check', '--disable-logging']
            )
            
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.maximize_window()
            self.open_site()
            
        except Exception as e:
            logging.error(f"Ошибка при инициализации браузера: {e}")
            raise

    def get_build(self):
        """Возвращает полученную информацию о здании"""
        return self.info

    def open_site(self):
        """Открытие сайта"""
        url = "https://dom.mingkh.ru/irkutskaya-oblast/irkutsk/"
        logging.info(f"Открытие сайта: {url}")
        self.driver.get(url)
        self.wait_for_page_load(timeout=30)

    def wait_for_page_load(self, timeout=10):
        """Ожидание загрузки страницы"""
        try:
            WebDriverWait(self.driver, timeout).until(
                lambda driver: driver.execute_script("return document.readyState") == "complete"
            )
        except TimeoutException:
            logging.warning("Страница загружалась дольше ожидаемого времени")

    def normalize_address(self, address):
        """Нормализация адреса для поиска"""
        # Удаляем лишние пробелы и приводим к нижнему регистру
        address = re.sub(r'\s+', ' ', address.strip()).lower()
        
        # Извлекаем компоненты адреса
        parts = [part.strip() for part in address.split(',')]
        
        if len(parts) >= 3:
            city = parts[0]
            street = parts[1]
            house = parts[2]
        elif len(parts) == 2:
            city = "иркутск"
            street = parts[0]
            house = parts[1]
        else:
            city = "иркутск"
            street = address
            house = ""
            
        # Нормализуем названия улиц
        street = self.normalize_street_name(street)
        
        # Очищаем номер дома от лишних символов
        house = re.sub(r'[^\d/]', '', house)
        
        return {
            'city': city,
            'street': street,
            'house': house,
            'full': address
        }

    def normalize_street_name(self, street):
        """Нормализация названия улицы"""
        replacements = {
            r'^ул ': 'улица ',
            r'^пр ': 'проспект ',
            r'^пр-т ': 'проспект ',
            r'^пер ': 'переулок ',
            r'^ш ': 'шоссе ',
            r'^наб ': 'набережная ',
            r'^б-р ': 'бульвар ',
            r'^пл ': 'площадь ',
            r'^мкр ': 'микрорайон ',
            r'^пр\.': 'проспект',
            r'^ул\.': 'улица',
        }
        
        street = street.lower().strip()
        
        # Заменяем сокращения
        for short, full in replacements.items():
            street = re.sub(short, full, street)
            
        # Удаляем точку в конце
        street = re.sub(r'\.$', '', street)
        
        return street.strip()

    def input_address(self):
        """Ввод адреса в поле поиска"""
        logging.info(f"Ввод адреса: {self.address}")
        try:
            input_box = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "address"))
            )
            input_box.clear()
            input_box.send_keys(self.address)
            input_box.send_keys(Keys.ENTER)
            time.sleep(3)  # Ждем обновления таблицы
            return True
        except Exception as e:
            logging.error(f"Ошибка при вводе адреса: {e}")
            return False

    def search_address(self):
        """Поиск адреса в таблице"""
        logging.info("Поиск адреса в таблице")
        
        try:
            # Нормализуем целевой адрес
            target_address = self.normalize_address(self.address)
            logging.info(f"Нормализованный адрес для поиска: {target_address}")
            
            # Ждем появления таблицы
            rows = WebDriverWait(self.driver, 10).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "table tbody tr"))
            )
            
            logging.info(f"Найдено строк в таблице: {len(rows)}")
            
            # Проходим по каждой строке
            for row in rows:
                try:
                    cells = row.find_elements(By.TAG_NAME, 'td')
                    if len(cells) >= 3:
                        city_cell = cells[1].text.strip().lower()
                        address_cell = cells[2].text.strip().lower()
                        
                        # Проверяем соответствие города
                        city_match = target_address['city'] in city_cell
                        
                        if city_match:
                            # Создаем более гибкие условия для поиска улицы
                            street_variants = self.get_street_variants(target_address['street'])
                            street_match = any(variant in address_cell for variant in street_variants)
                            
                            # Проверяем номер дома (если указан)
                            house_match = True
                            if target_address['house']:
                                # Ищем точное совпадение номера дома
                                house_pattern = r'\b' + re.escape(target_address['house']) + r'\b'
                                house_match = bool(re.search(house_pattern, address_cell))
                            
                            if street_match and house_match:
                                logging.info(f"Найден подходящий адрес: {city_cell}, {address_cell}")
                                try:
                                    link = row.find_element(By.TAG_NAME, "a")
                                    self.driver.execute_script("arguments[0].scrollIntoView(true);", link)
                                    time.sleep(1)
                                    link.click()
                                    self.wait_for_page_load()
                                    return True
                                except Exception as e:
                                    logging.error(f"Ошибка при клике на адрес: {e}")
                                    continue
                                    
                except Exception as e:
                    logging.warning(f"Ошибка при обработке строки таблицы: {e}")
                    continue
            
            # Если не нашли в первой странице, пробуем перейти на следующую
            if self.try_next_page():
                return self.search_address()
                    
        except TimeoutException:
            logging.warning("Таблица с адресами не найдена")
        except Exception as e:
            logging.error(f"Ошибка при поиске адреса: {e}")
            
        self.stats["not_found"] += 1
        logging.warning(f"Адрес не найден: {self.address}")
        return False

    def get_street_variants(self, street):
        """Генерирует варианты написания улицы для поиска"""
        variants = [street]
        
        # Убираем тип улицы
        street_without_type = re.sub(r'^(улица|проспект|переулок|шоссе|набережная|бульвар|площадь|микрорайон)\s+', '', street)
        if street_without_type != street:
            variants.append(street_without_type)
        
        # Добавляем сокращения
        if street.startswith('улица '):
            variants.append('ул ' + street[6:])
            variants.append('ул. ' + street[6:])
        elif street.startswith('проспект '):
            variants.append('пр ' + street[9:])
            variants.append('пр. ' + street[9:])
        
        return variants

    def try_next_page(self):
        """Попытка перейти на следующую страницу"""
        try:
            next_button = self.driver.find_element(By.CSS_SELECTOR, "a[rel='next']")
            if next_button.is_enabled():
                logging.info("Переход на следующую страницу")
                next_button.click()
                time.sleep(3)
                self.wait_for_page_load()
                return True
        except NoSuchElementException:
            logging.info("Следующая страница не найдена")
        except Exception as e:
            logging.warning(f"Ошибка при переходе на следующую страницу: {e}")
        return False

    def parse_page(self):
        """Парсинг данных со страницы здания"""
        logging.info("Парсинг данных со страницы здания")
        
        try:
            soup = BeautifulSoup(self.driver.page_source, "html.parser")
            data = {}

            # Парсим основную таблицу
            table_rows = soup.select("table.table.table-striped tr")
            
            for row in table_rows:
                cells = row.find_all("td")
                if len(cells) == 3:
                    key = cells[0].get_text(strip=True)
                    value = cells[2].get_text(strip=True)
                    if key and value:
                        data[key] = value

            # Добавляем URL страницы
            data['url'] = self.driver.current_url
            
            # Добавляем заголовок страницы
            title = soup.find('title')
            if title:
                data['page_title'] = title.get_text(strip=True)
                
            # Парсим адрес из заголовка
            h1 = soup.find('h1')
            if h1:
                data['address'] = h1.get_text(strip=True)
                
            logging.info(f"Извлечено полей: {len(data)}")
            return data
            
        except Exception as e:
            logging.error(f"Ошибка при парсинге страницы: {e}")
            return {}

    def run(self, address):
        """Основной метод для обработки адреса"""
        try:
            self.stats["total"] += 1
            self.address = address
            self.info = None
            
            logging.info(f"Обработка адреса: {address}")
            
            if not self.input_address():
                return
                
            if self.search_address():
                self.info = self.parse_page()
                if self.info:
                    self.stats["found_buildings"] += 1
                    logging.info("Данные успешно извлечены")
                    
                    # Возвращаемся назад
                    try:
                        self.driver.back()
                        self.wait_for_page_load()
                        time.sleep(2)
                    except Exception as e:
                        logging.warning(f"Ошибка при возврате назад: {e}")
                else:
                    self.stats["errors"] += 1
                    
        except Exception as e:
            logging.error(f"Ошибка при обработке адреса {address}: {e}")
            self.stats["errors"] += 1

    def get_stats(self):
        """Возвращает статистику работы"""
        return self.stats.copy()

    def close(self):
        """Закрытие браузера"""
        if self.driver:
            self.driver.quit()
            logging.info("Браузер закрыт")


if __name__ == "__main__":
    # Тестовые адреса
    test_addresses = [
        "Иркутск, Ленина, 15",
        "Иркутск, ул Карла Маркса, 35",
        "Иркутск, Карла Маркса, 35",
        "Иркутск, проспект Маршала Жукова, 40",
        "Маршала Жукова, 40",
        "Иркутск, ул. Байкальская, 160"
    ]
    
    parser = MinzhkhParser(headless=False)
    
    try:
        for address in test_addresses:
            parser.run(address)
            result = parser.get_build()
            if result:
                print(f"✅ Найдены данные для {address}:")
                print(f"   Адрес: {result.get('address', 'Нет данных')}")
                print(f"   Год постройки: {result.get('Год ввода в эксплуатацию', 'Нет данных')}")
                print(f"   Этажей: {result.get('Наибольшее количество этажей', 'Нет данных')}")
                print(f"   Квартир: {result.get('Количество квартир', 'Нет данных')}")
            else:
                print(f"❌ Данные для {address} не найдены")
            print("-" * 60)
            
    finally:
        # Выводим статистику
        stats = parser.get_stats()
        print("\n📊 Статистика работы:")
        print(f"   Всего обработано: {stats['total']}")
        print(f"   Найдено зданий: {stats['found_buildings']}")
        print(f"   Не найдено: {stats['not_found']}")
        print(f"   Ошибок: {stats['errors']}")
        
        parser.close()