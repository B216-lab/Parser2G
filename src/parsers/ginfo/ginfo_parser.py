import requests
from bs4 import BeautifulSoup
import json

headers = {
    "User-Agent": "Mozilla/5.0"
}
def get_districts(url):
    """
    Получение районов. Ищет div блок в котором есть тег <a> с url=/rayoni/<district>
    """
    try:
        response = requests.get(url, headers=headers, timeout=10,  verify=False)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        main_block = soup.find("div", class_="main_block")
        if not main_block:
            print('Не найден блок с районами')
            return

        district_links = []
        for a in main_block.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/rayoni/") and href != "/rayoni/":
                district_links.append(a)

        districts = []
        for a in district_links:
            name = a.text.strip()
            url = url + a["href"]
            districts.append({"name": name, "url": url})

        return districts

    except Exception as e:
        print(f"Ошибка при получении районов: {e}")
        return

import os

def run(base_rul="https://irkutsk.ginfo.ru/"):
    districts = get_districts(base_rul)
    save_to_temp('districts', districts)


def save_to_temp(name_file, data):
    """
    Сохраняет переданные данные в указанный JSON-файл во временной папке.
    """
    name_file = f'{name_file}.json'
    temp_dir = os.path.join(os.path.dirname(__file__), '../../data/temp/ginfo')
    os.makedirs(temp_dir, exist_ok=True)
    temp_file = os.path.join(temp_dir, name_file)
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print(f'Результаты сохранены в {temp_file}')
    except Exception as e:
        print(f'Ошибка при сохранении данных: {e}')

if __name__ == "__main__":
    print(run())