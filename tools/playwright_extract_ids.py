#!/usr/bin/env python3
"""
tools/playwright_extract_ids.py

Улучшенная Playwright-версия для извлечения geo-id из 2gis: /geo/<id>/...
Использует несколько стратегий поиска и надёжно закрывает браузер/БД по завершении.

Требования:
  pip install playwright
  python -m playwright install chromium

Пример запуска:
  python tools/playwright_extract_ids.py --db data/temp/55c1282dd7394968868625cffce94546.db \
      --city irkutsk --headless True --delay 0.6 --limit 200 --screenshots data/temp/ps_screens

Параметры:
  --db         путь к sqlite файлу сессии
  --city       (опционально) переопределение города для search URL
  --headless   true/false (по умолчанию true)
  --delay      пауза между запросами (сек)
  --limit      ограничение по числу обработанных записей (0 = все)
  --timeout-ms таймаут загрузки/ожидания страницы в миллисекундах
  --screenshots путь для сохранения скриншотов при ошибках (по умолчанию data/temp/ps_screens)
"""
import argparse
import sqlite3
import json
import re
import time
from pathlib import Path
from typing import Optional, Any, Tuple, Generator

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

GEO_RE = re.compile(r"/geo/(\d+)(?:/|\?|$)")
FALLBACK_DIGIT_RE = re.compile(r"\b(\d{9,20})\b")


def ensure_dgis_column(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(buildings)")
    cols = [r[1] for r in cur.fetchall()]
    if "dgis_id" not in cols:
        cur.execute("ALTER TABLE buildings ADD COLUMN dgis_id TEXT")
        conn.commit()


def extract_geo_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = GEO_RE.search(text)
    if m:
        return m.group(1)
    m2 = FALLBACK_DIGIT_RE.search(text)
    if m2:
        return m2.group(1)
    return None


def find_geo_in_obj(obj: Any) -> Optional[str]:
    """Рекурсивно ищем /geo/<id>/ в любом JSON-дереве или строковом поле."""
    if obj is None:
        return None
    if isinstance(obj, str):
        return extract_geo_from_text(obj)
    if isinstance(obj, (int, float)):
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                r = extract_geo_from_text(k)
                if r:
                    return r
            r2 = find_geo_in_obj(v)
            if r2:
                return r2
        return None
    if isinstance(obj, list):
        for it in obj:
            r = find_geo_in_obj(it)
            if r:
                return r
        return None
    return None


def rows_generator(conn: sqlite3.Connection, limit: int = 0) -> Generator[Tuple[int, str, str, str, Optional[str]], None, None]:
    """
    Генератор строк: (building_id, street_name, address, city_name, raw_json)
    Отбирает строки, где dgis_id IS NULL OR ''.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT b.id, s.name as street_name, b.address, c.name as city_name, b.raw_json
        FROM buildings b
        JOIN streets s ON b.street_id = s.id
        JOIN districts d ON s.district_id = d.id
        JOIN cities c ON d.city_id = c.id
        WHERE b.dgis_id IS NULL OR b.dgis_id = ''
        ORDER BY c.name, d.name, s.name
    """)
    count = 0
    while True:
        row = cur.fetchone()
        if not row:
            break
        count += 1
        yield row
        if limit and count >= limit:
            break


def build_search_url(city: str, street: str, address: str) -> Tuple[str, str]:
    """Возвращает (query, url)"""
    from urllib.parse import quote_plus
    q = f"{street} {address}".strip()
    url = f"https://2gis.ru/{quote_plus(city)}/search/{quote_plus(q)}"
    return q, url


def save_screenshot_safe(page, out_dir: Path, prefix: str):
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)
        path = out_dir / f"{prefix}_{ts}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return None


def playwright_find_id(page, search_url: str, timeout_ms: int, screenshot_dir: Path) -> Optional[str]:
    """
    Открывает search_url и пытается найти /geo/<id>/ через несколько стратегий.
    Возвращает найденный id или None.
    """
    # Навигация с таймаутом
    try:
        page.goto(search_url, timeout=timeout_ms)
    except PlaywrightTimeoutError:
        # Продолжаем — возможно часть контента загрузилась
        pass

    # Подождать некоторое время networkidle (не обязательно успешно)
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except PlaywrightTimeoutError:
        pass

    # 1) Пробуем прямые ссылки (href)
    try:
        anchors = page.query_selector_all("a[href*='/geo/']")
        if anchors:
            href = anchors[0].get_attribute("href")
            if href:
                gid = extract_geo_from_text(href)
                if gid:
                    return gid
    except Exception:
        pass

    # 2) Попробуем кликнуть первый результат. Набор селекторов расширенный и безопасный.
    selectors_to_try = [
        "a[href*='/geo/']",
        "div.search-result a",
        "div.search-list a",
        "div.result a",
        "a.link",
        "div.card a",
        "div[data-result] a",
        ".search-results .result a",
        ".search-item a",
        "li._result a"
    ]

    for sel in selectors_to_try:
        try:
            locator = page.locator(sel).first
            if locator:
                # ждем видимость и кликабельность (короткий таймаут)
                try:
                    locator.wait_for(state="visible", timeout=2000)
                except PlaywrightTimeoutError:
                    # не виден — пробуем следующий селектор
                    continue
                # Попытка кликнуть
                try:
                    locator.click(timeout=3000)
                except Exception:
                    # fallback: js click
                    page.evaluate("(el) => el.click()", locator.element_handle())
                # Ждём либо навигации на /geo/, либо появления ссылки /geo/
                try:
                    # ждём URL с /geo/<digits>
                    page.wait_for_url(re_compile_geo(), timeout=5000)
                    cur = page.url
                    gid = extract_geo_from_text(cur)
                    if gid:
                        return gid
                except PlaywrightTimeoutError:
                    pass

                # после клика — попробуем найти ссылки снова
                try:
                    anchors2 = page.query_selector_all("a[href*='/geo/']")
                    if anchors2:
                        href2 = anchors2[0].get_attribute("href")
                        if href2:
                            gid2 = extract_geo_from_text(href2)
                            if gid2:
                                return gid2
                except Exception:
                    pass
        except Exception:
            continue

    # 3) Last resort: перебор всех ссылок на странице
    try:
        all_anchors = page.query_selector_all("a")
        for a in all_anchors:
            href = a.get_attribute("href")
            if href:
                gid = extract_geo_from_text(href)
                if gid:
                    return gid
    except Exception:
        pass

    # на неудачу — сохранить скриншот для анализа
    try:
        save_screenshot_safe(page, screenshot_dir, "not_found")
    except Exception:
        pass

    return None


