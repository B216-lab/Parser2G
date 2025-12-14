def remove_duplicate_lines(file1_path, file2_path, output_path):
    """
    Сравнивает 2 txt файла и убирает строки в первом файле которые есть во втором
    
    Args:
        file1_path (str): Путь к первому файлу (из которого удаляются строки)
        file2_path (str): Путь ко второму файлу (содержит строки для сравнения)
        output_path (str): Путь к выходному файлу (результат)
    """
    # Читаем строки из второго файла и сохраняем их в множестве для быстрого поиска
    with open(file2_path, 'r', encoding='utf-8') as f2:
        lines_to_remove = set(line.strip() for line in f2)
    
    # Читаем строки из первого файла и оставляем только те, которых нет во втором
    with open(file1_path, 'r', encoding='utf-8') as f1:
        lines_to_keep = [line for line in f1 if line.strip() not in lines_to_remove]
    
    # Записываем результат в выходной файл
    with open(output_path, 'w', encoding='utf-8') as output_file:
        output_file.writelines(lines_to_keep)
    
    print(f"Обработка завершена. Результат сохранен в {output_path}")
    print(f"Удалено {len(lines_to_remove)} строк, осталось {len(lines_to_keep)} строк.")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) != 4:
        print("Использование: python remove_duplicate_lines.py <файл1> <файл2> <выходной_файл>")
        print("Пример: python remove_duplicate_lines.py file1.txt file2.txt result.txt")
        sys.exit(1)
    
    file1 = sys.argv[1]
    file2 = sys.argv[2]
    output = sys.argv[3]
    
    remove_duplicate_lines(file1, file2, output)