# main_interface.py
import os
import uuid
import threading
import time
import json
import sqlite3
from pathlib import Path
from typing import Dict, Any, Callable, Optional

from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, send_file

# Логика (убедитесь, что эти модули есть в проекте)
from logic.parsing_addresses import ParsingAddressesManager
from logic.two_gis_cli import TwoGisCliParser
from logic.playwright_extractor import run_extract_ids

# --- Конфигурация ---
# Поменяйте при необходимости на ваш реальный путь (например "G:/Rab Stol/Parser2GISNew/data/temp")
TEMP_ROOT = Path("data") / "temp"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = "dev-please-change"  # change for production

# tasks structure:
# tasks[session_id][taskKey] = {"status": "queued|running|finished|error", "progress": int, "message": str, "counts": dict, "result": {...}}
tasks: Dict[str, Dict[str, Dict[str, Any]]] = {}
tasks_lock = threading.Lock()


# ----------------- Database helpers -----------------
def make_session_db_path(session_id: str) -> Path:
    return TEMP_ROOT / f"{session_id}.db"


def ensure_temp_db(session_id: str) -> Path:
    """Создать временную DB с минимальной схемой, если нет."""
    db_path = make_session_db_path(session_id)
    if db_path.exists():
        return db_path

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS cities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            ginfo_url TEXT
        );
        CREATE TABLE IF NOT EXISTS districts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city_id INTEGER REFERENCES cities(id) ON DELETE CASCADE,
            name TEXT,
            url TEXT
        );
        CREATE TABLE IF NOT EXISTS streets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            district_id INTEGER REFERENCES districts(id) ON DELETE CASCADE,
            name TEXT,
            url TEXT,
            buildings_json TEXT
        );
        CREATE TABLE IF NOT EXISTS buildings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            street_id INTEGER REFERENCES streets(id) ON DELETE CASCADE,
            address TEXT,
            url TEXT,
            raw_json TEXT,
            dgis_id TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    return db_path


def list_sessions() -> list:
    """Собираем список существующих .db файлов в TEMP_ROOT и читаем мета (ginfo_url если есть)"""
    out = []
    for p in sorted(TEMP_ROOT.glob("*.db")):
        sid = p.stem
        ginfo = None
        try:
            conn = sqlite3.connect(str(p))
            cur = conn.cursor()
            cur.execute("SELECT ginfo_url FROM cities LIMIT 1")
            r = cur.fetchone()
            if r and r[0]:
                ginfo = r[0]
            conn.close()
        except Exception:
            ginfo = None
        out.append({"id": sid, "path": str(p), "ginfo_url": ginfo})
    return out


# ----------------- Task helpers -----------------
def update_task(session_id: str, task_key: str, *, status: Optional[str] = None,
                progress: Optional[int] = None, message: Optional[str] = None, counts: Optional[dict] = None, result: Optional[dict] = None):
    with tasks_lock:
        if session_id not in tasks:
            tasks[session_id] = {}
        rec = tasks[session_id].get(task_key, {})
        if status is not None:
            rec["status"] = status
        if progress is not None:
            rec["progress"] = int(progress)
            rec["percent"] = int(progress)  # backward compatibility
        if message is not None:
            rec["message"] = str(message)
        if counts is not None:
            rec["counts"] = counts
        if result is not None:
            rec["result"] = result
        tasks[session_id][task_key] = rec


def _start_background_task(session_id: str, task_key: str, target_fn: Callable[..., Any], *args, **kwargs) -> (bool, str):
    """
    Generic runner: запускает target_fn в фоне. target_fn должен принимать progress_callback kwarg.
    """
    with tasks_lock:
        if session_id not in tasks:
            tasks[session_id] = {}
        existing = tasks[session_id].get(task_key)
        if existing and existing.get("status") == "running":
            return False, "already running"
        # set queued
        tasks[session_id][task_key] = {"status": "queued", "progress": 0, "message": "Queued", "counts": {}}

    def runner():
        update_task(session_id, task_key, status="running", progress=0, message="Started", counts={})
        try:
            def progress_cb(pct: int, msg: str, counts_dict: dict):
                # normalize pct to 0..100
                try:
                    pp = int(pct)
                    if pp < 0: pp = 0
                    if pp > 100: pp = 100
                except Exception:
                    pp = 0
                update_task(session_id, task_key, progress=pp, message=msg, counts=counts_dict or {})
            # call target (target should accept progress_callback=progress_cb)
            target_fn(*args, progress_callback=progress_cb, **kwargs)
            update_task(session_id, task_key, status="finished", progress=100, message="Finished")
        except Exception as e:
            update_task(session_id, task_key, status="error", message=str(e))
    th = threading.Thread(target=runner, daemon=True)
    th.start()
    return True, "started"


