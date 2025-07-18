import json
import logging
import traceback

from src.parsers.twogis.twogis_parser import TwoGisParser
from scripts.preprocessing import preprocess
from src.database.db_scripts.gets.get_addresses_db import get_addresses_by_district
from src.database.db_scripts.writes.write_build_orgs_db import save_data_to_db


# Настройка логгера
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("parser_log.txt", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

def load_addresses(district):
    return get_addresses_by_district(district)

def save_to_db(data):
    return save_data_to_db(data)

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

def save_json(data, filename):
    # print(data)
    if data is None:
        return
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_counters_to_json(counters, filename="parser_counters.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(counters, f, ensure_ascii=False, indent=2)


def get_build_orgs(log_func=None):
    counters = {
        'success_save': 0,
        'error_save': 0,
        'not_data_on_address': 0,
        'error_processing': 0
    }
    district_name = 'Октябрьский'
    start_address = 3721
    addresses = get_addresses_by_district(district_name)[start_address:]
    # addresses = [(4397, 'Улица Лермонтова, д. 83'), (1495, 'Улица Автомобильная, д. 1')]

    parser = TwoGisParser()
    total = len(addresses) + start_address
    for num, address in enumerate(addresses, start=start_address):
        logging.info(f"🔍 ({num}/{total}) Обработка адреса: {address[1]}")
        if log_func: log_func(f"🔍 ({num}/{total}) Обработка адреса: {address[1]}")

        try:
            build_raw, orgs_raw = parse_2gis(address[1], parser)
            output_data = preprocess(build_raw=build_raw, orgs_raw=orgs_raw)

            if output_data:
                output_data['build']['address_id'] = address[0]
                if save_to_db(output_data) is True:
                    counters['success_save'] += 1
                    logging.info(f"✅ Успешно сохранено для адреса: {address[1]}")
                    if log_func:log_func(f"✅ Успешно сохранено для адреса: {address[1]}")
                else:
                    counters['error_save'] += 1
                    logging.error(f"❌ Не сохранено для адреса: {address[1]}")
                    logging.error(traceback.format_exc())
                    if log_func: log_func(f"❌ Не сохранено для адреса: {address[1]}")
            else:
                counters['not_data_on_address'] += 1
                logging.warning(f"⚠️ Нет данных для адреса: {address[1]}")
                if log_func: log_func(f"⚠️ Нет данных для адреса: {address[1]}")

        except Exception as e:
            counters['error_processing'] += 1
            logging.error(f"❌ Ошибка при обработке адреса: {address[1]}")
            logging.error(traceback.format_exc())
        finally:
            save_counters_to_json(counters)

    logging.info("🎯 Обработка завершена")
    logging.info(f"Всего адресов: {total}")
    logging.info(f"Успешно: {counters}")

    logging.info(f"Статистика парсера: {parser.stats}")

if __name__ == "__main__":
    pass