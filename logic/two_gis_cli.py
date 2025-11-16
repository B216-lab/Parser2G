# logic/two_gis_cli.py
import subprocess
import json
import os
import time
import hashlib
from urllib.parse import quote_plus
from pathlib import Path
from typing import Optional, Any, List, Dict
import shutil


class TwoGisCliParser:
    """
    Обёртка для CLI parser-2gis.

    - run_cli_once(...) — запуск для одного URL (оставлен для совместимости)
    - run_batch(city, dgis_ids, ...) — запускает parser-2gis один раз для списка inside-URL'ов.
    """
    def __init__(self, output_dir: str = None, parser_cmd: str = "parser-2gis",
                 max_retries: int = 2, retry_backoff: float = 1.0, logger=print):
        self.output_dir = Path(output_dir or os.path.join(os.getcwd(), "data", "temp", "2gis_results"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.parser_cmd = parser_cmd
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.logger = logger

        # проверим доступность бинаря
        self._binary_path = shutil.which(self.parser_cmd)
        if not self._binary_path:
            self.logger(f"TwoGisCliParser: не найден бинарь '{self.parser_cmd}' в PATH. Попробуйте указать полный путь.")
        else:
            self.logger(f"TwoGisCliParser: найден бинарь '{self._binary_path}'")

    def _make_outfile(self, city: str, suffix: str = None, ext: str = "json") -> str:
        # формируем короткое безопасное имя файла: <city>_<sha1>.json
        stamp = hashlib.sha1(f"{city}:{time.time()}".encode("utf-8")).hexdigest()[:12]
        if suffix:
            safe = "".join([c for c in suffix if c.isalnum() or c in "-_"])[:60]
            fname = f"{city}_{safe}_{stamp}.{ext}"
        else:
            fname = f"{city}_{stamp}.{ext}"
        outfile = self.output_dir / fname
        return str(outfile)

    def run_cli_once(self, url: str, outfile: str, output_format: str = "json", timeout: Optional[int] = None) -> dict:
        """
        Один запуск CLI для одного URL. Возвращает dict с ключами: ok, outfile, stdout, stderr, returncode
        """
        cmd = [self._binary_path or self.parser_cmd, "-i", url, "-o", outfile, "-f", output_format]
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            ok = proc.returncode == 0
            return {"ok": ok, "outfile": outfile, "stdout": stdout, "stderr": stderr, "returncode": proc.returncode}
        except subprocess.TimeoutExpired as e:
            return {"ok": False, "outfile": outfile, "stdout": "", "stderr": f"TimeoutExpired: {e}", "returncode": -1}
        except Exception as e:
            return {"ok": False, "outfile": outfile, "stdout": "", "stderr": str(e), "returncode": -1}

    def run_batch(self, city: str, dgis_ids: List[str], output_format: str = "json",
                  outfile: Optional[str] = None, timeout: int = 600) -> Dict[str, Any]:
        """
        Запускает parser-2gis один раз для набора inside-URL'ов:
          url = https://2gis.ru/<city>/inside/<dgis_id>
        Возвращает словарь:
          {"ok": bool, "outfile": path, "data": parsed_json_or_list_or_none, "stderr": str, "returncode": int}

        Если outfile не указан — создаётся автоматически в output_dir.
        """
        if not dgis_ids:
            return {"ok": True, "outfile": None, "data": [], "stderr": "", "returncode": 0}

        if not self._binary_path:
            msg = f"Ошибка: бинарь parser-2gis ('{self.parser_cmd}') не найден."
            self.logger(msg)
            return {"ok": False, "outfile": outfile, "data": None, "stderr": msg, "returncode": -1}

        # формируем URL'ы
        city_part = city or ""
        # не quote_plus для id, но city может требовать кодирования
        city_encoded = quote_plus(city_part)
        urls = [f"https://2gis.ru/{city_encoded}/inside/{dgid}" for dgid in dgis_ids]

        if not outfile:
            suffix = "_".join(dgis_ids[:3])
            outfile = self._make_outfile(city_part, suffix=suffix, ext=output_format if output_format != "csv" else "csv")

        # команда: parser-2gis -i <url1> <url2> ... -o <outfile> -f <format>
        cmd = [self._binary_path or self.parser_cmd, "-i"] + urls + ["-o", outfile, "-f", output_format]
        attempt = 0
        last_err = None
        while attempt <= self.max_retries:
            attempt += 1
            self.logger(f"2GIS (batch): запуск CLI для {len(urls)} URL'ов -> {outfile} (attempt {attempt}/{self.max_retries+1})")
            try:
                proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
                stdout = proc.stdout or ""
                stderr = proc.stderr or ""
                if proc.returncode == 0:
                    # попробуем прочитать JSON (если формат json)
                    data = None
                    if output_format == "json":
                        try:
                            with open(outfile, "r", encoding="utf-8") as f:
                                data = json.load(f)
                        except Exception as e:
                            self.logger(f"2GIS (batch): не удалось прочитать JSON {outfile}: {e}")
                            data = None
                    return {"ok": True, "outfile": outfile, "data": data, "stderr": stderr, "stdout": stdout, "returncode": proc.returncode}
                else:
                    last_err = f"CLI returned {proc.returncode}. stderr: {stderr.strip()}"
                    self.logger(f"2GIS (batch) error: {last_err}")
            except subprocess.TimeoutExpired as e:
                last_err = f"TimeoutExpired: {e}"
                self.logger(f"2GIS (batch) timeout: {last_err}")
            except Exception as e:
                last_err = str(e)
                self.logger(f"2GIS (batch) exception: {last_err}")

            # retry backoff
            if attempt <= self.max_retries:
                sleep_time = self.retry_backoff * (2 ** (attempt - 1))
                self.logger(f"2GIS (batch): retry after {sleep_time:.1f}s")
                time.sleep(sleep_time)

        return {"ok": False, "outfile": outfile, "data": None, "stderr": last_err or "Unknown", "returncode": -1}

    def run(self, city: str, address: str, **kwargs):
        """
        Совместимость с старым API: run возвращает результат (или []/None).
        По умолчанию выполняет run_batch для одиночного адреса.
        """
        return self.run_batch(city, [address], **kwargs)

    def save_stats(self):
        pass
