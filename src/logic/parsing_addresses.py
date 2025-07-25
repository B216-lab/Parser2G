from parsers.ginfo.ginfo_parser import GinfoParser
import os
import json


class ParsingAddresses:
    def __init__(self, log):
        self.parser = GinfoParser(log=log)
        self.log = log
        
        self.districts = None
        self.streets_district = None
        self.addresses_street = None
        
        self.count_districts = 0
        self.count_streets = 0
        self.count_addresses = 0

    def parse_districts(self):
        self.districts = self.parser.get_districts()
        self.update_counters()
        
    def _parse_street_district(self, district_url):
        self.parser.get_streets(district_url)
        self.update_counters()
        
    def parse_streets(self):
        self.districts = self.load_temp('districts')
        for district in self.districts:
            self._parse_street_district(district_url=district['url'])
            
    def reset_data(self):
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            districts_path = os.path.join(base_dir, '../../../data/temp/ginfo/districts.json')
            streets_path = os.path.join(base_dir, '../../../data/temp/ginfo/streets.json')

            if os.path.exists(districts_path):
                with open(districts_path, 'w', encoding='utf-8') as f:
                    json.dump([], f, ensure_ascii=False, indent=4)
            if os.path.exists(streets_path):
                with open(streets_path, 'w', encoding='utf-8') as f:
                    json.dump([], f, ensure_ascii=False, indent=4)

            self.count_districts = 0
            self.count_streets = 0
            self.count_addresses = 0
        except Exception as e:
            self.log(f"Ошибка при сбросе данных: {e}")
        
            
    def update_counters(self):
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            districts_path = os.path.abspath(os.path.join(base_dir, '..../../data/temp/ginfo/districts.json'))
            streets_path = os.path.abspath(os.path.join(base_dir, '../../data/temp/ginfo/streets.json'))
            
            if os.path.exists(districts_path):        
                with open(districts_path, 'r', encoding='utf-8') as f:
                    data_districts = json.load(f)
                    self.count_districts = len(data_districts)

            if os.path.exists(streets_path):
                with open(streets_path, 'r', encoding='utf-8') as f:
                    data_streets = json.load(f)
                    self.count_streets = len(data_streets)
                    self.count_addresses = sum(len(street['buildings_list']) for street in data_streets)
                    
        except Exception as e:
            self.log(f"Ошибка при загрузке данных: {e}")
    
    def load_temp(self, name_file):
        """
        Загружает список данных из временного файла.
        """
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            temp_file = os.path.join(base_dir, f'../../data/temp/ginfo/{name_file}.json')
            if not os.path.exists(temp_file):
                self.log(f"Файл {temp_file} не найден.")
                return []
            
            with open(temp_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data
            
        except Exception as e:
            self.log(f"Ошибка при загрузке данных: {e}")
            return []