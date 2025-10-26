# logic/parsing_addresses.py
import json
import sqlite3
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from logic.two_gis_cli import TwoGisCliParser

# Попытка подключить реальный парсер Ginfo
try:
    from parsers.ginfo.ginfo_parser import GinfoParser
    HAS_REAL_PARSER = True
except Exception:
    HAS_REAL_PARSER = False
    class GinfoParser:
        def __init__(self, log=None, base_url=None):
            self.log = log or (lambda s: None)
            self.BASE_URL = base_url or "https://irkutsk.ginfo.ru"
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

        # пытаемся предоставить session если парсер умеет
        try:
            import requests
            if not hasattr(self.parser, "session") or self.parser.session is None:
                s = requests.Session()
                s.headers.update({"User-Agent": "Mozilla/5.0"})
                self.parser.session = s
        except Exception:
            pass

    def parse_districts_to_db(self, db_path: str, progress_callback=None):
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
                percent = int((idx / max(1, len(districts))) * 100 * 0.8)
                progress_callback(percent, f"Сохранено районов: {idx}/{len(districts)}", {"districts": idx})

        conn.commit()
        conn.close()
        self.log(f"Районы сохранены в БД: добавлено {inserted}")

        if progress_callback:
            progress_callback(100, "Районы: готово", {"districts_total": inserted})

    def parse_all_streets_to_db(self, db_path: str, progress_callback=None, max_workers=4):
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
                percent = int((district_index - 1) / max(1, total_districts) * 5)
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
                        base = 0
                        local_part = (processed / max(1, total_in_this_district))
                        percent = int(((district_index - 1) / max(1, total_districts) + (local_part / max(1, total_districts))) * 100)
                        progress_callback(percent, f"Улицы: {processed}/{total_in_this_district} в районе {district_index}/{total_districts}",
                                          {"streets": total_streets, "buildings": total_buildings})
                    time.sleep(0.03)

        conn.close()
        self.log(f"Парсинг улиц завершён. Добавлено улиц: {total_streets}, домов: {total_buildings}")

    @staticmethod
    def _is_raw_json_primitive(raw_json_text: str) -> bool:
        if raw_json_text is None:
            return True
        raw = raw_json_text.strip()
        if raw == "":
            return True
        try:
            parsed = json.loads(raw)
        except Exception:
            return True
        if isinstance(parsed, (str, int, float, bool, type(None))):
            return True
        if isinstance(parsed, list):
            all_primitive = all(isinstance(item, (str, int, float, bool, type(None))) for item in parsed)
            return all_primitive
        return False

    def parse_buildings_with_2gis(self, db_path: str, parser_cmd: str = "parser-2gis",
                                  max_workers: int = 2, progress_callback=None, city_name: str = None):
        db_p = Path(db_path)
        if not db_p.exists():
            raise FileNotFoundError(f"DB not found: {db_p}")

        if not city_name:
            try:
                conn = sqlite3.connect(str(db_p))
                cur = conn.cursor()
                cur.execute("SELECT name FROM cities LIMIT 1")
                r = cur.fetchone()
                conn.close()
                if r and r[0]:
                    city_name = r[0]
                else:
                    conn = sqlite3.connect(str(db_p))
                    cur = conn.cursor()
                    cur.execute("SELECT ginfo_url FROM cities LIMIT 1")
                    r = cur.fetchone()
                    conn.close()
                    if r and r[0]:
                        try:
                            city_name = r[0].split("//")[-1].split(".")[0]
                        except Exception:
                            city_name = r[0]
                    else:
                        city_name = "irkutsk"
            except Exception:
                city_name = "irkutsk"

        parser = TwoGisCliParser(output_dir=str(Path(db_p).parent / "2gis_results"),
                                 parser_cmd=parser_cmd,
                                 max_retries=2,
                                 retry_backoff=1.0,
                                 logger=self.log)

        conn = sqlite3.connect(str(db_p))
        cur = conn.cursor()

        cur.execute("""
            SELECT b.id, b.address, s.name AS street_name, d.name AS district_name, c.name AS city_name, b.raw_json
            FROM buildings b
            JOIN streets s ON b.street_id = s.id
            JOIN districts d ON s.district_id = d.id
            JOIN cities c ON d.city_id = c.id
            ORDER BY c.name, d.name, s.name
        """)
        rows = []
        fetch_size = 1000
        while True:
            batch = cur.fetchmany(fetch_size)
            if not batch:
                break
            for r in batch:
                b_id, address, street_name, district_name, city_from_db, raw_json_text = r
                if self._is_raw_json_primitive(raw_json_text):
                    rows.append((b_id, address, street_name, district_name, city_from_db))

        total = len(rows)
        if total == 0:
            conn.close()
            self.log("Нет новых зданий для обработки 2GIS.")
            if progress_callback:
                progress_callback(100, "Нет зданий для обработки", {"total": 0})
            return {"total": 0, "processed": 0, "success": 0, "errors": 0}

        def worker(row):
            b_id, address, street_name, district_name, city_from_db = row
            city_for_query = city_name or city_from_db or ""
            query_parts = []
            if city_for_query:
                query_parts.append(city_for_query)
            if street_name:
                query_parts.append(street_name)
            if address:
                query_parts.append(str(address))
            query = " ".join([p for p in query_parts if p])

            try:
                # <-- здесь используем совместимый вызов (run / run_cli / run_cli_shell)
                if hasattr(parser, "run"):
                    result = parser.run(city_for_query, query)
                elif hasattr(parser, "run_cli"):
                    result = parser.run_cli(city_for_query, query)
                elif hasattr(parser, "run_cli_shell"):
                    result = parser.run_cli_shell(city_for_query, query)
                else:
                    raise RuntimeError("TwoGis parser has no runnable entrypoint (run/run_cli/run_cli_shell)")

                try:
                    raw = json.dumps(result, ensure_ascii=False) if result is not None else ""
                except Exception:
                    raw = ""
                conn_local = sqlite3.connect(str(db_p))
                cur_local = conn_local.cursor()
                cur_local.execute("UPDATE buildings SET raw_json = ? WHERE id = ?", (raw, b_id))
                conn_local.commit()
                conn_local.close()
                return True, b_id, query
            except Exception as e:
                self.log(f"2GIS: ошибка при обработке id={b_id} query='{query}': {e}")
                try:
                    conn_err = sqlite3.connect(str(db_p))
                    cur_err = conn_err.cursor()
                    cur_err.execute("UPDATE buildings SET raw_json = ? WHERE id = ?", ("", b_id))
                    conn_err.commit()
                    conn_err.close()
                except Exception:
                    pass
                return False, b_id, str(e)

        processed = 0
        success = 0
        errors = 0

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(worker, row): row for row in rows}
            for fut in as_completed(futures):
                ok, b_id, info = fut.result()
                processed += 1
                if ok:
                    success += 1
                else:
                    errors += 1

                if progress_callback:
                    pct = int(processed / max(1, total) * 100)
                    progress_callback(pct, f"Обработано {processed}/{total} (успех: {success}, ошибки: {errors})",
                                      {"total": total, "processed": processed, "success": success, "errors": errors})

        conn.close()
        self.log(f"2GIS: обработано зданий: {processed}, успех: {success}, ошибки: {errors}")
        if progress_callback:
            progress_callback(100, f"Готово. Успех: {success}, ошибки: {errors}", {"total": total, "processed": processed, "success": success, "errors": errors})

        return {"total": total, "processed": processed, "success": success, "errors": errors}

    def _safe_get_street_info(self, url):
        try:
            return self.parser.get_street_info(url)
        except Exception as e:
            self.log(f"Ошибка get_street_info для {url}: {e}")
            return {}
