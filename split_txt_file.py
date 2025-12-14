def split_txt_file(input_file_path, lines_per_file=5000):
    """
    Разделяет текстовый файл на несколько файлов по заданному количеству строк.
    
    :param input_file_path: Путь к исходному текстовому файлу
    :param lines_per_file: Количество строк в каждом выходном файле (по умолчанию 5000)
    :return: Список путей к созданным файлам
    """
    with open(input_file_path, 'r', encoding='utf-8') as input_file:
        lines = input_file.readlines()
    
    output_files = []
    file_index = 0
    
    for i in range(0, len(lines), lines_per_file):
        output_file_path = f"{input_file_path}_part_{file_index + 1}.txt"
        output_files.append(output_file_path)
        
        with open(output_file_path, 'w', encoding='utf-8') as output_file:
            output_file.writelines(lines[i:i + lines_per_file])
        
        file_index += 1
    
    return output_files


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) != 2:
        print("Использование: python split_txt_file.py <путь_к_файлу>")
        sys.exit(1)
    
    input_file_path = sys.argv[1]
    created_files = split_txt_file(input_file_path)
    
    print(f"Файл {input_file_path} был разделен на {len(created_files)} частей:")
    for file_path in created_files:
        print(f"  - {file_path}")