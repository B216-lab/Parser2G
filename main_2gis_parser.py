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


class Parser2GIS:
    def __init__(self, links_file: str, workers: int = 3):
        self.links_file = Path(links_file)
        self.output_dir = Path("output")
        self.output_dir.mkdir(exist_ok=True)
        self.db_path = self.output_dir / "buildings.db"
        self.results_csv_path = self.output_dir / f"{self.links_file.stem}_result.csv"
        self.workers = workers
        
        self._init_db()

    def _init_db(self):
        """Создает таблицы и включает режим WAL для многопоточности"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute('''
                CREATE TABLE IF NOT EXISTS buildings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    link TEXT UNIQUE,
                    coordinates TEXT,
                    latitude TEXT,      -- НОВОЕ
                    longitude TEXT,     -- НОВОЕ
                    address TEXT,
                    postal_code TEXT,
                    floors TEXT,
                    ceilings TEXT,
                    wall_material TEXT, 
                    construction_year TEXT,
                    gas_supply TEXT,
                    entrances_count TEXT,
                    processed INTEGER DEFAULT 0
                )
            ''')
            conn.commit()
    def _parse_initial_state(self, html: str) -> dict:
        """Извлекает initialState и ищет подъезды по паттерну"""
        results = {}
        
        # Ищем initialState = JSON.parse('...')
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
                    return results
                except:
                    continue
        
        # Альтернатива: ищем JSON в скриптах + паттерн подъездов
        scripts = re.findall(r'<script[^>]*>([\s\S]*?)</script>', html)
        for script in scripts:
            # 1. Парсим JSON как раньше
            if 'initialState' in script or '"address_name"' in script:
                json_start = script.find('{')
                if json_start != -1:
                    brace_count = 0
                    json_end = -1
                    for i, char in enumerate(script[json_start:], json_start):
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                json_end = i + 1
                                break
                    if json_end != -1:
                        try:
                            json_str = script[json_start:json_end]
                            json_str = json_str.replace('\\/', '/').replace('\\"', '"')
                            data = json.loads(json_str)
                            self._extract_from_json(data, results)
                        except:
                            pass
            
            # 2. ОТДЕЛЬНО: ищем паттерн подъездов в сыром тексте
            # Паттерн: {"entity_name":"N подъезд","entity_number":"N",...
            entrance_pattern = r'\{\s*"entity_name"\s*:\s*"[^"]*подъезд[^"]*"\s*,\s*"entity_number"\s*:\s*"(\d+)"'
            found_entrances = re.findall(entrance_pattern, script)
            if found_entrances and not results.get('entrances_count'):
                # Уникальные номера подъездов
                unique = list(set(found_entrances))
                results['entrances_count'] = str(len(unique))
        
        return results

    def load_links_to_db(self):
        """Загружает ссылки из файла в базу данных (игнорируя дубликаты)"""
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
            
        print(f"Загружено уникальных ссылок в базу: {len(links)}")

    def _extract_from_json(self, obj, results: dict, depth=0, path=""):
        """Рекурсивный поиск с приоритетом полей из профиля здания"""
        if depth > 25 or obj is None:
            return
            
        if isinstance(obj, dict):
            # 🔹 Адрес: приоритет адрес_name из профиля, затем full_name
            if not results.get('address'):
                if obj.get('address_name') and 'Иркутск' in str(obj.get('full_name', '')):
                    results['address'] = str(obj['address_name'])
                elif obj.get('full_name') and 'квартал' in str(obj['full_name']).lower():
                    results['address'] = str(obj['full_name'])
            
            # 🔹 Адрес из components (резервный вариант)
            if not results.get('address') and isinstance(obj.get('address'), dict):
                addr = obj['address']
                comps = addr.get('components', [])
                if comps:
                    parts = [c.get('street') for c in comps if c.get('street')]
                    nums = [c.get('number') for c in comps if c.get('number')]
                    if parts and nums:
                        results['address'] = f"{parts[0]}, {nums[0]}"
            
            # 🔹 Индекс
            if not results.get('postcode'):
                if isinstance(obj.get('address'), dict) and obj['address'].get('postcode'):
                    results['postcode'] = str(obj['address']['postcode'])
                elif obj.get('postcode'):
                    results['postcode'] = str(obj['postcode'])
            
            # 🔹 Этажность
            if not results.get('floors') and isinstance(obj.get('floors'), dict):
                if obj['floors'].get('ground_count'):
                    results['floors'] = str(obj['floors']['ground_count'])
            
            # 🔹 Материал и год
            if not results.get('wall_material') and obj.get('material'):
                results['wall_material'] = str(obj['material'])
            if not results.get('construction_year') and obj.get('year_of_construction'):
                results['construction_year'] = str(obj['year_of_construction'])
            if not results.get('gas_supply') and obj.get('gas_type'):
                results['gas_supply'] = str(obj['gas_type'])
            
            # 🔹 Координаты — РАЗДЕЛЬНО!
            if isinstance(obj.get('point'), dict):
                lat = obj['point'].get('lat')
                lon = obj['point'].get('lon')
                if lat and lon:
                    results['latitude'] = str(lat)
                    results['longitude'] = str(lon)
                    if not results.get('coordinates'):
                        results['coordinates'] = f"{lon}, {lat}"
            
            # 🔹 Подъезды: ищем entity_name с "подъезд"
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
            
            # 🔹 Рекурсия
            for key, value in obj.items():
                new_path = f"{path}.{key}" if path else key
                # Ищем только в data.profile.<id>.data
                if 'profile' in new_path and 'data' in new_path:
                    self._extract_from_json(value, results, depth + 1, new_path)
                elif not any(x in new_path for x in ['settlements', 'region', 'session']):
                    self._extract_from_json(value, results, depth + 1, new_path)
                    
        elif isinstance(obj, list):
            for item in obj:
                self._extract_from_json(item, results, depth + 1, path)

    def worker_fn(self, worker_idx: int, task_q: queue.Queue, stop_event: threading.Event, pbar):
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,  # ✅ Headless стабильнее и быстрее
                args=["--disable-blink-features=AutomationControlled", "--disable-gpu"]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                viewport={"width": 1280, "height": 720}  # Меньше = быстрее
            )
            context.route("**/*", lambda route, request: route.abort() 
                if request.resource_type in ("image", "font", "media", "stylesheet", "websocket") 
                else route.continue_())
            
            page = context.new_page()

            while not stop_event.is_set():
                try:
                    # ✅ Блокирующее получение с таймаутом
                    row_id, link = task_q.get(block=True, timeout=1.0)
                except queue.Empty:
                    # Если очередь пуста 1 сек — проверяем, не пора ли выходить
                    if task_q.empty() and stop_event.is_set():
                        break
                    continue

                results = {}
                
                def on_response(resp):
                    try:
                        rt = (resp.request.resource_type or "").lower()
                        if rt in ("xhr", "fetch") and "2gis" in resp.url:
                            data = resp.json()
                            self._extract_from_json(data, results)
                    except: pass

                page.on("response", on_response)

                try:
                    page.goto(link, wait_until="commit", timeout=10000)  # ✅ Быстрее чем domcontentloaded
                    page.wait_for_timeout(500)  # ✅ Меньше ожидание
                    
                    html = page.content()
                    scripts = re.findall(r'<script[^>]*>([\s\S]*?)</script>', html)
                    for script in scripts:
                        if '"address_name"' in script or '"ground_count"' in script:
                            try:
                                start_idx = script.find('{')
                                end_idx = script.rfind('}') + 1
                                if start_idx != -1 and end_idx != 0:
                                    data = json.loads(script[start_idx:end_idx])
                                    self._extract_from_json(data, results)
                            except: pass
                except PlaywrightTimeoutError:
                    pass
                except Exception as e:
                    print(f"Worker {worker_idx} error: {e}")
                finally:
                    try: page.remove_listener("response", on_response)
                    except: pass

                # Запись в БД
                try:
                    with sqlite3.connect(self.db_path, timeout=30) as conn:
                        cur = conn.cursor()
                        cur.execute("""
                            UPDATE buildings SET 
                                coordinates=?, address=?, postal_code=?, floors=?, ceilings=?, 
                                wall_material=?, construction_year=?, gas_supply=?, entrances_count=?, 
                                processed=1 WHERE id=?
                        """, (
                            results.get('coordinates', ''), results.get('address', ''), 
                            results.get('postcode', ''), results.get('floors', ''), 
                            results.get('ceilings', ''), results.get('wall_material', ''), 
                            results.get('construction_year', ''), results.get('gas_supply', ''), 
                            results.get('entrances_count', ''), row_id
                        ))
                        conn.commit()
                except Exception as db_err:
                    print(f"DB error: {db_err}")

                pbar.update(1)
                task_q.task_done()  # ✅ Обязательно!

            # ✅ Закрываем в правильном порядке с задержками
            try:
                page.close()
                time.sleep(0.2)
                context.close()
                time.sleep(0.2)
                browser.close()
            except Exception as e:
                print(f"Worker {worker_idx} close error: {e}")

    def run(self):
        self.load_links_to_db()
        
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, link FROM buildings WHERE processed = 0")
            rows = cur.fetchall()

        total = len(rows)
        if total == 0:
            print("✅ Все ссылки уже обработаны! Экспортирую...")
            self.export_to_csv()
            return

        task_q = queue.Queue()
        for r in rows:
            task_q.put(r)

        stop_event = threading.Event()
        
        def signal_handler(sig, frame):
            print("\n⛔ Остановка...")
            stop_event.set()
        signal.signal(signal.SIGINT, signal_handler)

        pbar = tqdm(total=total, desc="Обработка", unit="шт")
        
        threads = []
        for i in range(self.workers):
            t = threading.Thread(target=self.worker_fn, args=(i, task_q, stop_event, pbar))
            t.daemon = True
            t.start()
            threads.append(t)

        # ✅ Ждём, пока ВСЕ задачи будут обработаны
        try:
            task_q.join()  # 🔑 Ключевая строка!
        except KeyboardInterrupt:
            stop_event.set()
            task_q.join()

        # ✅ Даём потокам время на закрытие браузеров
        for t in threads:
            t.join(timeout=5.0)

        pbar.close()
        
        if not stop_event.is_set():
            self.export_to_csv()

    def export_to_csv(self):
        """Выгрузка результатов из БД в CSV"""
        print(f"\nЭкспорт данных в {self.results_csv_path}...")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT link, coordinates, 
                    latitude, longitude,  -- новые поля
                    address, postal_code, floors, ceilings, 
                    wall_material, construction_year, gas_supply, entrances_count 
                FROM buildings
            """)
            rows = cursor.fetchall()

        headers = [
            "Ссылка", "Координаты", "Широта", "Долгота", "Адрес", "Индекс", 
            "Этажность", "Перекрытия", "Материал стен", "Год постройки", 
            "Газоснабжение", "Кол-во подъездов"
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
        
    # workers=3 оптимально для стабильной работы без бана
    parser = Parser2GIS(links_file_path, workers=15)
    parser.run()