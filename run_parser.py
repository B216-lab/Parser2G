import os
import sys
import subprocess
import argparse
from pathlib import Path


def read_links_from_txt_files(txt_files):
    """Чтение ссылок из TXT-файлов"""
    links = []
    for txt_file in txt_files:
        with open(txt_file, 'r', encoding='utf-8') as f:
            for line in f:
                link = line.strip()
                if link:
                    links.append(link)
    return links


def main():
    parser = argparse.ArgumentParser(description='Запуск парсера 2GIS')
    parser.add_argument('-i', '--input', nargs='+', required=True, 
                        help='TXT-файлы с ссылками или сами ссылки')
    parser.add_argument('-o', '--output', required=True, 
                        help='Выходной CSV-файл')
    parser.add_argument('--dry-run', action='store_true',
                        help='Не запускать команду, только показать что будет выполнено')
    
    args = parser.parse_args()
    
    # Разделяем аргументы на файлы и прямые ссылки
    txt_files = []
    direct_links = []
    
    for item in args.input:
        if item.endswith('.txt'):
            txt_files.append(item)
        else:
            direct_links.append(item)
    
    # Считываем ссылки из TXT-файлов
    links_from_files = read_links_from_txt_files(txt_files)
    
    # Объединяем все ссылки
    all_links = links_from_files + direct_links
    
    if not all_links:
        print("Не найдено ни одной ссылки для парсинга")
        sys.exit(1)
    
    # Формируем команду, оборачивая все ссылки в двойные кавычки и путь к файлу в одинарные
    quoted_links = [f'"{link}"' for link in all_links]
    cmd = ['uv', 'run', 'parser-2gis', '-i'] + quoted_links + ['-o', f"'{args.output}'", '-f', 'csv']
    
    print(f"Команда: {' '.join(cmd)}")
    
    if args.dry_run:
        print("Режим просмотра команды - выполнение пропущено")
        return
    
    # Запускаем команду
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("Команда выполнена успешно")
        if result.stdout:
            print(f"Вывод: {result.stdout}")
        if result.stderr:
            print(f"Ошибки: {result.stderr}")
    except subprocess.CalledProcessError as e:
        print(f"Ошибка при выполнении команды: {e}")
        print(f"Вывод: {e.stdout}")
        print(f"Ошибки: {e.stderr}")
        sys.exit(1)


if __name__ == "__main__":
    main()