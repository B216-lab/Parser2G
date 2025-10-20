import sys
import os
import json
import concurrent.futures

# Добавляем корневую директорию в путь Python
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Теперь импортируем модули
from src.parsers.twogis import TwoGisParser
from src.parsers.minzhkh import MinzhkhParser
from scripts.preprocessing import preprocess

def load_addresses_from_file(filepath):
    """ Загрузка адресов из файла с адресами """
    try:
        with open(filepath, "r", encoding="utf-8") as file:
            out_addresses = file.read().splitlines()
            return out_addresses
    except FileNotFoundError as fe:
        print("❌ Ошибка, файл с адресами не найден!", fe)
        return []

def save_json(data, filename):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def parse_2gis(address, parser: TwoGisParser):
    build_data = None
    orgs_data = None

    parser.run(address)
    build_data_raw = parser.get_build()
    orgs_data_raw = parser.get_organizations()
    if build_data_raw is not None:
        build_data = json.loads(build_data_raw)
        if orgs_data_raw is not None:
            orgs_data = json.loads(orgs_data_raw)

    return build_data, orgs_data

def parse_minzhkh(address, parser: MinzhkhParser):
    parser.run(address)
    build_info = parser.get_build()
    return build_info

def extract_address_components(address):
    """Простая функция для извлечения компонентов адреса без Dadata"""
    components = {
        'value': address,
        'city': '',
        'street': '',
        'house': ''
    }
    
    parts = address.split(',')
    if len(parts) >= 1:
        components['city'] = parts[0].strip()
    if len(parts) >= 2:
        components['street'] = parts[1].strip()
    if len(parts) >= 3:
        components['house'] = parts[2].strip()
    
    return components

if __name__ == "__main__":
    # Используем относительные пути от корня проекта
    path_addresses = os.path.join("other", "test_addresses.txt")
    addresses = load_addresses_from_file(path_addresses)

    output_path = "data/ready"
    os.makedirs(output_path, exist_ok=True)

    parserMinzhkh = MinzhkhParser()
    parserTwogis = TwoGisParser()

    for num, address in enumerate(addresses, start=1):
        print(f"\n🔍 Обрабатываем: {address} ({num}/{len(addresses)})")
        
        address_data = extract_address_components(address)
        address_value = address_data['value']

        # Запускаем два парсера в отдельных потоках
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_2gis = executor.submit(parse_2gis, address_value, parserTwogis)
            future_minzhkh = executor.submit(parse_minzhkh, address, parserMinzhkh)

            build_raw, orgs_raw = future_2gis.result()
            minzhkh_raw = future_minzhkh.result()

        output_file = preprocess(address_raw=address_data, build_raw=build_raw, orgs_raw=orgs_raw, minzhkh_raw=minzhkh_raw)
        output_filename = os.path.join(output_path, f"file_{num}.json")
        save_json(output_file, output_filename)

    print(parserMinzhkh.stats)
    print(parserTwogis.stats)