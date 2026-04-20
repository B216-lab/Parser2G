import csv
import json
import queue
import re
import signal
import sqlite3
import threading
import time
from pathlib import Path
from dataclasses import dataclass
from tqdm import tqdm

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

@dataclass
class BuildingData:
    """Структура для хранения данных здания"""
    link: str
    coordinates: str = ""
    address: str = ""
    postal_code: str = ""
    floors: str = ""
    ceilings: str = ""
    wall_material: str = ""
    construction_year: str = ""
    gas_supply: str = ""
    entrances_count: str = ""
    apartments_count: str = ""  # НОВОЕ: Количество квартир
    building_type: str = ""


class Parser2GIS:
    def __init__(self, links_file: str, workers: int = 5):
        self.links_file = Path(links_file)
        self.output_dir = Path("output")
        self.output_dir.mkdir(exist_ok=True)
        self.db_path = self.output_dir / "buildings.db"
        self.results_csv_path = self.output_dir / f"{self.links_file.stem}_result.csv"
        self.workers = workers
        
        self._init_db()

    def _init_db(self):
        """Создает таблицы. ДОБАВЛЕНА КОЛОНКА apartments_count"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            # Используем ALTER TABLE для обновления старой БД без её удаления, если она есть
            try:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS buildings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        link TEXT UNIQUE,
                        coordinates TEXT,
                        latitude TEXT,
                        longitude TEXT,
                        address TEXT,
                        postal_code TEXT,
                        floors TEXT,
                        ceilings TEXT,
                        wall_material TEXT, 
                        construction_year TEXT,
                        gas_supply TEXT,
                        entrances_count TEXT,
                        apartments_count TEXT,
                        building_type TEXT,
                        processed INTEGER DEFAULT 0
                    )
                ''')
            except sqlite3.OperationalError:
                pass
                
            # Если база старая и там нет колонки apartments_count, добавляем её на лету
            try:
                conn.execute("ALTER TABLE buildings ADD COLUMN apartments_count TEXT")
            except sqlite3.OperationalError:
                pass # Колонка уже существует
                
            conn.commit()

    def _extract_apartments(self, text: str) -> str:
        """Ищет паттерны 'квартиры X–Y' и суммирует их количество"""
        total_apartments = 0
        # Ищем совпадения с учетом разных тире (минус, короткое тире, длинное тире)
        matches = re.findall(r'квартиры\s*(\d+)\s*[-–—]\s*(\d+)', text.lower())
        
        if not matches:
            return ""
            
        # Убираем дубликаты, чтобы не посчитать одни и те же квартиры дважды
        unique_matches = set(matches)
        for start_str, end_str in unique_matches:
            try:
                start, end = int(start_str), int(end_str)
                if end >= start:
                    total_apartments += (end - start + 1)
            except ValueError:
                pass
                
        return str(total_apartments) if total_apartments > 0 else ""

    def _parse_initial_state(self, html: str) -> dict:
        """Извлекает initialState и ищет подъезды/квартиры по паттерну в HTML"""
        results = {}
        
        patterns = [
            r'var\s+initialState\s*=\s*JSON\.parse\(\'(.*?)\'\)',
            r'var\s+initialState\s*=\s*JSON\.parse\("([^"]*)"\)',
            r'initialState\s*=\s*({.*?});\s*var',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)
            for match in matches:
                try:
                    decoded = match.replace('\\"', '"').replace('\\n', '').replace('\\t', '')
                    data = json.loads(decoded)
                    self._extract_from_json(data, results)
                except:
                    continue
        
        scripts = re.findall(r'<script[^>]*>([\s\S]*?)</script>', html)
        for script in scripts:
            entrance_pattern = r'\{\s*"entity_name"\s*:\s*"[^"]*подъезд[^"]*"\s*,\s*"entity_number"\s*:\s*"(\d+)"'
            found_entrances = re.findall(entrance_pattern, script)
            if found_entrances and not results.get('entrances_count'):
                unique = list(set(found_entrances))
                results['entrances_count'] = str(len(unique))

        # Ищем квартиры в сыром HTML
        apts = self._extract_apartments(html)
        if apts:
            results['apartments_count'] = apts
            
        return results

    def load_links_to_db(self):
        """Загружает ссылки из файла в БД"""
        links = []
        with open(self.links_file, 'r', encoding='utf-8') as f:
            for line in f:
                link = line.strip()
                if link.startswith('http'):
                    links.append(link)
        
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            for link in links:
                cur.execute("INSERT OR IGNORE INTO buildings (link) VALUES (?)", (link,))
            conn.commit()
            
        print(f"Загружено ссылок в базу: {len(links)}")

    def _extract_from_json(self, obj, results: dict, depth=0, path=""):
        """Рекурсивный поиск с приоритетом полей из профиля здания"""
        if depth > 25 or obj is None:
            return
            
        if isinstance(obj, dict):
            # Адрес
            if not results.get('address'):
                if obj.get('address_name') and 'Иркутск' in str(obj.get('full_name', '')):
                    results['address'] = str(obj['address_name'])
                elif obj.get('full_name') and 'квартал' in str(obj['full_name']).lower():
                    results['address'] = str(obj['full_name'])
            
            if not results.get('address') and isinstance(obj.get('address'), dict):
                addr = obj['address']
                comps = addr.get('components', [])
                if comps:
                    parts = [c.get('street') for c in comps if c.get('street')]
                    nums = [c.get('number') for c in comps if c.get('number')]
                    if parts and nums:
                        results['address'] = f"{parts[0]}, {nums[0]}"
            
            # Индекс
            if not results.get('postcode'):
                if isinstance(obj.get('address'), dict) and obj['address'].get('postcode'):
                    results['postcode'] = str(obj['address']['postcode'])
                elif obj.get('postcode'):
                    results['postcode'] = str(obj['postcode'])
            
            # Этажность
            if not results.get('floors') and isinstance(obj.get('floors'), dict):
                if obj['floors'].get('ground_count'):
                    results['floors'] = str(obj['floors']['ground_count'])
            
            # Материалы и газ
            if not results.get('wall_material') and obj.get('material'):
                results['wall_material'] = str(obj['material'])
            if not results.get('construction_year') and obj.get('year_of_construction'):
                results['construction_year'] = str(obj['year_of_construction'])
            if not results.get('gas_supply') and obj.get('gas_type'):
                results['gas_supply'] = str(obj['gas_type'])
            
            # ИСПРАВЛЕНИЕ ТИПА ЗДАНИЯ: Строго проверяем, что это здание, а не пристройка
            if obj.get('type') == 'building' and obj.get('purpose_name') and not results.get('building_type'):
                results['building_type'] = str(obj['purpose_name'])
            
            # Координаты
            if isinstance(obj.get('point'), dict):
                lat = obj['point'].get('lat')
                lon = obj['point'].get('lon')
                if lat and lon:
                    results['latitude'] = str(lat)
                    results['longitude'] = str(lon)
                    if not results.get('coordinates'):
                        results['coordinates'] = f"{lon}, {lat}"
            
            # Подъезды
            if isinstance(obj.get('database_entrances'), list) and not results.get('entrances_count'):
                entrances = set()
                for ent in obj['database_entrances']:
                    name = ent.get('entity_name', '')
                    if 'подъезд' in name.lower() and ent.get('is_visible_in_ui', True):
                        num = ent.get('entity_number')
                        if num:
                            entrances.add(num)
                if entrances:
                    results['entrances_count'] = str(len(entrances))
            
            # Рекурсия
            for key, value in obj.items():
                new_path = f"{path}.{key}" if path else key
                if 'profile' in new_path and 'data' in new_path:
                    self._extract_from_json(value, results, depth + 1, new_path)
                elif not any(x in new_path for x in ['settlements', 'region', 'session']):
                    self._extract_from_json(value, results, depth + 1, new_path)
                    
        elif isinstance(obj, list):
            for item in obj:
                self._extract_from_json(item, results, depth + 1, path)

    def worker_fn(self, worker_idx: int, task_q: queue.Queue, stop_event: threading.Event, pbar):
        p = sync_playwright().start()
        browser = None
        context = None
        page = None
        requests_count = 0

        def init_browser():
            nonlocal browser, context, page
            if browser:
                try: browser.close()
                except: pass
            
            browser = p.chromium.launch(
                headless=False, 
                args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--no-sandbox"]
            )
            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            context.route(
                "**/*", 
                lambda route, request: route.abort() 
                if request.resource_type in ("image", "font", "media", "stylesheet") 
                else route.continue_()
            )
            page = context.new_page()

        init_browser()

        while not stop_event.is_set():
            try:
                row_id, link = task_q.get(timeout=1.0)
            except queue.Empty:
                if task_q.empty() or stop_event.is_set():
                    break
                continue

            if requests_count > 150:
                init_browser()
                requests_count = 0

            results = {}

            def on_response(resp):
                try:
                    rt = (resp.request.resource_type or "").lower()
                    if rt in ("xhr", "fetch") and "2gis" in resp.url.lower():
                        data = resp.json()
                        self._extract_from_json(data, results)
                        
                        # Если данные содержат текст с квартирами, вытаскиваем их прямо из JSON-строки
                        json_str = json.dumps(data, ensure_ascii=False)
                        if "квартиры" in json_str.lower():
                            apts = self._extract_apartments(json_str)
                            if apts and not results.get('apartments_count'):
                                results['apartments_count'] = apts
                except:
                    pass

            try:
                page.on("response", on_response)
                
                try:
                    page.goto(link, wait_until="domcontentloaded", timeout=15000)
                    
                    # Динамическое ожидание данных
                    for _ in range(25):
                        if results.get('address') and results.get('floors') and results.get('building_type'):
                            break
                        page.wait_for_timeout(100)
                        
                    # Финальная выгрузка из HTML на случай, если API не отдало всё
                    html = ""
                    try:
                        html = page.content()
                    except Exception as e:
                        if "navigating" in str(e).lower() or "target closed" in str(e).lower():
                            pass
                            
                    if html:
                        embedded_data = self._parse_initial_state(html)
                        for k, v in embedded_data.items():
                            if v and not results.get(k):
                                results[k] = v
                                
                except PlaywrightTimeoutError:
                    pass
                
                except Exception as e:
                    err_str = str(e).lower()
                    if "target closed" in err_str or "browser" in err_str or "disconnected" in err_str:
                        init_browser()
                        requests_count = 0
                        
            finally:
                try: page.remove_listener("response", on_response)
                except: pass

            try:
                with sqlite3.connect(self.db_path, timeout=30) as conn:
                    conn.execute("""
                        UPDATE buildings SET 
                            coordinates=?, latitude=?, longitude=?, address=?, postal_code=?, 
                            floors=?, ceilings=?, wall_material=?, construction_year=?, 
                            gas_supply=?, entrances_count=?, apartments_count=?, building_type=?, processed=1
                        WHERE id=?
                    """, (
                        results.get('coordinates', ''), results.get('latitude', ''), results.get('longitude', ''),
                        results.get('address', ''), results.get('postcode', ''), results.get('floors', ''), 
                        results.get('ceilings', ''), results.get('wall_material', ''), results.get('construction_year', ''), 
                        results.get('gas_supply', ''), results.get('entrances_count', ''), results.get('apartments_count', ''),
                        results.get('building_type', ''), row_id
                    ))
                    conn.commit()
            except Exception:
                pass

            pbar.update(1)
            requests_count += 1
            task_q.task_done()

        if browser:
            try: browser.close()
            except: pass
        p.stop()

    def run(self):
        self.load_links_to_db()
        
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            # Сбрасываем прогресс для пустых записей
            cur.execute("""
                UPDATE buildings 
                SET processed = 0 
                WHERE processed = 1 
                  AND (address IS NULL OR address = '') 
                  AND (coordinates IS NULL OR coordinates = '')
            """)
            conn.commit()

            cur.execute("SELECT id, link FROM buildings WHERE processed = 0")
            rows = cur.fetchall()

        total = len(rows)
        if total == 0:
            print("Все ссылки обработаны! Перехожу к экспорту.")
            self.export_to_csv()
            return

        task_q = queue.Queue()
        for r in rows:
            task_q.put(r)

        stop_event = threading.Event()
        
        def signal_handler(sig, frame):
            print("\n⛔ Остановка. Сохраняю прогресс...")
            stop_event.set()
            
        signal.signal(signal.SIGINT, signal_handler)

        pbar = tqdm(total=total, desc="Обработка ссылок", unit="шт")
        threads = []
        
        for i in range(self.workers):
            t = threading.Thread(target=self.worker_fn, args=(i, task_q, stop_event, pbar))
            t.daemon = True
            threads.append(t)
            t.start()

        try:
            while not task_q.empty() and not stop_event.is_set():
                time.sleep(0.5)
            if not stop_event.is_set():
                task_q.join()
        except KeyboardInterrupt:
            stop_event.set()
            print("\n⛔ Остановка...")

        for t in threads:
            t.join(timeout=3.0)

        pbar.close()
        self.export_to_csv()

    def export_to_csv(self):
        print(f"\nЭкспорт данных в {self.results_csv_path}...")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT link, coordinates, latitude, longitude, address, postal_code, 
                       floors, ceilings, wall_material, construction_year, 
                       gas_supply, entrances_count, apartments_count, building_type 
                FROM buildings
            """)
            rows = cursor.fetchall()

        headers = [
            "Ссылка", "Координаты", "Широта", "Долгота", "Адрес", "Индекс", 
            "Этажность", "Перекрытия", "Материал стен", "Год постройки", 
            "Газоснабжение", "Кол-во подъездов", "Кол-во квартир", "Тип здания"
        ]

        with open(self.results_csv_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
            writer = csv.writer(csvfile, delimiter=';')
            writer.writerow(headers)
            writer.writerows(rows)
            
        print("✅ Экспорт завершен!")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) != 2:
        print("Использование: python main_parser.py <файл_со_ссылками.txt>")
        sys.exit(1)
        
    links_file_path = sys.argv[1]
    if not Path(links_file_path).exists():
        print(f"❌ Файл {links_file_path} не найден!")
        sys.exit(1)
        
    # Запускаем в 10 потоков
    parser = Parser2GIS(links_file_path, workers=5)
    parser.run()