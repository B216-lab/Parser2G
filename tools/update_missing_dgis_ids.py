#!/usr/bin/env python3
"""
tools/update_missing_dgis_ids.py

Мини-программа для запуска алгоритма парсинга ID для 2gis 
для тех записей в БД у которых она отсутствует.

Использует асинхронный extractor из logic/playwright_extractor.py
"""
import asyncio
import sys
import argparse
from pathlib import Path


def main():
    # Добавляем путь к logic в sys.path для импорта
    sys.path.insert(0, str(Path(__file__).parent.parent))
    
    from logic.playwright_extractor import extract_ids_to_db

    parser = argparse.ArgumentParser(
        description="Обновление отсутствующих dgis_id в базе данных"
    )
    parser.add_argument(
        "--db", 
        required=True, 
        help="Путь к SQLite базе данных"
    )
    parser.add_argument(
        "--headless", 
        action="store_true", 
        help="Запуск браузера в фоновом режиме"
    )
    parser.add_argument(
        "--concurrency", 
        type=int, 
        default=5, 
        help="Количество одновременных задач (по умолчанию: 5)"
    )
    parser.add_argument(
        "--delay", 
        type=float, 
        default=0.5, 
        help="Задержка между запросами в секундах (по умолчанию: 0.5)"
    )
    parser.add_argument(
        "--limit", 
        type=int, 
        default=0, 
        help="Ограничение количества обрабатываемых записей (0 = все)"
    )
    parser.add_argument(
        "--city", 
        type=str, 
        default=None, 
        help="Название города для поиска (переопределяет город из базы)"
    )

    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Ошибка: файл базы данных не найден: {db_path}")
        sys.exit(1)

    print(f"Запуск обновления отсутствующих dgis_id")
    print(f"База данных: {db_path}")
    print(f"Параметры: headless={args.headless}, concurrency={args.concurrency}, delay={args.delay}, limit={args.limit}, city={args.city}")

    async def run_extraction():
        result = await extract_ids_to_db(
            db_path=str(db_path),
            headless=args.headless,
            concurrency=args.concurrency,
            delay=args.delay,
            limit=args.limit,
            log=print,
            city=args.city
        )
        print(f"Обработка завершена. Результат: {result}")
        return result

    try:
        result = asyncio.run(run_extraction())
        print(f"Финальный результат: {result}")
    except KeyboardInterrupt:
        print("\nОбработка была прервана пользователем.")
        sys.exit(1)
    except Exception as e:
        print(f"Ошибка во время обработки: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()