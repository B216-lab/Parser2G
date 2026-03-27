import csv
import json
import queue
import re
import signal
import sqlite3
import threading
import time
from pathlib import Path
from tqdm import tqdm

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

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
                    building_type TEXT,
                    processed INTEGER DEFAULT 0
                )
            ''')
            conn.commit()

    def _parse_initial_state(self, html: str) -> dict:
        """Извлекает initialState и ищет подъезды по паттерну"""
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
                    return results
                except:
                    continue
        
        scripts = re.findall(r'<script[^>]*>([\s\S]*?)</script>', html)
        for script in scripts:
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
            
            entrance_pattern = r'\{\s*"entity_name"\s*:\s*"[^"]*подъезд[^"]*"\s*,\s*"entity_number"\s*:\s*"(\d+)"'
            found_entrances = re.findall(entrance_pattern, script)
            if found_entrances and not results.get('entrances_count'):
                unique = list(set(found_entrances))
                results['entrances_count'] = str(len(unique))
        
        return results

    def load_links_to_db(self):
        """Загружает ссылки из файла в базу данных"""
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
        """Рекурсивный поиск данных"""
        if depth > 25 or obj is None:
            return
            
        if isinstance(obj, dict):
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
            
            if not results.get('postcode'):
                if isinstance(obj.get('address'), dict) and obj['address'].get('postcode'):
                    results['postcode'] = str(obj['address']['postcode'])
                elif obj.get('postcode'):
                    results['postcode'] = str(obj['postcode'])
            
            if not results.get('floors') and isinstance(obj.get('floors'), dict):
                if obj['floors'].get('ground_count'):
                    results['floors'] = str(obj['floors']['ground_count'])
            
            if not results.get('wall_material') and obj.get('material'):
                results['wall_material'] = str(obj['material'])
            if not results.get('construction_year') and obj.get('year_of_construction'):
                results['construction_year'] = str(obj['year_of_construction'])
            if not results.get('gas_supply') and obj.get('gas_type'):
                results['gas_supply'] = str(obj['gas_type'])
                
            # ИЗВЛЕЧЕНИЕ ТИПА ЗДАНИЯ
            if not results.get('building_type') and obj.get('purpose_name'):
                results['building_type'] = str(obj['purpose_name'])
            
            if isinstance(obj.get('point'), dict):
                lat = obj['point'].get('lat')
                lon = obj['point'].get('lon')
                if lat and lon:
                    results['latitude'] = str(lat)
                    results['longitude'] = str(lon)
                    if not results.get('coordinates'):
                        results['coordinates'] = f"{lon}, {lat}"
            
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
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080}
                )
                
                context.route(
                    "**/*",
                    lambda route, request: route.abort() 
                    if request.resource_type in ("image", "font", "media", "stylesheet") 
                    else route.continue_()
                )
                
                page = context.new_page()

                while not stop_event.is_set():
                    try:
                        row_id, link = task_q.get(block=True, timeout=1.0)
                    except queue.Empty:
                        if stop_event.is_set():
                            break
                        continue

                    results = {}
                    
                    try:
                        def on_response(resp):
                            try:
                                rt = (resp.request.resource_type or "").lower()
                                if rt in ("xhr", "fetch") and "2gis" in resp.url.lower():
                                    data = resp.json()
                                    self._extract_from_json(data, results)
                            except:
                                pass
                        
                        page.on("response", on_response)
                        
                        try:
                            page.goto(link, wait_until="domcontentloaded", timeout=20000)
                            page.wait_for_timeout(2000) 
                            
                            if not results.get('address') or not results.get('floors'):
                                html = page.content()
                                embedded_data = self._parse_initial_state(html)
                                for key, value in embedded_data.items():
                                    if value and not results.get(key):
                                        results[key] = value
                                        
                        except PlaywrightTimeoutError:
                            html = page.content()
                            embedded_data = self._parse_initial_state(html)
                            for key, value in embedded_data.items():
                                if value and not results.get(key):
                                    results[key] = value
                        except Exception as e:
                            if "TargetClosedError" not in str(e):
                                print(f"Worker {worker_idx} navigation error: {e}")
                        finally:
                            try:
                                page.remove_listener("response", on_response)
                            except:
                                pass

                        # Запись в базу
                        with sqlite3.connect(self.db_path, timeout=30) as conn:
                            cur = conn.cursor()
                            cur.execute("""
                                UPDATE buildings SET 
                                    coordinates=?, latitude=?, longitude=?, address=?, postal_code=?, 
                                    floors=?, ceilings=?, wall_material=?, construction_year=?, 
                                    gas_supply=?, entrances_count=?, building_type=?, processed=1
                                WHERE id=?
                            """, (
                                results.get('coordinates', ''), 
                                results.get('latitude', ''),
                                results.get('longitude', ''),
                                results.get('address', ''), 
                                results.get('postcode', ''), 
                                results.get('floors', ''), 
                                results.get('ceilings', ''), 
                                results.get('wall_material', ''), 
                                results.get('construction_year', ''), 
                                results.get('gas_supply', ''), 
                                results.get('entrances_count', ''), 
                                results.get('building_type', ''), 
                                row_id
                            ))
                            conn.commit()

                    except Exception as e:
                        if "TargetClosedError" not in str(e):
                            print(f"Worker {worker_idx} error: {e}")
                    finally:
                        # ГАРАНТИРУЕТ, что счетчик обновится и задача закроется, даже если была ошибка
                        pbar.update(1)
                        task_q.task_done()

                context.close()
                browser.close()
                
        except Exception as e:
            # Игнорируем ошибку закрытия браузера при прерывании
            if "TargetClosedError" not in str(e):
                pass

    def run(self):
        self.load_links_to_db()
        
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, link FROM buildings WHERE processed = 0")
            rows = cur.fetchall()

        total = len(rows)
        if total == 0:
            print("Все ссылки уже обработаны! Перехожу к экспорту.")
            self.export_to_csv()
            return

        task_q = queue.Queue()
        for r in rows:
            task_q.put(r)

        stop_event = threading.Event()
        
        def signal_handler(sig, frame):
            print("\n⛔ Получен сигнал остановки. Завершаем потоки и сохраняем данные...")
            stop_event.set()
            
        signal.signal(signal.SIGINT, signal_handler)

        pbar = tqdm(total=total, desc="Обработка ссылок", unit="шт")
        
        threads = []
        for i in range(self.workers):
            t = threading.Thread(target=self.worker_fn, args=(i, task_q, stop_event, pbar))
            t.daemon = True
            threads.append(t)
            t.start()

        # Мягкое ожидание завершения работы потоков
        try:
            while not task_q.empty() and not stop_event.is_set():
                time.sleep(0.5)
            # Ждем завершения последних активных задач, если скрипт не прерван
            if not stop_event.is_set():
                task_q.join()
        except KeyboardInterrupt:
            stop_event.set()
            print("\n⛔ Остановка потоков...")

        # Даем потокам пару секунд на корректное закрытие браузеров
        for t in threads:
            t.join(timeout=2.0)

        pbar.close()
        self.export_to_csv()

    def export_to_csv(self):
        print(f"\nЭкспорт данных в {self.results_csv_path}...")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT link, coordinates, latitude, longitude, address, postal_code, 
                       floors, ceilings, wall_material, construction_year, 
                       gas_supply, entrances_count, building_type 
                FROM buildings
            """)
            rows = cursor.fetchall()

        headers = [
            "Ссылка", "Координаты", "Широта", "Долгота", "Адрес", "Индекс", 
            "Этажность", "Перекрытия", "Материал стен", "Год постройки", 
            "Газоснабжение", "Кол-во подъездов", "Тип здания"
        ]

        with open(self.results_csv_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
            writer = csv.writer(csvfile, delimiter=';')
            writer.writerow(headers)
            writer.writerows(rows)
            
        print("✅ Экспорт завершен!")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) != 2:
        print("Использование: python script.py <файл_со_ссылками.txt>")
        sys.exit(1)
        
    links_file_path = sys.argv[1]
    if not Path(links_file_path).exists():
        print(f"❌ Файл {links_file_path} не найден!")
        sys.exit(1)
        
    parser = Parser2GIS(links_file_path, workers=20)
    parser.run()