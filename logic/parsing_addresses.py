# logic/parsing_addresses.py
import json
import sqlite3
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Попытка подключить реальный парсер
try:
    from parsers.ginfo.ginfo_parser import GinfoParser
    HAS_REAL_PARSER = True
except Exception:
    HAS_REAL_PARSER = False
    class GinfoParser:
        def __init__(self, log=None, base_url=None):
            self.log = log or (lambda s: None)
            self.BASE_URL = base_url or "https://irkutsk.ginfo.ru"
            self.session = None
        def get_districts(self):
            self.log("[stub] get_districts")
            return []
        def get_streets(self, district_url):
            self.log(f"[stub] get_streets({district_url})")
            return []
        def get_street_info(self, street_url):
            self.log(f"[stub] get_street_info({street_url})")
            return {"numbers_houses": []}

class ParsingAddressesManager:
    def __init__(self, log=print, base_url: str = None):
        self.log = log
        self.base_url = None
        if base_url:
            if base_url.startswith("http://") or base_url.startswith("https://"):
                self.base_url = base_url
            else:
                self.base_url = "https://" + base_url.rstrip("/")
        try:
            if self.base_url:
                self.parser = GinfoParser(log=self.log, base_url=self.base_url)
            else:
                self.parser = GinfoParser(log=self.log)
        except TypeError:
            self.parser = GinfoParser(log=self.log)

        # Попытка предоставить session для парсера (если он использует)
        try:
            import requests
            if not hasattr(self.parser, "session") or self.parser.session is None:
                s = requests.Session()
                s.headers.update({"User-Agent": "Mozilla/5.0"})
                self.parser.session = s
        except Exception:
            pass

    def parse_districts_to_db(self, db_path: str, progress_callback=None):
        """
        Получаем районы через parser.get_districts() и записываем в sqlite temp DB.
        progress_callback(percent:int, message:str, counts:dict)
        """
        db_p = Path(db_path)
        if not db_p.exists():
            raise FileNotFoundError(f"DB not found: {db_p}")

        if progress_callback:
            progress_callback(0, "Начало парсинга районов", {})

        self.log("Запуск парсинга районов (Ginfo)")
        districts = self.parser.get_districts() or []
        if not districts:
            self.log("Районы не найдены или парсер вернул пустой список.")
        conn = sqlite3.connect(str(db_p))
        cur = conn.cursor()

        city_name = None
        if self.base_url:
            try:
                city_name = self.base_url.split("//")[-1].split(".")[0]
            except Exception:
                city_name = self.base_url

        cur.execute("SELECT id FROM cities WHERE ginfo_url = ?", (self.base_url,))
        row = cur.fetchone()
        if row:
            city_id = row[0]
            self.log(f"Используется существующий город (id={city_id})")
        else:
            cur.execute("INSERT INTO cities(name, ginfo_url) VALUES (?, ?)", (city_name, self.base_url))
            city_id = cur.lastrowid
            self.log(f"Создан город (id={city_id}) name={city_name} url={self.base_url}")

        inserted = 0
        for idx, d in enumerate(districts, start=1):
            name = d.get("name")
            url = d.get("url")
            cur.execute("SELECT id FROM districts WHERE url = ?", (url,))
            if cur.fetchone():
                self.log(f"Район уже существует в БД: {name} ({url}) — пропускаем")
                continue
            cur.execute("INSERT INTO districts(city_id, name, url) VALUES (?, ?, ?)", (city_id, name, url))
            inserted += 1
            if progress_callback:
                percent = int((idx / max(1, len(districts))) * 100 * 0.8)  # доля для районoв (0..80%)
                progress_callback(percent, f"Сохранено районов: {idx}/{len(districts)}", {"districts": idx})

        conn.commit()
        conn.close()
        self.log(f"Районы сохранены в БД: добавлено {inserted}")

        if progress_callback:
            progress_callback(100, "Районы: готово", {"districts_total": inserted})

    def parse_all_streets_to_db(self, db_path: str, progress_callback=None, max_workers=4):
        """
        Для каждой записи в districts получаем список улиц и затем info по каждой улице.
        progress_callback(percent:int, message:str, counts:dict)
        """
        db_p = Path(db_path)
        if not db_p.exists():
            raise FileNotFoundError(f"DB not found: {db_p}")

        conn = sqlite3.connect(str(db_p))
        cur = conn.cursor()

        cur.execute("SELECT id, name, url FROM districts")
        districts = cur.fetchall()
        if not districts:
            self.log("В базе нет районов — сначала выполните parse_districts")
            conn.close()
            return

        total_districts = len(districts)
        district_index = 0
        total_streets = 0
        total_buildings = 0

        for district_id, district_name, district_url in districts:
            district_index += 1
            if not district_url:
                self.log(f"Район {district_name} не содержит URL — пропускаем")
                continue
            self.log(f"Парсим улицы района: {district_name} ({district_url})")
            try:
                streets_urls = self.parser.get_streets(district_url) or []
            except Exception as e:
                self.log(f"Ошибка получения списка улиц для {district_name}: {e}")
                streets_urls = []

            if progress_callback:
                percent = int((district_index - 1) / max(1, total_districts) * 5)  # небольшая предварительная метка
                progress_callback(percent, f"Обрабатываю район {district_index}/{total_districts}", {"district": district_index})

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_url = {executor.submit(self._safe_get_street_info, u): u for u in streets_urls}
                processed = 0
                total_in_this_district = len(streets_urls)
                for future in as_completed(future_to_url):
                    s_url = future_to_url[future]
                    processed += 1
                    try:
                        info = future.result() or {}
                    except Exception as e:
                        self.log(f"Ошибка при парсинге улицы {s_url}: {e}")
                        info = {}

                    street_name = info.get("name") or s_url.split("/")[-1] or "unknown_street"
                    buildings = info.get("numbers_houses") or []
                    buildings_json = json.dumps(buildings, ensure_ascii=False)

                    cur.execute("SELECT id FROM streets WHERE url = ?", (s_url,))
                    existing = cur.fetchone()
                    if existing:
                        street_id = existing[0]
                    else:
                        cur.execute(
                            "INSERT INTO streets(district_id, name, url, buildings_json) VALUES (?, ?, ?, ?)",
                            (district_id, street_name, s_url, buildings_json)
                        )
                        street_id = cur.lastrowid
                        total_streets += 1
                        self.log(f"Добавлена улица: {street_name} ({s_url}) — id {street_id}")

                    for house in buildings:
                        address = str(house).strip()
                        cur.execute("SELECT id FROM buildings WHERE street_id = ? AND address = ?", (street_id, address))
                        if cur.fetchone():
                            continue
                        cur.execute("INSERT INTO buildings(street_id, address, url, raw_json) VALUES (?, ?, ?, ?)",
                                    (street_id, address, "", json.dumps(house, ensure_ascii=False)))
                        total_buildings += 1

                    conn.commit()
                    if progress_callback:
                        # percent от 0..100: здесь выделим диапазон 0..100 for streets; caller может map'ить
                        # для более плавного отображения: базовая доля = 0, добавляем локальную часть
                        base = 0
                        # local progress inside current district
                        local_part = (processed / max(1, total_in_this_district))
                        # simple global percent estimate:
                        percent = int(((district_index - 1) / max(1, total_districts) + (local_part / max(1, total_districts))) * 100)
                        progress_callback(percent, f"Улицы: {processed}/{total_in_this_district} в районе {district_index}/{total_districts}",
                                          {"streets": total_streets, "buildings": total_buildings})
                    time.sleep(0.03)

        conn.close()
        self.log(f"Парсинг улиц завершён. Добавлено улиц: {total_streets}, домов: {total_buildings}")

    def _safe_get_street_info(self, url):
        try:
            return self.parser.get_street_info(url)
        except Exception as e:
            self.log(f"Ошибка get_street_info для {url}: {e}")
            return {}
