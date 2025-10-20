# utils/temp_db.py
import sqlite3
from pathlib import Path
from dataclasses import dataclass
from typing import List

@dataclass
class SessionInfo:
    id: str
    path: str
    ginfo_url: str = ""

def create_temp_db(path: str):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS cities(
            id INTEGER PRIMARY KEY,
            name TEXT,
            ginfo_url TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS districts(
            id INTEGER PRIMARY KEY,
            city_id INTEGER,
            name TEXT,
            url TEXT,
            FOREIGN KEY(city_id) REFERENCES cities(id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS streets(
            id INTEGER PRIMARY KEY,
            district_id INTEGER,
            name TEXT,
            url TEXT,
            buildings_json TEXT,
            FOREIGN KEY(district_id) REFERENCES districts(id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS buildings(
            id INTEGER PRIMARY KEY,
            street_id INTEGER,
            address TEXT,
            url TEXT,
            raw_json TEXT,
            FOREIGN KEY(street_id) REFERENCES streets(id)
        )
    """)
    conn.commit()
    conn.close()

def list_temp_db_sessions(temp_root: str or Path) -> List[SessionInfo]:
    p = Path(temp_root)
    sessions = []
    if not p.exists():
        return sessions
    for f in p.iterdir():
        if f.is_file() and f.suffix == ".db":
            ginfo = ""
            try:
                ginfo = get_city_ginfo_url_from_db(str(f))
            except Exception:
                ginfo = ""
            sessions.append(SessionInfo(id=f.stem, path=str(f), ginfo_url=ginfo))
    sessions.sort(key=lambda s: s.path, reverse=True)
    return sessions

def db_path_for_session(temp_root: str or Path, session_id: str) -> Path:
    return Path(temp_root) / f"{session_id}.db"

def get_city_ginfo_url_from_db(db_path: str) -> str:
    p = Path(db_path)
    if not p.exists():
        return ""
    try:
        conn = sqlite3.connect(str(p))
        cur = conn.cursor()
        cur.execute("SELECT ginfo_url FROM cities LIMIT 1")
        r = cur.fetchone()
        conn.close()
        if r and r[0]:
            return r[0]
    except Exception:
        return ""
    return ""
