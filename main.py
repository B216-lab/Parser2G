# main.py
from main_interface import app

if __name__ == "__main__":
    # запускаем встроенным сервером flask (для разработки).
    # В продакшене — ставьте gunicorn/uvicorn и т.д.
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
