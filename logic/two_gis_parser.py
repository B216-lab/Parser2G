# logic/two_gis_parser.py
import subprocess
import json
import os
from datetime import datetime
from urllib.parse import quote
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Callable

class TwoGisCliParser:
    """
    Обёртка вокруг CLI parser-2gis.

    Утилита 'parser-2gis' должна быть доступна в PATH.
    Этот класс сохраняет выходные JSON в output_dir и возвращает загруженный JSON.
    """
    def __init__(self, output_dir="results_2gis", limit_restart=300, logger=print):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.limit_restart = limit_restart
        self.restart_counter = 0
        self.logger = logger or (lambda *a, **k: None)
        self.stats = {
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": None,
            "total": 0,
            "success": 0,
            "errors": 0
        }

    def _outfile_path(self, city: str, query: str) -> str:
        # безопасное имя файла
        safe_city = quote(city, safe='')
        safe_q = quote(query, safe='')
        return str(self.output_dir / f"{safe_city}_{safe_q}.json")

    def run_cli(self, city: str, query: str, output_format: str = "json") -> List[Dict]:
        """
        Запускает parser-2gis CLI и возвращает десериализованный JSON.
        Возвращает [] при ошибках.
        """
        url = f"https://2gis.ru/{quote(city)}/search/{quote(query)}"
        outfile = self._outfile_path(city, query)

        cmd = [
            "parser-2gis",
            "-i", url,
            "-o", outfile,
            "-f", output_format
        ]
        self.logger(f"2GIS: запуск CLI для {url} -> {outfile}")
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            if Path(outfile).exists():
                with open(outfile, "r", encoding="utf-8") as f:
                    try:
                        data = json.load(f)
                    except Exception:
                        data = []
                self.stats["success"] += 1
                return data
            else:
                self.stats["errors"] += 1
                return []
        except Exception as e:
            self.logger(f"Ошибка 2GIS CLI для {query}: {e}")
            self.stats["errors"] += 1
            return []

    def run_many(self, city: str, queries: List[str], progress_callback: Callable = None, max_workers: int = 4) -> Dict[str, List]:
        """
        Параллельно запускает run_cli для списка запросов.
        Возвращает dict: query -> result (list).
        progress_callback(percent:int, message:str, counts:dict)
        """
        self.stats["total"] += len(queries)
        results = {}
        total = len(queries)
        done = 0

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            future_to_q = {ex.submit(self.run_cli, city, q): q for q in queries}
            for future in as_completed(future_to_q):
                q = future_to_q[future]
                try:
                    res = future.result()
                except Exception as e:
                    self.logger(f"Ошибка при run_many для {q}: {e}")
                    res = []
                results[q] = res
                done += 1
                if progress_callback:
                    percent = int(done / max(1, total) * 100)
                    progress_callback(percent, f"2GIS: обработано {done}/{total}", {"done": done, "total": total})
        self.stats["end_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return results

    def save_stats(self, filename="2gis_cli_stats.json"):
        self.stats["end_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.output_dir / filename, "w", encoding="utf-8") as f:
            json.dump(self.stats, f, indent=2, ensure_ascii=False)
