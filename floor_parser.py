import csv
import json
import re
import sqlite3
import time
import signal
from pathlib import Path
from urllib.parse import urlparse
from tqdm import tqdm

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def extract_city_alias(dgis_url: str) -> str:
        path = urlparse(dgis_url).path.strip("/")
        parts = path.split("/")
        return parts[0] if parts else "irkutsk"
    

class FloorParser:
    def __init__(self, csv_file_path: str):
        # Init paths first (важно для создания таблицы кэша)
        self.csv_file_path = Path(csv_file_path)
        self.output_dir = Path("output")
        self.output_dir.mkdir(exist_ok=True)
        self.temp_db_path = self.output_dir / "temp_buildings.db"
        # memory cache for this run (fast)
        self._memory_cache = {}
        # create persistent cache table (temp_db_path уже задан)
        self._create_building_cache_table()

        # name for result csv: originalname_floors.csv
        self.results_csv_path = self.output_dir / f"{self.csv_file_path.stem}_floors.csv"

        # создаём основную таблицу и другие колонки
        self._create_temp_db()
        self._ensure_russian_column()
    def _create_building_cache_table(self):
        """Создаём таблицу для кэша building_id -> floor_count"""
        conn = sqlite3.connect(self.temp_db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS building_cache (
                building_id TEXT PRIMARY KEY,
                floor_count INTEGER,
                last_updated INTEGER
            )
        """)
        conn.commit()
        conn.close()

    def get_cached_floor(self, building_id: str):
        """Сначала смотрим in-memory, затем sqlite; возвращаем int или None."""
        if not building_id:
            return None
        # in-memory
        if building_id in self._memory_cache:
            return self._memory_cache[building_id]
        # sqlite
        conn = sqlite3.connect(self.temp_db_path)
        cur = conn.cursor()
        cur.execute("SELECT floor_count FROM building_cache WHERE building_id = ?", (building_id,))
        row = cur.fetchone()
        conn.close()
        if row and row[0] is not None:
            self._memory_cache[building_id] = int(row[0])
            return int(row[0])
        return None

    def set_cached_floor(self, building_id: str, floor_count: int):
        """Записать значение в in-memory и в sqlite."""
        if not building_id:
            return
        try:
            self._memory_cache[building_id] = int(floor_count)
        except Exception:
            pass
        try:
            conn = sqlite3.connect(self.temp_db_path)
            cur = conn.cursor()
            # ON CONFLICT ... works with modern SQLite; если у тебя старая версия, замени на REPLACE INTO
            cur.execute("""
                INSERT INTO building_cache(building_id, floor_count, last_updated)
                VALUES (?, ?, ?)
                ON CONFLICT(building_id) DO UPDATE SET floor_count = excluded.floor_count, last_updated = excluded.last_updated
            """, (building_id, int(floor_count), int(time.time())))
            conn.commit()
            conn.close()
        except Exception:
            # fallback для старых SQLite: REPLACE
            try:
                conn = sqlite3.connect(self.temp_db_path)
                cur = conn.cursor()
                cur.execute("REPLACE INTO building_cache(building_id, floor_count, last_updated) VALUES (?, ?, ?)",
                            (building_id, int(floor_count), int(time.time())))
                conn.commit()
                conn.close()
            except Exception:
                pass


    def _create_temp_db(self):
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS buildings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                description TEXT,
                rubrics TEXT,
                address TEXT,
                address_comment TEXT,
                postal_code TEXT,
                microdistrict TEXT,
                district TEXT,
                city TEXT,
                area TEXT,
                region TEXT,
                country TEXT,
                working_hours TEXT,
                timezone TEXT,
                rating TEXT,
                review_count TEXT,
                phone1 TEXT,
                phone2 TEXT,
                phone3 TEXT,
                email1 TEXT,
                email2 TEXT,
                email3 TEXT,
                website1 TEXT,
                website2 TEXT,
                website3 TEXT,
                twitter TEXT,
                vk TEXT,
                whatsapp1 TEXT,
                whatsapp2 TEXT,
                whatsapp3 TEXT,
                viber1 TEXT,
                viber2 TEXT,
                viber3 TEXT,
                telegram1 TEXT,
                telegram2 TEXT,
                telegram3 TEXT,
                youtube TEXT,
                latitude TEXT,
                longitude TEXT,
                dgis_url TEXT,
                building_type TEXT,
                floor_count INTEGER DEFAULT NULL
            )
        ''')
        cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_buildings_dgis_url ON buildings(dgis_url)')
        conn.commit()
        conn.close()

    def _ensure_russian_column(self):
        # добавим колонку "Количество_этажей" если её нет (имя с подчеркиванием для безопасности)
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(buildings)")
        cols = [c[1] for c in cursor.fetchall()]
        if "Количество_этажей" not in cols:
            cursor.execute('ALTER TABLE buildings ADD COLUMN "Количество_этажей" INTEGER')
            conn.commit()
        conn.close()

    def load_csv_to_db(self, clear_table_before_load: bool = False):
        """
        Загрузка данных из CSV во временную базу данных.
        Если clear_table_before_load=True — удалит старые записи и загрузит только содержимое CSV.
        """
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()

        if clear_table_before_load:
            cursor.execute("DELETE FROM buildings")
            conn.commit()

        with open(self.csv_file_path, 'r', encoding='utf-8-sig', newline='') as csvfile:
            sample = csvfile.read(4096)
            csvfile.seek(0)
            try:
                delimiter = csv.Sniffer().sniff(sample).delimiter
            except Exception:
                delimiter = ';'

            reader = csv.DictReader(csvfile, delimiter=delimiter)

            for row in reader:
                # Используем INSERT OR IGNORE: если dgis_url уже есть — запись игнорируется
                cursor.execute('''
                    INSERT OR IGNORE INTO buildings (
                        name, description, rubrics, address, address_comment,
                        postal_code, microdistrict, district, city, area,
                        region, country, working_hours, timezone, rating,
                        review_count, phone1, phone2, phone3, email1, email2,
                        email3, website1, website2, website3, twitter, vk,
                        whatsapp1, whatsapp2, whatsapp3, viber1, viber2,
                        viber3, telegram1, telegram2, telegram3, youtube,
                        latitude, longitude, dgis_url, building_type
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''', (
                    row.get('Наименование', ''),
                    row.get('Описание', ''),
                    row.get('Рубрики', ''),
                    row.get('Адрес', ''),
                    row.get('Комментарий к адресу', ''),
                    row.get('Почтовый индекс', ''),
                    row.get('Микрорайон', ''),
                    row.get('Район', ''),
                    row.get('Город', ''),
                    row.get('Округ', ''),
                    row.get('Регион', ''),
                    row.get('Страна', ''),
                    row.get('Часы работы', ''),
                    row.get('Часовой пояс', ''),
                    row.get('Рейтинг', ''),
                    row.get('Количество отзывов', ''),
                    row.get('Телефон 1', ''),
                    row.get('Телефон 2', ''),
                    row.get('Телефон 3', ''),
                    row.get('E-mail 1', ''),
                    row.get('E-mail 2', ''),
                    row.get('E-mail 3', ''),
                    row.get('Веб-сайт 1', ''),
                    row.get('Веб-сайт 2', ''),
                    row.get('Веб-сайт 3', ''),
                    row.get('Twitter', ''),
                    row.get('ВКонтакте', ''),
                    row.get('WhatsApp 1', ''),
                    row.get('WhatsApp 2', ''),
                    row.get('WhatsApp 3', ''),
                    row.get('Viber 1', ''),
                    row.get('Viber 2', ''),
                    row.get('Viber 3', ''),
                    row.get('Telegram 1', ''),
                    row.get('Telegram 2', ''),
                    row.get('Telegram 3', ''),
                    row.get('YouTube', ''),
                    row.get('Широта', ''),
                    row.get('Долгота', ''),
                    row.get('2GIS URL', ''),
                    row.get('Тип', '')
                ))
        conn.commit()
        conn.close()


    def _extract_id_from_text(self, text: str):
        # ищем history_objects и извлекаем id (если есть)
        m = re.search(r'"history_objects"\s*:\s*\[.*?\]', text, re.DOTALL | re.IGNORECASE)
        if m:
            mm = re.search(r'"id"\s*:\s*"(\d+)"', m.group(0))
            if mm:
                return mm.group(1)
        # fallback: просто любое "id":"<числа>" рядом с objectType":"building"
        m2 = re.search(r'"objectType"\s*:\s*"building".{0,200}?["\']id["\']\s*:\s*["\'](\d+)["\']', text, re.DOTALL | re.IGNORECASE)
        if m2:
            return m2.group(1)
        return None

    def _extract_ground_count_from_text(self, text: str):
        m = re.search(r'"ground_count"\s*:\s*(\d+)', text, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None
    
    def _build_geo_url_from_original(self, original_url: str, building_id: str) -> str:
        parsed = urlparse(original_url)
        scheme = parsed.scheme or "https"
        netloc = parsed.netloc or "2gis.ru"
        path_parts = parsed.path.strip("/").split("/") if parsed.path else []
        if path_parts and path_parts[0].lower() not in ("firm", "search", "catalog", "item", "place"):
            city_alias = path_parts[0]
            if not re.fullmatch(r"\d+", city_alias):
                return f"{scheme}://{netloc}/{city_alias}/geo/{building_id}"
        return f"{scheme}://{netloc}/geo/{building_id}"

    def _choose_geo_base_from_responses(self, collected_responses, original_url):
        """
        Пытаемся выбрать наиболее подходящую базу (scheme://netloc/<city_alias>)
        - ищем в collected_responses url с pattern /<city_alias>/firm/ или /<city_alias>/geo/
        - предпочитаем домен содержащий '2gis.ru' если есть
        """
        from urllib.parse import urlparse
        candidates = []
        for resp in collected_responses:
            try:
                u = resp.url
                parsed = urlparse(u)
                parts = parsed.path.strip("/").split("/")
                # ищем pattern: /<city_alias>/firm/ или /<city_alias>/geo/
                for i in range(len(parts)-1):
                    if parts[i+1] in ("firm", "geo"):
                        alias = parts[i]
                        if alias and re.fullmatch(r"[a-z\-]+", alias, re.IGNORECASE):
                            base = f"{parsed.scheme}://{parsed.netloc}/{alias}"
                            candidates.append((base, parsed.netloc))
                            break
            except Exception:
                continue

        # если есть 2gis.ru вариант — отдаём его
        for base, netloc in candidates:
            if "2gis.ru" in netloc:
                return base
        if candidates:
            return candidates[0][0]

        # fallback: из original_url взять netloc + возможно city_alias
        parsed0 = urlparse(original_url)
        path0 = parsed0.path.strip("/").split("/")
        if path0 and path0[0].lower() not in ("firm","search","catalog","item","place"):
            return f"{parsed0.scheme or 'https'}://{parsed0.netloc}/{path0[0]}"
        return f"{parsed0.scheme or 'https'}://{parsed0.netloc}"


    def process_building_url(self, page, url: str):
        """
        Открываем исходный URL, собираем xhr/fetch/json ответы, ищем history_objects/objectType:building/id.
        Если найден building_id — сначала проверяем кэш, затем формируем geo URL и вызываем get_building_data_by_geo.
        """
        print("\n=== process_building_url:", url)
        building_id = None
        collected = []

        def on_response(resp):
            try:
                rt = (resp.request.resource_type or "").lower()
                ctype = (resp.headers.get("content-type") or "").lower()
                # фильтруем: только xhr/fetch или json/text
                if not (rt in ("xhr", "fetch") or "application/json" in ctype or "text/plain" in ctype):
                    return
                # ещё фильтр по URL чтобы реже парсить
                ru = resp.url.lower()
                if not any(k in ru for k in ("data", "byid", "profile", "catalog", "items", "search", "geo", "history")):
                    return
                collected.append(resp)
            except Exception:
                pass

        page.on("response", on_response)

        try:
            page.goto(url, timeout=30000)
        except Exception as e:
            print("  goto error:", e)

        # Быстрее: domcontentloaded + короткая пауза; XHR мы всё равно слушаем
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(1500)

        # 1) Ищем точные совпадения — prefer JSON parsing и context 'history_objects' / objectType == 'building'
        for resp in collected:
            try:
                text = None
                try:
                    # безопасно получить текст (устойчивее к неверным content-type)
                    text = resp.text()
                except Exception:
                    try:
                        j = resp.json()
                        text = json.dumps(j)
                    except Exception:
                        continue
                if not text:
                    continue

                # быстрый фильтр
                if '"history_objects"' not in text and '"objectType"' not in text and '"building_id"' not in text:
                    continue

                # пробуем распарсить JSON
                data = None
                try:
                    data = json.loads(text)
                except Exception:
                    data = None

                if isinstance(data, dict):
                    # result.history_objects
                    if "result" in data and isinstance(data["result"], dict):
                        ho = data["result"].get("history_objects")
                        if isinstance(ho, list):
                            for obj in ho:
                                if isinstance(obj, dict) and obj.get("objectType") == "building" and obj.get("id"):
                                    cand = str(obj.get("id"))
                                    # plausibility: длинный id, обычно начинается с 7 (но не строго)
                                    if len(cand) >= 8:
                                        building_id = cand
                                        print("  → found building_id (result.history_objects):", building_id, " (resp:", resp.url, ")")
                                        break
                            if building_id:
                                break

                    # root.history_objects
                    if "history_objects" in data and isinstance(data["history_objects"], list):
                        for obj in data["history_objects"]:
                            if isinstance(obj, dict) and obj.get("objectType") == "building" and obj.get("id"):
                                cand = str(obj.get("id"))
                                if len(cand) >= 8:
                                    building_id = cand
                                    print("  → found building_id (root.history_objects):", building_id, " (resp:", resp.url, ")")
                                    break
                        if building_id:
                            break

                    # items[].address.building_id (вариант для firm -> building)
                    if "items" in data and isinstance(data["items"], list):
                        for it in data["items"]:
                            addr = it.get("address") or {}
                            if isinstance(addr, dict) and addr.get("building_id"):
                                cand = str(addr.get("building_id"))
                                if len(cand) >= 8:
                                    building_id = cand
                                    print("  → found building_id (items.address.building_id):", building_id, " (resp:", resp.url, ")")
                                    break
                        if building_id:
                            break

                # Более строгий regex: objectType:"building" рядом с id
                m = re.search(r'"objectType"\s*:\s*"building"[^}]{0,400}?"id"\s*:\s*"(?:\d+)"', text, re.IGNORECASE | re.DOTALL)
                if m:
                    # извлечём ближайший id после objectType
                    m2 = re.search(r'"id"\s*:\s*"(\d+)"', m.group(0))
                    if m2:
                        cand = m2.group(1)
                        if len(cand) >= 8:
                            building_id = cand
                            print("  → found building_id (regex nearby):", building_id, " (resp:", resp.url, ")")
                            break
            except Exception:
                continue

        # снимем listener
        try:
            page.off("response", on_response)
        except Exception:
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass

        # fallback: попробовать найти /geo/ в DOM anchors
        if not building_id:
            try:
                anchors = page.query_selector_all("a[href*='/geo/'], a[href*='geo/']")
                for a in anchors:
                    try:
                        href = a.get_attribute("href") or ""
                        m = re.search(r"/geo/(\d+)", href)
                        if m:
                            cand = m.group(1)
                            if len(cand) >= 8:
                                building_id = cand
                                print("  → found building_id from href:", building_id, " (href:", href, ")")
                                break
                    except Exception:
                        continue
            except Exception:
                pass

        # последний HTML fallback (строгий)
        if not building_id:
            try:
                html = page.content()
                m = re.search(r'"objectType"\s*:\s*"building"[^}]{0,400}?"id"\s*:\s*"(?:\d+)"', html, re.IGNORECASE | re.DOTALL)
                if m:
                    m2 = re.search(r'"id"\s*:\s*"(\d+)"', m.group(0))
                    if m2:
                        cand = m2.group(1)
                        if len(cand) >= 8:
                            building_id = cand
                            print("  → found building_id in HTML (regex):", building_id)
            except Exception:
                pass

        if not building_id:
            print("  ❌ building_id не найден (увеличь таймауты/проверь селектор клика вручную).")
            return None

        # проверяем кэш прежде чем идти на geo
        cached = self.get_cached_floor(building_id)
        if cached is not None:
            print(f"  → Использую кэшированную этажность для {building_id}: {cached}")
            return {"floor_count": cached}

        # выберем базу для geo (по собранным ответам или original URL)
        geo_base = self._choose_geo_base_from_responses(collected, url)
        geo_url = f"{geo_base.rstrip('/')}/geo/{building_id}"
        print("  → переходим на geo:", geo_url)
        return self.get_building_data_by_geo(page, geo_url)




    def get_building_data_by_geo(self, page, geo_url: str):
        """
        Переходим на geo URL, собираем JSON/XHR ответы и ищем items[].floors.ground_count.
        При успехе — записываем в кеш (sqlite + in-memory).
        """
        floor_count = None
        responses = []

        def on_response(resp):
            try:
                rt = (resp.request.resource_type or "").lower()
                ctype = (resp.headers.get("content-type") or "").lower()
                if not (rt in ("xhr", "fetch") or "application/json" in ctype or "text/plain" in ctype):
                    return
                # фильтр по URL — ускоряет
                ru = resp.url.lower()
                if not any(k in ru for k in ("byid", "data", "items", "floors", "geo", "search")):
                    return
                responses.append(resp)
            except Exception:
                pass

        page.on("response", on_response)

        try:
            page.goto(geo_url, timeout=30000)
        except Exception as e:
            print(f"  Ошибка при заходе на {geo_url}: {e}")

        # дать время XHR подгрузить
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(1500)

        for resp in responses:
            try:
                try:
                    text = resp.text()
                except Exception:
                    try:
                        j = resp.json()
                        text = json.dumps(j)
                    except Exception:
                        continue

                if not text:
                    continue

                # быстрый фильтр
                if '"ground_count"' not in text and '"floors"' not in text and '"items"' not in text:
                    continue

                # попробуем распарсить JSON
                data = None
                try:
                    data = json.loads(text)
                except Exception:
                    data = None

                if isinstance(data, dict):
                    if "items" in data and isinstance(data["items"], list):
                        for it in data["items"]:
                            floors = it.get("floors") or {}
                            if isinstance(floors, dict) and "ground_count" in floors:
                                floor_count = int(floors["ground_count"])
                                print(f"  ✔ Найдено этажей: {floor_count} (resp: {resp.url})")
                                break
                        if floor_count is not None:
                            break

                    # рекурсивный поиск в тексте JSON-объекта
                    jtext = json.dumps(data)
                    m = re.search(r'"ground_count"\s*:\s*(\d+)', jtext)
                    if m:
                        floor_count = int(m.group(1))
                        print(f"  ✔ Найдено этажей (вложенный): {floor_count} (resp: {resp.url})")
                        break

                # regex fallback прямо по тексту
                m2 = re.search(r'"ground_count"\s*:\s*(\d+)', text)
                if m2:
                    floor_count = int(m2.group(1))
                    print(f"  ✔ Найдено этажей (regex): {floor_count} (resp: {resp.url})")
                    break

            except Exception:
                continue

        # снимем listener
        try:
            page.off("response", on_response)
        except Exception:
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass

        # если нашли — закешируем результат (извлечь building_id из geo_url)
        if floor_count is not None:
            m = re.search(r"/geo/(\d+)", geo_url)
            if m:
                bid = m.group(1)
                try:
                    self.set_cached_floor(bid, floor_count)
                except Exception:
                    pass
            return {"floor_count": floor_count}

        # fallback: искать прямо в HTML/скриптах
        html = page.content()
        gc = self._extract_ground_count_from_text(html)
        if gc is not None:
            # кешируем, если можем извлечь id из geo_url
            m = re.search(r"/geo/(\d+)", geo_url)
            if m:
                try:
                    self.set_cached_floor(m.group(1), gc)
                except Exception:
                    pass
            print(f"  ✔ Найдено этажей в HTML fallback: {gc}")
            return {"floor_count": gc}

        print("  ❌ ground_count не найден")
        return None




    def update_db_with_floor_data(self):
        # ====== обработка Ctrl+C (graceful stop) ======
        self._stop_requested = False

        def _signal_handler(signum, frame):
            print("\n⛔ Получен сигнал остановки. Корректно завершаюсь...")
            self._stop_requested = True

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        # ====== считаем ТОЛЬКО необработанные адреса ======
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*)
            FROM buildings
            WHERE dgis_url IS NOT NULL
            AND dgis_url != ''
            AND floor_count IS NULL
        """)
        total = cursor.fetchone()[0]
        conn.close()

        if total == 0:
            print("✔ Нет адресов для обработки (всё уже собрано)")
            return

        print(f"🔍 Адресов к обработке: {total}")

        # ====== Playwright (один браузер на всё) ======
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()

            # ускоряем — блокируем картинки, видео, шрифты
            context.route(
                "**/*",
                lambda route, request: (
                    route.abort()
                    if request.resource_type in ("image", "font", "media")
                    else route.continue_()
                )
            )

            page = context.new_page()

            try:
                conn = sqlite3.connect(self.temp_db_path)
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT id, dgis_url
                    FROM buildings
                    WHERE dgis_url IS NOT NULL
                    AND dgis_url != ''
                    AND floor_count IS NULL
                """)
                rows = cursor.fetchall()

                for row_id, dgis_url in tqdm(
                    rows,
                    total=total,
                    desc="🏢 Сбор этажности",
                    unit="адр",
                    mininterval=1.0
                ):
                    if self._stop_requested:
                        break

                    try:
                        result = self.process_building_url(page, dgis_url)
                    except Exception as e:
                        print(f"\n⚠ Ошибка при обработке {dgis_url}: {e}")
                        result = None

                    if result and "floor_count" in result:
                        cursor.execute(
                            "UPDATE buildings SET floor_count = ? WHERE id = ?",
                            (result["floor_count"], row_id)
                        )

                    # сохраняем после КАЖДОГО адреса — для resume
                    conn.commit()

                    # небольшая пауза (не обязательно, но полезно)
                    time.sleep(0.2)

                conn.close()

            finally:
                browser.close()

        print("✅ update_db_with_floor_data завершена")


    def export_to_csv(self):
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM buildings")
        rows = cursor.fetchall()
        cursor.execute("PRAGMA table_info(buildings)")
        columns_info = cursor.fetchall()
        column_names = [col[1] for col in columns_info]
        conn.close()

        with open(self.results_csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(column_names)
            for row in rows:
                writer.writerow(row)

        print(f"Результаты сохранены в: {self.results_csv_path}")

    def run(self):
        print("Загрузка данных из CSV во временную базу данных...")
        self.load_csv_to_db()
        print("Обновление базы данных информацией об этажности (запускаю браузер)...")
        self.update_db_with_floor_data()
        print("Экспорт результатов в CSV файл...")
        self.export_to_csv()
        print("Готово.")
        

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Использование: python floor_parser.py <путь_к_csv_файлу>")
        sys.exit(1)
    csv_file_path = sys.argv[1]
    if not Path(csv_file_path).exists():
        print(f"Файл не найден: {csv_file_path}")
        sys.exit(1)
    parser = FloorParser(csv_file_path)
    parser.run()
