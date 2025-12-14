#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Переработанный парсер этажности для 2GIS.
Запуск: python floor_parser.py /path/to/All21209.csv
Требования:
    pip install playwright requests
    python -m playwright install chromium
"""
import csv
import json
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def extract_city_alias(dgis_url: str) -> str:
        path = urlparse(dgis_url).path.strip("/")
        parts = path.split("/")
        return parts[0] if parts else "irkutsk"
    

class FloorParser:
    def __init__(self, csv_file_path: str):
        self.csv_file_path = Path(csv_file_path)
        self.output_dir = Path("output")
        self.output_dir.mkdir(exist_ok=True)
        self.temp_db_path = self.output_dir / "temp_buildings.db"
        # name for result csv: originalname_floors.csv
        self.results_csv_path = self.output_dir / f"{self.csv_file_path.stem}_floors.csv"

        self._create_temp_db()
        self._ensure_russian_column()

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

    def load_csv_to_db(self):
        # читаем CSV (поддержка BOM) и загружаем в БД
        with open(self.csv_file_path, 'r', encoding='utf-8-sig', newline='') as csvfile:
            sample = csvfile.read(4096)
            csvfile.seek(0)
            try:
                delimiter = csv.Sniffer().sniff(sample).delimiter
            except Exception:
                # часто 2GIS выгрузки используют ';'
                delimiter = ';'

            reader = csv.DictReader(csvfile, delimiter=delimiter)
            conn = sqlite3.connect(self.temp_db_path)
            cursor = conn.cursor()

            for row in reader:
                cursor.execute('''
                    INSERT INTO buildings (
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
        Открываем исходный URL, собираем все xhr/fetch/json ответы, ищем history_objects/objectType:building/id
        Если найден building_id — формируем geo URL с более корректным базовым путём и вызываем get_building_data_by_geo.
        """
        print("\n=== process_building_url:", url)
        building_id = None
        collected = []

        def on_response(resp):
            try:
                rt = (resp.request.resource_type or "").lower()
                ctype = (resp.headers.get("content-type") or "").lower()
                # собираем xhr/fetch и любые ответы, где есть текст/json
                if rt in ("xhr","fetch") or "application/json" in ctype or "text/plain" in ctype:
                    collected.append(resp)
                    # тонкая отладка:
                    # print("RESP:", rt, resp.url)
            except Exception:
                pass

        page.on("response", on_response)

        try:
            page.goto(url, timeout=30000)
        except Exception as e:
            print("  goto error:", e)

        # даём время на XHR (увеличь если нужно)
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(500)

        # 1) Ищем точные совпадения — prefer JSON parsing и context 'history_objects' / objectType == 'building'
        for resp in collected:
            try:
                text = None
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
                if '"history_objects"' not in text and '"objectType"' not in text and '"building_id"' not in text:
                    continue

                # пробуем распарсить JSON
                data = None
                try:
                    data = json.loads(text)
                except Exception:
                    data = None

                if isinstance(data, dict):
                    # история в result.history_objects
                    if "result" in data and isinstance(data["result"], dict):
                        ho = data["result"].get("history_objects")
                        if isinstance(ho, list):
                            for obj in ho:
                                if isinstance(obj, dict) and obj.get("objectType") == "building" and obj.get("id"):
                                    cand = str(obj.get("id"))
                                    # проверим plausibility: длинный id, начинается с 7 (типично)
                                    if len(cand) >= 10 and cand.startswith("7"):
                                        building_id = cand
                                        print("  → found building_id (result.history_objects):", building_id, " (resp:", resp.url, ")")
                                        break
                            if building_id:
                                break

                    # иногда history_objects в корне
                    if "history_objects" in data and isinstance(data["history_objects"], list):
                        for obj in data["history_objects"]:
                            if isinstance(obj, dict) and obj.get("objectType") == "building" and obj.get("id"):
                                cand = str(obj.get("id"))
                                if len(cand) >= 10 and cand.startswith("7"):
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
                                if len(cand) >= 10 and cand.startswith("7"):
                                    building_id = cand
                                    print("  → found building_id (items.address.building_id):", building_id, " (resp:", resp.url, ")")
                                    break
                        if building_id:
                            break

                # 2) regex более строгий: ищем objectType":"building" рядом с id
                m = re.search(r'"objectType"\s*:\s*"building"[^}]{0,300}?"id"\s*:\s*"(7\d{8,})"', text, re.IGNORECASE | re.DOTALL)
                if m:
                    cand = m.group(1)
                    if len(cand) >= 10:
                        building_id = cand
                        print("  → found building_id (regex objectType nearby):", building_id, " (resp:", resp.url, ")")
                        break

                # 3) если не найдено, не берём любой id (раньше это давало 906446), пропускаем
            except Exception:
                continue

        # 4) снимем listener
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass

        # 5) fallback: попробовать найти /geo/ в DOM anchors
        if not building_id:
            try:
                anchors = page.query_selector_all("a[href*='/geo/'], a[href*='geo/']")
                for a in anchors:
                    try:
                        href = a.get_attribute("href") or ""
                        m = re.search(r"/geo/(\d{6,})", href)
                        if m:
                            cand = m.group(1)
                            if len(cand) >= 10 and cand.startswith("7"):
                                building_id = cand
                                print("  → found building_id from href:", building_id, " (href:", href, ")")
                                break
                    except Exception:
                        continue
            except Exception:
                pass

        # 6) ещё fallback: искать в HTML/скриптах, но тоже строгий поиск рядом с objectType:"building"
        if not building_id:
            try:
                html = page.content()
                m = re.search(r'"objectType"\s*:\s*"building"[^}]{0,300}?"id"\s*:\s*"(7\d{8,})"', html, re.IGNORECASE | re.DOTALL)
                if m:
                    building_id = m.group(1)
                    print("  → found building_id in HTML (regex):", building_id)
            except Exception:
                pass

        if not building_id:
            print("  ❌ building_id не найден (строгая проверка). Раньше ловились короткие id — это мог быть id фото/товара.")
            return None

        # 7) Выбираем хороший базовый путь для geo (из collected ответов или original_url)
        geo_base = self._choose_geo_base_from_responses(collected, url)
        # убедимся что geo_base ок (если возвращён только domain без city alias, добавим nothing — /geo/<id> всё равно рабоч)
        geo_url = f"{geo_base.rstrip('/')}/geo/{building_id}"
        print("  → переходим на geo:", geo_url)
        return self.get_building_data_by_geo(page, geo_url)




    def get_building_data_by_geo(self, page, geo_url: str):
        """
        Переходим на geo URL, собираем XHR/json ответы и ищем items[].floors.ground_count
        """
        floor_count = None
        responses = []

        def on_response(resp):
            try:
                rt = resp.request.resource_type or ""
                ctype = (resp.headers.get("content-type") or "").lower()
                if rt in ("xhr", "fetch") or "application/json" in ctype or "text/plain" in ctype:
                    responses.append(resp)
            except Exception:
                pass

        page.on("response", on_response)

        try:
            page.goto(geo_url, timeout=30000)
        except Exception as e:
            print(f"  Ошибка при заходе на {geo_url}: {e}")

        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        page.wait_for_timeout(5000)

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

                # быстрый поиск
                if '"ground_count"' not in text and '"floors"' not in text and '"items"' not in text:
                    continue

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

                    # рекурсивный текстовый поиск
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

        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass

        if floor_count is not None:
            return {"floor_count": floor_count}

        # последний fallback: искать в HTML/скриптах
        html = page.content()
        gc = self._extract_ground_count_from_text(html)
        if gc is not None:
            print(f"  ✔ Найдено этажей в HTML fallback: {gc}")
            return {"floor_count": gc}

        print("  ❌ ground_count не найден")
        return None




    def update_db_with_floor_data(self):
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, dgis_url FROM buildings WHERE dgis_url IS NOT NULL AND dgis_url != ''")
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            print("Нет записей с 2GIS URL в базе.")
            return

        # запускаем playwright один раз и переиспользуем браузер/страницу
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)  # False чтобы было видно окно; на сервере используйте True
            context = browser.new_context()
            page = context.new_page()

            for row_id, dgis_url in rows:
                if not dgis_url:
                    continue
                print(f"Обработка URL (id={row_id}): {dgis_url}")
                try:
                    floor_data = self.process_building_url(page, dgis_url)
                except Exception as e:
                    print(f"  Ошибка при обработке: {e}")
                    floor_data = None

                conn = sqlite3.connect(self.temp_db_path)
                cur = conn.cursor()
                if floor_data and 'floor_count' in floor_data:
                    cur.execute("UPDATE buildings SET floor_count = ?, \"Количество_этажей\" = ? WHERE id = ?", (floor_data['floor_count'], floor_data['floor_count'], row_id))
                    print(f"  Найдено этажей: {floor_data['floor_count']}")
                else:
                    print("  Информация об этажности не найдена")
                conn.commit()
                conn.close()

                time.sleep(1)  # пауза между запросами

            browser.close()

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
