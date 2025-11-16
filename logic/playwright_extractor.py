# logic/playwright_extractor.py
import asyncio
import sqlite3
import json
import re
from pathlib import Path
from urllib.parse import quote_plus
from typing import Optional, Callable, List, Tuple, Dict, Any

# playwright async API
from playwright.async_api import async_playwright, Page, Browser, TimeoutError as PlaywrightTimeoutError

# Helper: run blocking DB code in thread
async def _run_db(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _fetch_rows_sync(db_path: str, limit: int = 0) -> List[Tuple]:
    """
    Возвращает список записей, которые нужно обработать:
    (building_id, address, street_name, city_name, ginfo_url)
    Фильтр: dgis_id IS NULL OR dgis_id == ''
    Ordered by city/district/street/id
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    q = """
    SELECT b.id, COALESCE(b.address, ''), COALESCE(s.name, ''), COALESCE(c.name, ''), COALESCE(c.ginfo_url, '')
    FROM buildings b
    LEFT JOIN streets s ON b.street_id = s.id
    LEFT JOIN districts d ON s.district_id = d.id
    LEFT JOIN cities c ON d.city_id = c.id
    WHERE b.dgis_id IS NULL OR b.dgis_id = ''
    ORDER BY c.name, d.name, s.name, b.id
    """
    if limit and limit > 0:
        q += f" LIMIT {int(limit)}"
    cur.execute(q)
    rows = cur.fetchall()
    conn.close()
    return rows


def _write_dgis_id_sync(db_path: str, building_id: int, dgis_id: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE buildings SET dgis_id = ? WHERE id = ?", (dgis_id, building_id))
    conn.commit()
    conn.close()


def _mark_failed_sync(db_path: str, building_id: int):
    # Помечаем пустым raw_json чтобы не зациклиться — но можно адаптировать
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE buildings SET raw_json = ? WHERE id = ?", ("", building_id))
    conn.commit()
    conn.close()


def guess_city_from_ginfo(ginfo_url: str) -> Optional[str]:
    if not ginfo_url:
        return None
    try:
        host = ginfo_url.split("://", 1)[-1].split("/")[0]
        return host.split(".")[0]
    except Exception:
        return None


def build_search_query(city: str, street: str, address: str) -> str:
    # В отличие от синхронной версии, не включаем город в поисковый запрос
    # так как он уже будет в URL
    parts = []
    if street:
        parts.append(street)
    if address:
        parts.append(address)
    query = " ".join([p for p in parts if p])
    return query


def extract_id_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/(?:inside|geo)/([0-9A-Za-z_%-]+)", url)
    if m:
        return m.group(1)
    return None


async def _process_one(page: Page, url: str, timeout: int = 20000, log: Callable[[str], None] = print) -> Optional[str]:
    """
    Навигация к url (search URL), попытаемся дождаться результатов и получить page.url(),
    извлечь id (inside|geo). Возвращаем найденный id или None.
    """
    try:
        await page.goto(url, timeout=timeout)
    except PlaywrightTimeoutError:
        log(f"Playwright: timeout loading {url}")
        return None
    except Exception as e:
        log(f"Playwright: error loading {url}: {e}")
        return None

    # Ждем загрузки состояния networkidle для обеспечения полной загрузки страницы
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        # продолжаем работу, даже если не достигнуто networkidle
        pass

    # Если после перехода url уже содержит inside/geo -> сразу вернём
    cur = page.url
    found = extract_id_from_url(cur)
    if found:
        return found

    # Попробуем кликнуть первый результат (несколько стратегий)
    selectors_to_try = [
        "a[href*='/inside/']",
        "a[href*='/geo/']",
        "a.search-result__link",        # возможный селектор результатов
        "a.search-item__link",          # запасной
        ".searchResults a",             # общий запасной
        "a",                            # fallback: первый <a>
    ]
    for sel in selectors_to_try:
        try:
            # wait_for_selector с небольшим timeout
            el = await page.query_selector(sel)
            if el:
                try:
                    await el.click(timeout=5000)
                    # подождать навигацию/idle
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8000)
                    except PlaywrightTimeoutError:
                        # игнорируем — просто проверим URL
                        pass
                    cur = page.url
                    found = extract_id_from_url(cur)
                    if found:
                        return found
                except Exception:
                    # если click не сработал — попытаемся получить href из элемента
                    try:
                        href = await el.get_attribute("href")
                        if href:
                            found = extract_id_from_url(href)
                            if found:
                                return found
                    except Exception:
                        pass
        except Exception:
            continue

    # как последний вариант — ищем ссылки на странице и пробуем их href
    try:
        anchors = await page.query_selector_all("a")
        for a in anchors[:30]:  # не перебираем слишком много
            try:
                href = await a.get_attribute("href")
                if href:
                    found = extract_id_from_url(href)
                    if found:
                        return found
            except Exception:
                continue
    except Exception:
        pass

    return None


async def extract_ids_to_db(
    db_path: str,
    *,
    headless: bool = False,
    concurrency: int = 10,
    delay: float = 0.6,
    limit: int = 0,
    log: Callable[[str], None] = print,
    progress_callback: Optional[Callable[[int, str, Dict[str, int]], None]] = None,
    city: Optional[str] = None
) -> Dict[str, Any]:
    """
    Асинхронный extractor, который запускает несколько страниц в одном браузере.

    Параметры:
      - db_path: путь к sqlite db (session db)
      - headless: True/False
      - concurrency: число параллельных страниц (windows)
      - delay: задержка между запросами на одной странице (сек)
      - limit: максимум обрабатываемых записей (0 = без лимита)
      - log: callable(str) — куда писать логи
      - progress_callback(percent:int, message:str, counts:dict) — опционально
      - city: название города для поиска (переопределяет город из базы)

    Возвращает статистику: { total, processed, success, errors }
    """
    # 1) загрузим список задач (в отдельном потоке)
    rows = await _run_db(_fetch_rows_sync, db_path, limit)
    total = len(rows)
    if total == 0:
        if progress_callback:
            progress_callback(100, "No addresses to process", {"total": 0, "processed": 0, "success": 0, "errors": 0})
        return {"total": 0, "processed": 0, "success": 0, "errors": 0}

    # build tasks list: list of (id, address, street, city, ginfo_url)
    tasks_list = []
    for (b_id, address, street, city_db, ginfo_url) in rows:
        city_for = city or city_db or guess_city_from_ginfo(ginfo_url) or ""
        query = build_search_query(city_for, street, address)
        tasks_list.append((b_id, query, city_for))

    processed = 0
    success = 0
    errors = 0

    # internal queue for tasks
    q: asyncio.Queue = asyncio.Queue()
    for item in tasks_list:
        await q.put(item)

    # worker coroutine
    async def worker_task(name: str, browser: Browser):
        nonlocal processed, success, errors
        page = await browser.new_page()
        # set viewport / user agent to look real-ish (you can tweak)
        await page.set_viewport_size({"width": 1280, "height": 800})
        await page.set_extra_http_headers({"Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"})
        # optional: user agent
        try:
            await page.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        except Exception:
            pass

        while not q.empty():
            try:
                b_id, query, city_for = await q.get()
            except asyncio.CancelledError:
                break
            try:
                search_q = quote_plus(query)
                url = f"https://2gis.ru/{quote_plus(city_for)}/search/{search_q}"
                log(f"[worker-{name}] processing id={b_id} query='{query}' -> {url}")
                # call processing
                found = await _process_one(page, url, timeout=20000, log=log)
                if found:
                    # write to DB in thread
                    await _run_db(_write_dgis_id_sync, db_path, b_id, found)
                    success += 1
                    log(f"[worker-{name}] FOUND id for b_id={b_id}: {found}")
                else:
                    # mark failed to avoid endless retries (optional)
                    await _run_db(_mark_failed_sync, db_path, b_id)
                    errors += 1
                    log(f"[worker-{name}] NOT found for b_id={b_id}")
            except Exception as e:
                errors += 1
                log(f"[worker-{name}] Exception for b_id={b_id}: {e}")
                try:
                    await _run_db(_mark_failed_sync, db_path, b_id)
                except Exception:
                    pass
            finally:
                processed += 1
                # progress
                if progress_callback:
                    pct = int(processed / max(1, total) * 100)
                    progress_callback(pct, f"Processed {processed}/{total}", {"total": total, "processed": processed, "success": success, "errors": errors})
                # polite delay
                # Убираем задержку при успешной обработке, оставляем только при ошибках
                if not found:
                    await asyncio.sleep(delay)
                q.task_done()
        try:
            await page.close()
        except Exception:
            pass

    # start playwright and workers
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless, args=["--no-sandbox"])
            # create worker tasks
            workers = min(concurrency, total) if concurrency > 0 else 1
            log(f"Playwright: launching {workers} worker(s) (headless={headless})")
            worker_coros = [worker_task(str(i+1), browser) for i in range(workers)]
            # run workers concurrently
            await asyncio.gather(*worker_coros)
            try:
                await browser.close()
            except Exception:
                pass
    except Exception as e:
        log(f"Playwright: fatal error: {e}")
        # if fatal, still return counts
        if progress_callback:
            progress_callback(100, f"Error: {e}", {"total": total, "processed": processed, "success": success, "errors": errors})
        return {"total": total, "processed": processed, "success": success, "errors": errors}

    # done
    if progress_callback:
        progress_callback(100, f"Done. success={success}, errors={errors}", {"total": total, "processed": processed, "success": success, "errors": errors})
    return {"total": total, "processed": processed, "success": success, "errors": errors}