def re_compile_geo():
    # Возвращаем regex-паттерн для page.wait_for_url
    import re
    return re.compile(r".*/geo/\d+.*")


def update_dgis(conn: sqlite3.Connection, b_id: int, dgis_id: str):
    cur = conn.cursor()
    cur.execute("UPDATE buildings SET dgis_id = ? WHERE id = ?", (str(dgis_id), b_id))
    conn.commit()


def process(db_path: Path, city_override: Optional[str], headless: bool, delay: float,
            limit: int, timeout_ms: int, screenshots_dir: Path):
    conn = sqlite3.connect(str(db_path))
    ensure_dgis_column(conn)

    browser = None
    context = None
    page = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(viewport={"width": 1200, "height": 900})
            page = context.new_page()

            total_checked = 0
            total_updated = 0
            total_not_found = 0
            total_skipped_existing = 0

            gen = rows_generator(conn, limit)
            for idx, row in enumerate(gen, start=1):
                b_id, street, address, city_db, raw_json = row
                total_checked += 1
                city = (city_override or city_db or "irkutsk")
                q, search_url = build_search_url(city, street, address)
                print(f"[{idx}] id={b_id} -> {city} / {street} {address}")

                # 0) сначала пробуем найти id в raw_json
                found = None
                if raw_json:
                    try:
                        parsed = json.loads(raw_json)
                    except Exception:
                        parsed = raw_json
                    found = find_geo_in_obj(parsed)

                if found:
                    print(f"  -> найден в raw_json: {found} (запись обновляется)")
                    try:
                        update_dgis(conn, b_id, found)
                        total_updated += 1
                    except Exception as e:
                        print("  Ошибка записи в БД:", e)
                    time.sleep(delay)
                    # проверяем лимит вручную — если достигнут, корректно выходим
                    if limit and idx >= limit:
                        print("Достигнут лимит — выходим")
                        break
                    continue

                # 1) Playwright поиск
                try:
                    found = playwright_find_id(page, search_url, timeout_ms=timeout_ms, screenshot_dir=screenshots_dir)
                except Exception as e:
                    print(f"  playwright error: {e}")
                    found = None

                if found:
                    print(f"  -> найден через Playwright: {found} (обновляем DB)")
                    try:
                        update_dgis(conn, b_id, found)
                        total_updated += 1
                    except Exception as e:
                        print("  Ошибка записи в БД:", e)
                else:
                    print("  -> geo id не найден")
                    total_not_found += 1

                time.sleep(delay)
                if limit and idx >= limit:
                    print("Достигнут лимит — выходим")
                    break

            print(f"Готово. Checked={total_checked} updated={total_updated} not_found={total_not_found} skipped_existing={total_skipped_existing}")

    finally:
        # Гарантированное закрытие ресурсов
        try:
            if page:
                page.close()
        except Exception:
            pass
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", "-d", required=True, help="Путь к sqlite DB (например data/temp/<session_id>.db)")
    parser.add_argument("--city", help="Переопределить город (например irkutsk). Если не указан — берём из DB")
    parser.add_argument("--headless", type=lambda s: s.lower() in ("1", "true", "yes"), default=True)
    parser.add_argument("--delay", type=float, default=0.6, help="Пауза между запросами (сек)")
    parser.add_argument("--limit", type=int, default=0, help="Лимит по числу обрабатываемых записей (0 = все)")
    parser.add_argument("--timeout-ms", type=int, default=12000, help="Timeout загрузки/ожидания страницы в ms")
    parser.add_argument("--screenshots", default="data/temp/ps_screens", help="Папка для скриншотов при ошибках")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print("DB не найден:", db_path)
        raise SystemExit(1)

    screenshots_dir = Path(args.screenshots)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    process(db_path, args.city, args.headless, args.delay, args.limit, args.timeout_ms, screenshots_dir)
