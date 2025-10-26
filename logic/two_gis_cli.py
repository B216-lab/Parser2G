# logic/two_gis_cli.py
import subprocess
import json
import os
import time
import hashlib
from urllib.parse import quote_plus
from pathlib import Path
from typing import Optional, Any
import shutil

class TwoGisCliParser:
    def __init__(self, output_dir: str = None, parser_cmd: str = "parser-2gis",
                 max_retries: int = 2, retry_backoff: float = 1.0, logger=print):
        self.output_dir = Path(output_dir or os.path.join(os.getcwd(), "data", "temp", "2gis_results"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.parser_cmd = parser_cmd
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.logger = logger

        # если указали абсолютный путь — используем его, иначе ищем в PATH
        if Path(self.parser_cmd).exists():
            self._binary_path = str(Path(self.parser_cmd).resolve())
        else:
            self._binary_path = shutil.which(self.parser_cmd)

        if not self._binary_path:
            self.logger(f"TwoGisCliParser: бинарь '{self.parser_cmd}' не найден.")
        else:
            self.logger(f"TwoGisCliParser: найден бинарь '{self._binary_path}'")

    def _make_outfile(self, city: str, query: str, ext: str):
        # создаём безопасное имя и возвращаем абсолютный путь
        q = f"{city} {query}"
        h = hashlib.sha1(q.encode("utf-8")).hexdigest()[:12]
        safe = quote_plus(query)[:60]
        fname = f"{city}_{h}_{safe}.{ext}"
        return str((self.output_dir / fname).resolve())

    def _run_subprocess(self, exe_path: str, args: list, timeout: Optional[int], exe_cwd: Optional[str]):
        """
        Запускает subprocess с exe_path и args (список). Возвращает dict с полями ok, returncode, stdout, stderr.
        """
        cmd = [exe_path] + args
        try:
            self.logger(f"Executing: {cmd}")
            if exe_cwd:
                self.logger(f"cwd: {exe_cwd}")
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout, cwd=exe_cwd)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            self.logger(f"Returncode={proc.returncode}, stderr_len={len(stderr)}")
            if stderr:
                self.logger(f"stderr (head): {stderr.strip()[:1000]}")
            return {"ok": proc.returncode == 0, "stdout": stdout, "stderr": stderr, "returncode": proc.returncode}
        except subprocess.TimeoutExpired as e:
            return {"ok": False, "stdout": "", "stderr": f"TimeoutExpired: {e}", "returncode": -1}
        except Exception as e:
            return {"ok": False, "stdout": "", "stderr": str(e), "returncode": -1}

    def run_cli(self, city: str, query: str, out_filename: Optional[str] = None,
                output_format: str = "json", timeout: int = 120) -> Any:
        """
        Безопасно запускает parser-2gis:
         - формирует url = https://2gis.ru/{city}/search/{query}
         - формирует абсолютный outfile (если out_filename не указан — автогенерация)
         - запускает команду: <exe> -i <url> -o <outfile> -f <output_format>
        Возвращает десериализованный JSON (или путь к csv, или None при ошибке).
        """
        if not self._binary_path:
            self.logger("parser-2gis не найден")
            return None

        url = f"https://2gis.ru/{quote_plus(city)}/search/{quote_plus(query)}"
        ext = "json" if output_format.lower() == "json" else "csv"
        outfile = out_filename if out_filename else self._make_outfile(city, query, ext)
        # убедимся, что путь абсолютный
        outfile = str(Path(outfile).resolve())
        # определим exe cwd — папка с бинарём
        exe_cwd = str(Path(self._binary_path).parent)

        args = ["-i", url, "-o", outfile, "-f", output_format]

        res = self._run_subprocess(self._binary_path, args, timeout=timeout, exe_cwd=exe_cwd)
        if not res["ok"]:
            self.logger(f"CLI returned code {res['returncode']}. stderr: {res['stderr']}")
            return None

        # при csv — просто возвращаем путь к файлу
        if output_format.lower() == "csv":
            return outfile

        # при json — попробуем загрузить
        try:
            with open(outfile, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger(f"Не удалось прочитать JSON {outfile}: {e}")
            return None

    def run_cli_shell(self, city: str, query: str, out_filename: Optional[str] = None,
                      output_format: str = "json", timeout: int = 120) -> Any:
        """
        Альтернатива: строит строку и запускает через shell=True.
        НЕ рекомендуется при параметрах из ненадёжных источников (возможность инъекции).
        """
        if not self._binary_path:
            self.logger("parser-2gis не найден")
            return None

        url = f"https://2gis.ru/{quote_plus(city)}/search/{quote_plus(query)}"
        ext = "json" if output_format.lower() == "json" else "csv"
        outfile = out_filename if out_filename else self._make_outfile(city, query, ext)
        outfile = str(Path(outfile).resolve())
        # формируем строку с корректным quoting для Windows (используем двойные кавычки)
        cmd_str = f'"{self._binary_path}" -i "{url}" -o "{outfile}" -f {output_format}'
        self.logger(f"Executing shell: {cmd_str}")
        try:
            proc = subprocess.run(cmd_str, shell=True, capture_output=True, text=True, timeout=timeout, cwd=str(Path(self._binary_path).parent))
            if proc.returncode != 0:
                self.logger(f"shell returned {proc.returncode}, stderr: {proc.stderr}")
                return None
            if output_format.lower() == "csv":
                return outfile
            with open(outfile, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger(f"Shell-run error: {e}")
            return None
