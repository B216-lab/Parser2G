# main_interface.py
import os
import uuid
import threading
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
from logic.parsing_addresses import ParsingAddressesManager
from utils.temp_db import create_temp_db, list_temp_db_sessions, db_path_for_session, get_city_ginfo_url_from_db
from export_logic import export_db_to_csv

# tasks structure:
# tasks = { session_id: { 'districts': {status, progress, message, counts}, 'streets': {...} } }
tasks = {}
tasks_lock = threading.Lock()

def create_app():
    app = Flask(__name__)
    app.secret_key = os.getenv("FLASK_SECRET", "dev-secret")
    app.project_root = Path(__file__).resolve().parents[0]
    app.temp_root = app.project_root / "data" / "temp"
    app.temp_root.mkdir(parents=True, exist_ok=True)

    @app.route("/", methods=["GET"])
    def index():
        fallback_ginfo = request.args.get("ginfo_url", "").strip()
        sessions = list_temp_db_sessions(app.temp_root)
        for s in sessions:
            try:
                s.ginfo_url = get_city_ginfo_url_from_db(s.path) or ""
            except Exception:
                s.ginfo_url = ""
            if not s.ginfo_url and fallback_ginfo:
                s.ginfo_url = fallback_ginfo
        return render_template("index.html", sessions=sessions, fallback_ginfo=fallback_ginfo)

    @app.route("/start_session", methods=["POST"])
    def start_session():
        session_id = uuid.uuid4().hex
        db_path = db_path_for_session(app.temp_root, session_id)
        try:
            create_temp_db(str(db_path))
            flash(f"Создана сессия {session_id}", "success")
        except Exception as e:
            app.logger.exception("Ошибка создания сессии")
            flash(f"Ошибка при создании сессии: {e}", "danger")
        return redirect(url_for("index"))

    def normalize_ginfo_url(raw: str) -> str:
        if not raw:
            return ""
        raw = raw.strip()
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw.rstrip("/")
        return "https://" + raw.rstrip("/")

    # Синхронный (как раньше) парсинг районов (оставил для совместимости)
    @app.route("/parse_districts/<session_id>", methods=["POST"])
    def parse_districts(session_id):
        raw_ginfo = request.form.get("ginfo_url", "").strip()
        if not raw_ginfo:
            flash("Укажите ссылку на Ginfo (например irkutsk.ginfo.ru)", "danger")
            return redirect(url_for("index"))
        ginfo_url = normalize_ginfo_url(raw_ginfo)
        db_path = db_path_for_session(app.temp_root, session_id)
        if not db_path.exists():
            flash("Сессия не найдена.", "danger")
            return redirect(url_for("index"))
        logger = lambda msg: app.logger.info(f"[session {session_id}] {msg}")
        manager = ParsingAddressesManager(log=logger, base_url=ginfo_url)
        try:
            manager.parse_districts_to_db(db_path=str(db_path))
            flash("Парсинг районов завершён.", "success")
        except Exception as e:
            app.logger.exception("Ошибка при парсинге районов")
            flash(f"Ошибка при парсинге районов: {e}", "danger")
        return redirect(url_for("index", ginfo_url=raw_ginfo))

    # Фоновый запуск парсинга районов (отдельно)
    @app.route("/start_parse_districts_bg/<session_id>", methods=["POST"])
    def start_parse_districts_bg(session_id):
        raw_ginfo = request.form.get("ginfo_url", "").strip()
        db_path = db_path_for_session(app.temp_root, session_id)
        if not db_path.exists():
            return jsonify({"error": "session not found"}), 404

        with tasks_lock:
            sess = tasks.setdefault(session_id, {})
            if sess.get("districts", {}).get("status") == "running":
                return jsonify({"status": "already running"}), 400
            sess["districts"] = {"status": "queued", "progress": 0, "message": "Queued", "counts": {}}
            sess.setdefault("streets", {"status": "idle", "progress": 0, "message": "", "counts": {}})

        ginfo_url = normalize_ginfo_url(raw_ginfo) if raw_ginfo else get_city_ginfo_url_from_db(str(db_path)) or None

        def target(session_id_local, db_path_local, ginfo_local):
            try:
                logger = lambda msg: app.logger.info(f"[session {session_id_local}] {msg}")
                manager = ParsingAddressesManager(log=logger, base_url=ginfo_local)
                def progress_cb(percent, message=None, counts=None):
                    with tasks_lock:
                        tasks[session_id_local]["districts"]["progress"] = int(percent)
                        tasks[session_id_local]["districts"]["status"] = "running"
                        if message is not None:
                            tasks[session_id_local]["districts"]["message"] = message
                        if counts is not None:
                            tasks[session_id_local]["districts"]["counts"] = counts

                tasks[session_id_local]["districts"]["message"] = "Парсинг районов..."
                manager.parse_districts_to_db(db_path=str(db_path_local), progress_callback=progress_cb)
                with tasks_lock:
                    tasks[session_id_local]["districts"]["progress"] = 100
                    tasks[session_id_local]["districts"]["status"] = "finished"
                    tasks[session_id_local]["districts"]["message"] = "Районы спарсены"
            except Exception as e:
                app.logger.exception("Background districts error")
                with tasks_lock:
                    tasks[session_id_local]["districts"]["status"] = "error"
                    tasks[session_id_local]["districts"]["message"] = str(e)

        thread = threading.Thread(target=target, args=(session_id, db_path, ginfo_url), daemon=True)
        thread.start()
        return jsonify({"status": "started"}), 202

    # Фоновый запуск парсинга улиц (отдельно)
    @app.route("/start_parse_streets_bg/<session_id>", methods=["POST"])
    def start_parse_streets_bg(session_id):
        db_path = db_path_for_session(app.temp_root, session_id)
        if not db_path.exists():
            return jsonify({"error": "session not found"}), 404

        with tasks_lock:
            sess = tasks.setdefault(session_id, {})
            if sess.get("streets", {}).get("status") == "running":
                return jsonify({"status": "already running"}), 400
            sess["streets"] = {"status": "queued", "progress": 0, "message": "Queued", "counts": {}}
            sess.setdefault("districts", {"status": "idle", "progress": 0, "message": "", "counts": {}})

        # base_url берём из DB
        ginfo_url = get_city_ginfo_url_from_db(str(db_path)) or None

        def target(session_id_local, db_path_local, ginfo_local):
            try:
                logger = lambda msg: app.logger.info(f"[session {session_id_local}] {msg}")
                manager = ParsingAddressesManager(log=logger, base_url=ginfo_local)
                def progress_cb(percent, message=None, counts=None):
                    with tasks_lock:
                        tasks[session_id_local]["streets"]["progress"] = int(percent)
                        tasks[session_id_local]["streets"]["status"] = "running"
                        if message is not None:
                            tasks[session_id_local]["streets"]["message"] = message
                        if counts is not None:
                            tasks[session_id_local]["streets"]["counts"] = counts

                tasks[session_id_local]["streets"]["message"] = "Парсинг улиц..."
                manager.parse_all_streets_to_db(db_path=str(db_path_local), progress_callback=progress_cb, max_workers=6)
                with tasks_lock:
                    tasks[session_id_local]["streets"]["progress"] = 100
                    tasks[session_id_local]["streets"]["status"] = "finished"
                    tasks[session_id_local]["streets"]["message"] = "Улицы спарсены"
            except Exception as e:
                app.logger.exception("Background streets error")
                with tasks_lock:
                    tasks[session_id_local]["streets"]["status"] = "error"
                    tasks[session_id_local]["streets"]["message"] = str(e)

        thread = threading.Thread(target=target, args=(session_id, db_path, ginfo_url), daemon=True)
        thread.start()
        return jsonify({"status": "started"}), 202

    # Статус для конкретной задачи (districts|streets)
    @app.route("/task_status/<session_id>/<task_type>", methods=["GET"])
    def task_status(session_id, task_type):
        with tasks_lock:
            sess = tasks.get(session_id)
            if not sess:
                return jsonify({"status": "not_found"}), 404
            task = sess.get(task_type)
            if not task:
                return jsonify({"status": "not_found", "task": task_type}), 404
            return jsonify(task)

    @app.route("/parse_streets/<session_id>", methods=["POST"])
    def parse_streets(session_id):
        # sync fallback
        db_path = db_path_for_session(app.temp_root, session_id)
        if not db_path.exists():
            flash("Сессия не найдена.", "danger")
            return redirect(url_for("index"))
        logger = lambda msg: app.logger.info(f"[session {session_id}] {msg}")
        manager = ParsingAddressesManager(log=logger)
        try:
            manager.parse_all_streets_to_db(db_path=str(db_path))
            flash("Парсинг улиц и домов завершён.", "success")
        except Exception as e:
            app.logger.exception("Ошибка при парсинге улиц")
            flash(f"Ошибка при парсинге улиц: {e}", "danger")
        session_ginfo = get_city_ginfo_url_from_db(str(db_path)) or ""
        return redirect(url_for("index", ginfo_url=session_ginfo))

    @app.route("/export_csv/<session_id>", methods=["GET"])
    def export_csv(session_id):
        db_path = db_path_for_session(app.temp_root, session_id)
        if not db_path.exists():
            flash("Сессия не найдена.", "danger")
            return redirect(url_for("index"))
        try:
            csv_path = export_db_to_csv(db_path=str(db_path), out_dir=str(app.temp_root))
            return send_file(csv_path, as_attachment=True)
        except Exception as e:
            app.logger.exception("Ошибка при экспорте")
            flash(f"Ошибка при экспорте: {e}", "danger")
            return redirect(url_for("index"))

    @app.route("/delete_session/<session_id>", methods=["POST"])
    def delete_session(session_id):
        db_path = db_path_for_session(app.temp_root, session_id)
        try:
            if db_path.exists():
                db_path.unlink()
                flash("Сессия удалена.", "success")
            else:
                flash("Сессия не найдена.", "warning")
        except Exception as e:
            app.logger.exception("Ошибка удаления сессии")
            flash(f"Не удалось удалить сессию: {e}", "danger")
        return redirect(url_for("index"))

    return app