# ----------------- Flask routes -----------------
@app.route("/", methods=["GET"])
def index():
    sessions = list_sessions()
    fallback_ginfo = request.args.get("ginfo_url", "")
    return render_template("index.html", sessions=sessions, fallback_ginfo=fallback_ginfo)


@app.route("/start_session", methods=["POST"])
def start_session():
    session_id = uuid.uuid4().hex
    ensure_temp_db(session_id)
    flash(f"Session created: {session_id}", "success")
    return redirect(url_for("index"))


@app.route("/delete_session/<session_id>", methods=["POST"])
def delete_session(session_id):
    dbp = make_session_db_path(session_id)
    if dbp.exists():
        try:
            dbp.unlink()
            flash(f"Session {session_id} removed", "success")
        except Exception as e:
            flash(f"Can't remove session: {e}", "danger")
    else:
        flash("Session not found", "warning")
    return redirect(url_for("index"))


@app.route("/export_csv/<session_id>", methods=["GET"])
def export_csv(session_id):
    # very simple export: dump buildings table to CSV
    dbp = make_session_db_path(session_id)
    if not dbp.exists():
        flash("DB not found", "danger")
        return redirect(url_for("index"))
    out_csv = TEMP_ROOT / f"{session_id}_buildings.csv"
    try:
        conn = sqlite3.connect(str(dbp))
        cur = conn.cursor()
        cur.execute("SELECT id, address, raw_json, dgis_id FROM buildings")
        rows = cur.fetchall()
        import csv
        with open(out_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "address", "raw_json_head", "dgis_id"])
            for r in rows:
                raw = r[2]
                head = raw[:200] if raw else ""
                w.writerow([r[0], r[1], head, r[3]])
        conn.close()
        return send_file(str(out_csv), as_attachment=True, download_name=out_csv.name)
    except Exception as e:
        flash(f"Export failed: {e}", "danger")
        return redirect(url_for("index"))


# ---------- Sync parsing endpoints (used by index.html forms) ----------
@app.route("/parse_districts/<session_id>", methods=["POST"])
def parse_districts(session_id):
    dbp = ensure_temp_db(session_id)
    ginfo_url = request.form.get("ginfo_url") or None
    try:
        manager = ParsingAddressesManager(log=app.logger.info, base_url=ginfo_url)
        # synchronous blocking call
        manager.parse_districts_to_db(str(dbp))
        flash("Районы спарсены (синхронно)", "success")
    except Exception as e:
        app.logger.exception("parse_districts error")
        flash(f"Ошибка парсинга районов: {e}", "danger")
    return redirect(url_for("index"))


@app.route("/parse_streets/<session_id>", methods=["POST"])
def parse_streets(session_id):
    dbp = ensure_temp_db(session_id)
    try:
        manager = ParsingAddressesManager(log=app.logger.info)
        manager.parse_all_streets_to_db(str(dbp))
        flash("Улицы спарсены (синхронно)", "success")
    except Exception as e:
        app.logger.exception("parse_streets error")
        flash(f"Ошибка парсинга улиц: {e}", "danger")
    return redirect(url_for("index"))


