import json

def extract_labels_without_children(json_file_path):
    """
    Извлекает значения 'label' из JSON файла, у которых нет поля 'children' или поле 'children' пустое.
    В данном случае JSON имеет структуру, где ключи - это коды рубрик, а значения - объекты с полями.
    
    Args:
        json_file_path (str): Путь к JSON файлу с рубриками
    
    Returns:
        list: Список значений 'label' без детей
    """
    with open(json_file_path, 'r', encoding='utf-8') as file:
        data = json.load(file)
    
    labels_without_children = []
    
    # Проходим по всем элементам в JSON (ключи - это коды рубрик)
    for key, item in data.items():
        # Проверяем, есть ли у элемента поле 'children' и не пустое ли оно
        if 'children' not in item or not item['children']:
            # Если нет поля 'children' или оно пустое, добавляем 'label'
            if 'label' in item:
                labels_without_children.append(item['label'])
    
    return labels_without_children

def main():
    # Путь к файлу rubrics.json
    json_file_path = 'rubrics.json'
    
    try:
        labels = extract_labels_without_children(json_file_path)
        
        print(f"Найдено {len(labels)} рубрик без детей:")
        for label in labels:
            print(f"- {label}")
        
        # Сохраняем результат в файл
        with open('labels_without_children.txt', 'w', encoding='utf-8') as output_file:
            for label in labels:
                output_file.write(f"{label}\n")
        
        print(f"\nРезультат также сохранен в файл 'labels_without_children.txt'")
        
    except FileNotFoundError:
        print(f"Файл {json_file_path} не найден.")
    except json.JSONDecodeError:
        print(f"Файл {json_file_path} содержит некорректный JSON.")
    except Exception as e:
        print(f"Произошла ошибка: {e}")

if __name__ == "__main__":
    main()