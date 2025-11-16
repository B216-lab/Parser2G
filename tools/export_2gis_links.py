#!/usr/bin/env python3
"""
export_2gis_links.py

Пример:
    python tools/export_2gis_links.py --db data/temp/55c1282dd7394968868625cffce94546.db --out data/temp/links.txt --only-pending --unique

Опции:
  --db PATH         путь к sqlite базе (обязателен)
  --out PATH        куда записать файл (по умолчанию ./2gis_links.txt)
  --only-pending    брать только записи, у которых raw_json IS NULL OR raw_json = ''
  --unique          убрать дубликаты (оставить уникальные ссылки)
  --limit N         максимальное кол-во ссылок (0 = без лимита)
  --city CITY       приоритетно использовать это имя города вместо city из БД
"""

import argparse
import sqlite3
from pathlib import Path
from urllib.parse import quote_plus
import sys
import re

def guess_city_from_ginfo(ginfo_url: str):
    # Если ginfo_url типа https://irkutsk.ginfo.ru -> вернём 'irkutsk'
    if not ginfo_url:
        return None
    try:
        # достать хост
        if "://" in ginfo_url:
            host = ginfo_url.split("://",1)[1]
        else:
            host = ginfo_url
        host = host.split("/")[0]
        # взять первый сегмент до точки
        city = host.split(".")[0]
        # очистить от лишних символов
        city = re.sub(r'[^0-9A-Za-zА-Яа-я_\-]', '', city)
        return city or None
    except Exception:
        return None

def build_inside_url(city: str, dgis_id: str):
    # city может содержать пробелы или кириллицу — кодируем
    city_enc = quote_plus(city or "")
    # dgis_id обычно уже пригоден для URL (число или строка), но на всякий случай str()
    return f"https://2gis.ru/{city_enc}/inside/{dgis_id}"

def fetch_rows(db_path: Path, only_pending: bool):
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Попробуем выбрать записи и город
    # будем возвращать: (building_id, dgis_id, city_name, ginfo_url)
    # city_name извлекаем из cities.name (если есть), иначе ginfo_url для fallback
    query = """
    SELECT b.id, b.dgis_id, c.name, c.ginfo_url
    FROM buildings b
    LEFT JOIN streets s ON b.street_id = s.id
    LEFT JOIN districts d ON s.district_id = d.id
    LEFT JOIN cities c ON d.city_id = c.id
    WHERE b.dgis_id IS NOT NULL AND b.dgis_id != ''
    """
    if only_pending:
        query += " AND (b.raw_json IS NULL OR b.raw_json = '')"

    query += " ORDER BY c.name, d.name, s.name, b.id"
    cur.execute(query)
    rows = cur.fetchall()
    conn.close()
    return rows

def main():
    p = argparse.ArgumentParser(description="Export 2GIS inside links from session DB (one URL per line).")
    p.add_argument("--db", "-d", required=True, help="Path to sqlite DB file (session .db)")
    p.add_argument("--out", "-o", default="2gis_links.txt", help="Output txt file (one URL per line)")
    p.add_argument("--only-pending", action="store_true", help="Export only buildings without raw_json (pending processing)")
    p.add_argument("--unique", action="store_true", help="Keep unique links only")
    p.add_argument("--limit", type=int, default=0, help="Limit number of links (0 = no limit)")
    p.add_argument("--city", type=str, default=None, help="Force city name for all links (overrides DB city)")
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB file not found: {db_path}", file=sys.stderr)
        sys.exit(2)

    rows = fetch_rows(db_path, only_pending=args.only_pending)
    if not rows:
        print("No rows found (no dgis_id). Exiting.")
        sys.exit(0)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    urls = []
    for (b_id, dgis_id, city_name, ginfo_url) in rows:
        # if user forced city - use it
        if args.city:
            city_for = args.city
        else:
            city_for = city_name or guess_city_from_ginfo(ginfo_url) or "irkutsk"
        url = build_inside_url(city_for, dgis_id)
        urls.append(url)
        if args.limit and len(urls) >= args.limit:
            break

    if args.unique:
        seen = set()
        uniq = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                uniq.append(u)
        urls = uniq

    # Save
    with open(out_path, "w", encoding="utf-8") as f:
        for u in urls:
            f.write(u + "\n")

    print(f"Wrote {len(urls)} links to {out_path}")

if __name__ == "__main__":
    main()