# ---------- Background endpoints (used by buttons / JS polling) ----------
@app.route("/start_parse_districts_bg/<session_id>", methods=["POST"])
def start_parse_districts_bg(session_id):
    dbp = ensure_temp_db(session_id)
    ginfo_url = request.form.get("ginfo_url") or request.form.get("ginfo") or None

    def target(progress_callback=None):
        mgr = ParsingAddressesManager(log=app.logger.info, base_url=ginfo_url)
        mgr.parse_districts_to_db(str(dbp), progress_callback=progress_callback)

    ok, msg = _start_background_task(session_id, "districts", target)
    if not ok:
        return jsonify({"error": msg}), 409
    return jsonify({"status": "started"}), 202


@app.route("/start_parse_streets_bg/<session_id>", methods=["POST"])
def start_parse_streets_bg(session_id):
    dbp = ensure_temp_db(session_id)

    def target(progress_callback=None):
        mgr = ParsingAddressesManager(log=app.logger.info)
        mgr.parse_all_streets_to_db(str(dbp), progress_callback=progress_callback)

    ok, msg = _start_background_task(session_id, "streets", target)
    if not ok:
        return jsonify({"error": msg}), 409
    return jsonify({"status": "started"}), 202


@app.route("/start_parse_2gis_bg/<session_id>", methods=["POST"])
def start_parse_2gis_bg(session_id):
    dbp = ensure_temp_db(session_id)
    parser_cmd = request.form.get("parser_cmd") or request.form.get("parser_bin") or "parser-2gis"

    def target(progress_callback=None):
        mgr = ParsingAddressesManager(log=app.logger.info)
        if hasattr(mgr, "parse_buildings_with_2gis"):
            # try to call with signature we expect
            try:
                mgr.parse_buildings_with_2gis(str(dbp), parser_cmd=parser_cmd, max_workers=2, progress_callback=progress_callback)
            except TypeError:
                # fallback to simpler signature
                mgr.parse_buildings_with_2gis(str(dbp), progress_callback=progress_callback)
        else:
            # fallback: try TwoGisCliParser on sample rows (simple)
            raise RuntimeError("Manager has no parse_buildings_with_2gis method")

    ok, msg = _start_background_task(session_id, "2gis", target)
    if not ok:
        return jsonify({"error": msg}), 409
    return jsonify({"status": "started"}), 202


@app.route("/start_extract_ids_bg/<session_id>", methods=["POST"])
def start_extract_ids_bg(session_id):
    dbp = ensure_temp_db(session_id)
    city = request.form.get("city") or None
    headless = request.form.get("headless", "false").lower() in ("1", "true", "yes")
    try:
        delay = float(request.form.get("delay", 0.6))
    except Exception:
        delay = 0.6
    try:
        limit = int(request.form.get("limit", 0))
    except Exception:
        limit = 0
    try:
        timeout_ms = int(request.form.get("timeout_ms", 12000))
    except Exception:
        timeout_ms = 12000

    screenshots_dir = str(Path(TEMP_ROOT) / "playwright_screens")

    def target(progress_callback=None):
        run_extract_ids(
            str(dbp),
            city_override=city,
            headless=headless,
            delay=delay,
            limit=limit,
            timeout_ms=timeout_ms,
            screenshots_dir=screenshots_dir,
            progress_callback=progress_callback
        )

    ok, msg = _start_background_task(session_id, "extract_ids", target)
    if not ok:
        return jsonify({"error": msg}), 409
    return jsonify({"status": "started"}), 202


# ---------- Task status ----------
@app.route("/task_status/<session_id>/<task_key>", methods=["GET"])
def task_status(session_id, task_key):
    with tasks_lock:
        st = tasks.get(session_id, {}).get(task_key)
        if not st:
            return jsonify({"status": "idle", "progress": 0, "message": "", "counts": {}}), 200
        # ensure keys presence
        resp = {
            "status": st.get("status", "idle"),
            "progress": st.get("progress", st.get("percent", 0)),
            "message": st.get("message", ""),
            "counts": st.get("counts", {}),
        }
        return jsonify(resp), 200


# ---------- Misc ----------
@app.route("/download_db/<session_id>", methods=["GET"])
def download_db(session_id):
    dbp = make_session_db_path(session_id)
    if not dbp.exists():
        return jsonify({"error": "db not found"}), 404
    return send_file(str(dbp), as_attachment=True, download_name=f"{session_id}.db")


# ---------- Run ----------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
