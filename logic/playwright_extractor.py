# logic/playwright_extractor.py
from pathlib import Path
import sqlite3
import json
import re
import time
from typing import Optional, Any, Callable, Dict

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

GEO_RE = re.compile(r"/geo/(\d+)(?:/|\?|$)")
FALLBACK_DIGIT_RE = re.compile(r"\b(\d{9,20})\b")


def _extract_geo_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = GEO_RE.search(text)
    if m:
        return m.group(1)
    m2 = FALLBACK_DIGIT_RE.search(text)
    if m2:
        return m2.group(1)
    return None


def _find_geo_in_obj(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    if isinstance(obj, str):
        return _extract_geo_from_text(obj)
    if isinstance(obj, (int, float)):
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                r = _extract_geo_from_text(k)
                if r:
                    return r
            r2 = _find_geo_in_obj(v)
            if r2:
                return r2
        return None
    if isinstance(obj, list):
        for it in obj:
            r = _find_geo_in_obj(it)
            if r:
                return r
        return None
    return None


def _ensure_dgis_column(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(buildings)")
    cols = [r[1] for r in cur.fetchall()]
    if "dgis_id" not in cols:
        cur.execute("ALTER TABLE buildings ADD COLUMN dgis_id TEXT")
        conn.commit()


def _build_search_url(city: str, street: str, address: str) -> str:
    from urllib.parse import quote_plus
    q = f"{street} {address}".strip()
    return f"https://2gis.ru/{quote_plus(city)}/search/{quote_plus(q)}"


def run_extract_ids(
    db_path: str,
    *,
    city_override: Optional[str] = None,
    headless: bool = False,              # по опыту: по умолчанию False (видимый), чтобы меньше детектов робота
    delay: float = 0.6,
    limit: int = 0,
    timeout_ms: int = 12000,
    screenshots_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[int, str, Dict[str,int]], None]] = None,
) -> Dict[str,int]:
    """
    Основная функция для извлечения dgis_id и записи в таблицу buildings.dgis_id.

    Аргументы:
      db_path - путь к sqlite (строка)
      city_override - если указан, используется вместо города из DB
      headless - True или False (рекомендуется False для надёжности)
      delay - пауза между запросами (сек)
      limit - ограничение по кол-ву записей (0 = все)
      timeout_ms - timeout для загрузки страниц (ms)
      screenshots_dir - если указан, сохраняет скриншоты ошибок
      progress_callback(percent:int, message:str, counts:dict) - optional callback

    Возвращает статистику: dict {checked, updated, not_found}
    """
    db_p = Path(db_path)
    if not db_p.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    screenshots_dir_p = Path(screenshots_dir) if screenshots_dir else None
    if screenshots_dir_p:
        screenshots_dir_p.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_p))
    try:
        _ensure_dgis_column(conn)
        cur = conn.cursor()
        # select rows (dgis_id is null or empty)
        cur.execute("""
            SELECT b.id, s.name AS street_name, b.address, c.name AS city_name, b.raw_json
            FROM buildings b
            JOIN streets s ON b.street_id = s.id
            JOIN districts d ON s.district_id = d.id
            JOIN cities c ON d.city_id = c.id
            WHERE b.dgis_id IS NULL OR b.dgis_id = ''
            ORDER BY c.name, d.name, s.name
        """)
        rows = []
        # соберём rows в список (можно ещё читать пачками; но для UI задач это нормально)
        fetch_size = 1000
        while True:
            batch = cur.fetchmany(fetch_size)
            if not batch:
                break
            rows.extend(batch)
        total = len(rows)
        if total == 0:
            if progress_callback:
                progress_callback(100, "Нет зданий для извлечения id", {"total": 0})
            return {"checked": 0, "updated": 0, "not_found": 0}

        checked = 0
        updated = 0
        not_found = 0

        # Playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(viewport={"width": 1200, "height": 900})
            page = context.new_page()

            try:
                for idx, row in enumerate(rows, start=1):
                    b_id, street, address, city_db, raw_json = row
                    checked += 1
                    city = (city_override or city_db or "irkutsk")
                    search_url = _build_search_url(city, street, address)

                    # progress
                    if progress_callback:
                        pct = int((idx-1) / max(1, total) * 100)
                        progress_callback(pct, f"Обрабатываю {idx}/{total}: {street} {address}", {"checked": checked, "updated": updated, "not_found": not_found, "total": total})

                    # 0) попробовать raw_json
                    found = None
                    if raw_json:
                        try:
                            parsed = json.loads(raw_json)
                        except Exception:
                            parsed = raw_json
                        found = _find_geo_in_obj(parsed)

                    if not found:
                        # 1) открыть search_url и попытаться узнать id
                        try:
                            try:
                                page.goto(search_url, timeout=timeout_ms)
                            except PlaywrightTimeoutError:
                                # продолжим — возможно частичный контент уже доступен
                                pass
                            try:
                                page.wait_for_load_state("networkidle", timeout=3000)
                            except PlaywrightTimeoutError:
                                pass

                            # сначала прямые ссылки
                            anchors = page.query_selector_all("a[href*='/geo/']")
                            if anchors:
                                href = anchors[0].get_attribute("href")
                                found = _extract_geo_from_text(href) if href else None

                            # если не найдено — кликаем первый результат
                            if not found:
                                selectors_to_try = [
                                    "a[href*='/geo/']",
                                    "div.search-result a",
                                    "div.search-list a",
                                    "div.result a",
                                    "a.link",
                                    "div.card a",
                                    "div[data-result] a",
                                ]
                                for sel in selectors_to_try:
                                    try:
                                        locator = page.locator(sel).first
                                        if not locator:
                                            continue
                                        try:
                                            locator.wait_for(state="visible", timeout=1500)
                                        except PlaywrightTimeoutError:
                                            continue
                                        try:
                                            locator.click(timeout=2000)
                                        except Exception:
                                            # fallback js-click
                                            handle = locator.element_handle()
                                            if handle:
                                                page.evaluate("(el) => el.click()", handle)
                                        # ждём либо смены url с /geo/, либо появления ссылок
                                        try:
                                            page.wait_for_url(re_compile_geo(), timeout=4000)
                                            cururl = page.url
                                            found = _extract_geo_from_text(cururl)
                                        except PlaywrightTimeoutError:
                                            pass

                                        if not found:
                                            anchors2 = page.query_selector_all("a[href*='/geo/']")
                                            if anchors2:
                                                href2 = anchors2[0].get_attribute("href") or ""
                                                found = _extract_geo_from_text(href2)
                                        if found:
                                            break
                                    except Exception:
                                        continue
                        except Exception as e:
                            # логирование через callback
                            if progress_callback:
                                progress_callback(int((idx-1)/max(1,total)*100), f"Ошибка Playwright: {e}", {"checked": checked, "updated": updated, "not_found": not_found})
                            # при ошибке сохраняем скриншот (если разрешено)
                            try:
                                if screenshots_dir_p:
                                    out = screenshots_dir_p / f"err_{b_id}_{int(time.time()*1000)}.png"
                                    page.screenshot(path=str(out), full_page=True)
                            except Exception:
                                pass

                    if found:
                        try:
                            cur_local = conn.cursor()
                            cur_local.execute("UPDATE buildings SET dgis_id = ? WHERE id = ?", (str(found), b_id))
                            conn.commit()
                            updated += 1
                        except Exception as e:
                            # запись упала — не останавливаемся
                            if progress_callback:
                                progress_callback(int((idx)/max(1,total)*100), f"Ошибка записи dgis_id для id={b_id}: {e}", {"checked": checked, "updated": updated, "not_found": not_found})
                    else:
                        not_found += 1

                    # задержка между запросами
                    time.sleep(delay)

                    # лимит
                    if limit and idx >= limit:
                        break

                    # обновление прогресса
                    if progress_callback:
                        pct = int(idx / max(1, total) * 100)
                        progress_callback(pct, f"Обработано {idx}/{total}", {"checked": checked, "updated": updated, "not_found": not_found, "total": total})
            finally:
                try:
                    page.close()
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass
    finally:
        conn.close()

    # финальный callback
    if progress_callback:
        progress_callback(100, "Готово", {"checked": checked, "updated": updated, "not_found": not_found, "total": total})

    return {"checked": checked, "updated": updated, "not_found": not_found, "total": total}
