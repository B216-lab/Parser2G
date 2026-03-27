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

    def _extract_from_json(self, obj, results: dict, depth=0):
        """Рекурсивно ищет нужные ключи в JSON-ответах"""
        if depth > 20: 
            return
            
        if isinstance(obj, dict):
            if obj.get('address_name') and not results.get('address'):
                results['address'] = str(obj['address_name'])
            if obj.get('postcode') and not results.get('postcode'):
                results['postcode'] = str(obj['postcode'])
            if isinstance(obj.get('floors'), dict) and 'ground_count' in obj['floors']:
                results['floors'] = str(obj['floors']['ground_count'])
            if obj.get('floor_type') and not results.get('ceilings'):
                results['ceilings'] = str(obj['floor_type'])
            if obj.get('material') and not results.get('wall_material'):
                results['wall_material'] = str(obj['material'])
            if obj.get('year_of_construction') and not results.get('construction_year'):
                results['construction_year'] = str(obj['year_of_construction'])
            if obj.get('gas_type') and not results.get('gas_supply'):
                results['gas_supply'] = str(obj['gas_type'])
            if isinstance(obj.get('entrances'), list) and not results.get('entrances_count'):
                results['entrances_count'] = str(len(obj['entrances']))
            
            # Координаты
            if isinstance(obj.get('point'), dict):
                lat, lon = obj.get('point', {}).get('lat'), obj.get('point', {}).get('lon')
                if lat and lon and not results.get('coordinates'):
                    results['coordinates'] = f"{lon}, {lat}"
                    
            if isinstance(obj.get('geometry'), dict) and obj['geometry'].get('centroid'):
                match = re.search(r'POINT\(([-\d.]+)\s+([-\d.]+)\)', obj['geometry']['centroid'])
                if match and not results.get('coordinates'):
                    lon, lat = match.groups()
                    results['coordinates'] = f"{lat}, {lon}"

            for key, value in obj.items():
                self._extract_from_json(value, results, depth + 1)
                
        elif isinstance(obj, list):
            for item in obj:
                self._extract_from_json(item, results, depth + 1)

    def worker_fn(self, worker_idx: int, task_q: queue.Queue, stop_event: threading.Event, pbar):
        """Функция потока: открывает браузер и парсит ссылки"""
        with sync_playwright() as p:
            # Запускаем браузер с отключенными метками автоматизации
            browser = p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080}
            )
            
            # ВАЖНО: Блокируем картинки, шрифты и стили. Это убивает "вечную загрузку" 
            # и ускоряет работу парсера в 5-10 раз.
            context.route(
                "**/*",
                lambda route, request: route.abort() 
                if request.resource_type in ("image", "font", "media", "stylesheet") 
                else route.continue_()
            )
            
            page = context.new_page()

            while not stop_event.is_set():
                try:
                    row_id, link = task_q.get(block=False)
                except queue.Empty:
                    break

                results = {}
                
                # Перехватчик: ловим все API запросы 2GIS
                def on_response(resp):
                    try:
                        rt = (resp.request.resource_type or "").lower()
                        if rt in ("xhr", "fetch") and "2gis" in resp.url:
                            data = resp.json()
                            self._extract_from_json(data, results)
                    except Exception:
                        pass

                page.on("response", on_response)

                try:
                    # Ждем только загрузки DOM, игнорируем тяжелые скрипты
                    page.goto(link, wait_until="domcontentloaded", timeout=20000)
                    
                    # Даем 2GIS подтянуть данные по сети (XHR)
                    page.wait_for_timeout(3000) 
                    
                    # Резервный поиск: вытягиваем NUXT/State прямо из HTML
                    html = page.content()
                    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
                    for script in scripts:
                        if '"address_name"' in script or '"ground_count"' in script:
                            try:
                                start_idx = script.find('{')
                                end_idx = script.rfind('}') + 1
                                if start_idx != -1 and end_idx != 0:
                                    data = json.loads(script[start_idx:end_idx])
                                    self._extract_from_json(data, results)
                            except:
                                pass
                                
                except PlaywrightTimeoutError:
                    # Если страница всё-таки повисла, просто идём дальше (данные часто успевают перехватиться)
                    pass
                except Exception as e:
                    pass
                finally:
                    try:
                        page.remove_listener("response", on_response)
                    except:
                        pass

                # Запись в базу данных
                try:
                    with sqlite3.connect(self.db_path, timeout=30) as conn:
                        cur = conn.cursor()
                        cur.execute("""
                            UPDATE buildings SET 
                            coordinates=?, address=?, postal_code=?, floors=?, ceilings=?, 
                            wall_material=?, construction_year=?, gas_supply=?, entrances_count=?, 
                            processed=1
                            WHERE id=?
                        """, (
                            results.get('coordinates', ''), results.get('address', ''), 
                            results.get('postcode', ''), results.get('floors', ''), 
                            results.get('ceilings', ''), results.get('wall_material', ''), 
                            results.get('construction_year', ''), results.get('gas_supply', ''), 
                            results.get('entrances_count', ''), row_id
                        ))
                        conn.commit()
                except Exception as db_err:
                    print(f"Ошибка БД: {db_err}")

                pbar.update(1)
                task_q.task_done()

            context.close()
            browser.close()

    def run(self):
        """Основной метод запуска"""
        self.load_links_to_db()
        
        # Забираем из базы все необработанные ссылки
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
        
        # Корректное завершение по Ctrl+C
        def signal_handler(sig, frame):
            print("\n⛔ Остановка потоков... Пожалуйста, подождите.")
            stop_event.set()
        signal.signal(signal.SIGINT, signal_handler)

        pbar = tqdm(total=total, desc="Обработка ссылок", unit="шт")
        
        threads = []
        # Запуск рабочих потоков
        for i in range(self.workers):
            t = threading.Thread(target=self.worker_fn, args=(i, task_q, stop_event, pbar))
            t.daemon = True
            threads.append(t)
            t.start()

        # Ждем завершения
        try:
            for t in threads:
                while t.is_alive():
                    t.join(timeout=0.5)
                    if stop_event.is_set():
                        break
        except KeyboardInterrupt:
            stop_event.set()

        pbar.close()
        
        if not stop_event.is_set():
            self.export_to_csv()

    def export_to_csv(self):
        """Выгрузка результатов из БД в CSV"""
        print(f"\nЭкспорт данных в {self.results_csv_path}...")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT link, coordinates, address, postal_code, floors, ceilings, 
                       wall_material, construction_year, gas_supply, entrances_count 
                FROM buildings
            """)
            rows = cursor.fetchall()

        headers = [
            "Ссылка", "Координаты", "Адрес", "Индекс", "Этажность", "Перекрытия", 
            "Материал стен", "Год постройки", "Газоснабжение", "Кол-во подъездов"
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
    parser = Parser2GIS(links_file_path, workers=3)
    parser.run()