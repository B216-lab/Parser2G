#!/usr/bin/env python3
"""
run_update_dgis_ids.py

Упрощённый скрипт для запуска обновления отсутствующих dgis_id в базе данных.
"""
import subprocess
import sys
from pathlib import Path


def main():
    # Путь к основному скрипту
    script_path = Path("tools/update_missing_dgis_ids.py")
    
    if not script_path.exists():
        print(f"Ошибка: файл {script_path} не найден")
        sys.exit(1)
    
    # Передаём все аргументы командной строки в основной скрипт
    cmd = [sys.executable, str(script_path)] + sys.argv[1:]
    
    print("Запуск обновления отсутствующих dgis_id...")
    print(f"Команда: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, check=True)
        print("Обновление завершено успешно.")
    except subprocess.CalledProcessError as e:
        print(f"Ошибка при выполнении: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nВыполнение прервано пользователем.")
        sys.exit(1)


if __name__ == "__main__":
    main()