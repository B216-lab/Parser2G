#!/usr/bin/env python3
"""
Скрипт для запуска playwright_extractor вне веб-интерфейса.
"""
import asyncio
import sys
from pathlib import Path

# Убедитесь, что путь к logic включен в sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from logic.playwright_extractor import extract_ids_to_db


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run playwright extractor standalone")
    parser.add_argument("--db", required=True, help="Path to the SQLite database file")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent workers (default: 5)")
    parser.add_argument("--delay", type=float, default=0.1, help="Delay between requests in seconds (default: 0.1)")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of records to process (default: 0 = all)")
    parser.add_argument("--city", type=str, default=None, help="Force city name for all links (overrides DB city)")

    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: Database file not found: {db_path}")
        sys.exit(1)

    print(f"Starting playwright extractor with DB: {db_path}")
    print(f"Parameters: headless={args.headless}, concurrency={args.concurrency}, delay={args.delay}, limit={args.limit}, city={args.city}")

    async def run():
        result = await extract_ids_to_db(
            db_path=str(db_path),
            headless=args.headless,
            concurrency=args.concurrency,
            delay=args.delay,
            limit=args.limit,
            log=print,
            city=args.city
        )
        print(f"Extraction completed. Result: {result}")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nExtraction was interrupted by user.")
        sys.exit(1)


if __name__ == "__main__":
    main()