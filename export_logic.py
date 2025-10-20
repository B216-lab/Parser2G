# export_logic.py
import csv
import sqlite3
from pathlib import Path
import datetime

def export_db_to_csv(db_path: str, out_dir: str):
    db_p = Path(db_path)
    out_p = Path(out_dir)
    out_p.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_name = f"export_{db_p.stem}_{timestamp}.csv"
    csv_path = out_p / csv_name

    conn = sqlite3.connect(str(db_p))
    cur = conn.cursor()

    query = """
        SELECT c.name as city, d.name as district, s.name as street, s.buildings_json, b.address
        FROM cities c
        LEFT JOIN districts d ON d.city_id = c.id
        LEFT JOIN streets s ON s.district_id = d.id
        LEFT JOIN buildings b ON b.street_id = s.id
        ORDER BY c.name, d.name, s.name
    """
    cur.execute(query)
    rows = cur.fetchall()
    headers = ["city", "district", "street", "buildings_json", "building_address"]

    with open(csv_path, "w", encoding="utf-8", newline='') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for r in rows:
            writer.writerow(r)
    conn.close()
    return str(csv_path)
