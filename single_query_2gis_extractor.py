#!/usr/bin/env python3
"""
single_query_2gis_extractor.py

Программа для одиночных запросов к 2GIS.
Например, при поиске "ТЦ" программа переходит по ссылке вида:
https://2gis.ru/irkutsk/search/ТЦ
Затем заходит в первое здание из результатов, нажимает "в здании",
находит первый элемент в списке и извлекает ID из URL после "inside/".
Сохраняет полученные ссылки в формате: https://2gis.ru/irkutsk/inside/[ID]

Пример использования:
    python single_query_2gis_extractor.py --query "ТЦ" --city irkutsk --out links.txt
"""

import argparse
import re
from pathlib import Path
import urllib.parse
from typing import List, Optional
import time

from parser_2gis.chrome.browser import ChromeBrowser
from parser_2gis.chrome.remote import ChromeRemote
from parser_2gis.chrome.options import ChromeOptions
from parser_2gis.parser.options import ParserOptions
from parser_2gis.common import wait_until_finished
from parser_2gis.logger import logger


def extract_inside_id(url: str) -> Optional[str]:
    """
    Извлекает ID из URL после 'inside/' и до '/firm' или из других форматов URL.
    
    Args:
        url: URL вида https://2gis.ru/irkutsk/inside/1548748027185762/firm/...
        
    Returns:
        ID после 'inside/' или None если не найдено
    """
    # Ищем стандартный формат: /inside/ID/firm/
    match = re.search(r'/inside/([^/]+)/firm/', url)
    if match:
        return match.group(1)
    
    # Ищем формат: /firm/ID?context=building_id_tab=inside
    match = re.search(r'/firm/(\d+)\?.*context=(\d+).*tab=inside', url)
    if match:
        # Возвращаем ID здания (второй захват)
        return match.group(2)
    
    # Ищем формат: /context/(\d+)/
    match = re.search(r'/context/(\d+)/', url)
    if match:
        return match.group(1)
    
    return None


def extract_building_id_from_url(url: str) -> Optional[str]:
    """
    Извлекает ID здания из URL различных форматов.
    
    Args:
        url: Любой URL 2GIS
        
    Returns:
        ID здания или None если не найдено
    """
    # Ищем в URL параметры, которые могут содержать ID здания
    # Например: ?context=building_id или /inside/building_id или гибридные форматы
    patterns = [
        r'/inside/(\d+)',  # /inside/building_id
        r'context[=/](\d+)',  # ?context=building_id или /context/building_id
        r'hybridEntities[^}]*?"id"[^0-9]*(\d+)',  # внутри гибридных данных: hybridEntities":[{"id":"1548748027185851","type":"building"}
        r'"buildingId"\s*:\s*"(\d+)"',  # в JSON данных в URL
        r'"buildingId"\s*:\s*(\d+)',  # в JSON данных в URL без кавычек
        r'hybridEntities[^}]*?(\d+)[^}]*?building',  # гибридные сущности
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None


def extract_building_id_from_dom(dom_tree) -> Optional[str]:
    """
    Извлекает ID здания из DOM дерева страницы.
    
    Args:
        dom_tree: DOM дерево страницы
        
    Returns:
        ID здания или None если не найдено
    """
    # Ищем ID здания в атрибутах элементов DOM
    def find_building_id_in_attributes(node):
        if node.attributes:
            for attr_name, attr_value in node.attributes.items():
                if 'building' in attr_name.lower():
                    # Ищем числовое значение в атрибуте
                    match = re.search(r'\d+', str(attr_value))
                    if match:
                        return match.group(0)
        return None
    
    # Обходим все узлы DOM
    nodes_to_check = [dom_tree]
    while nodes_to_check:
        current_node = nodes_to_check.pop(0)
        building_id = find_building_id_in_attributes(current_node)
        if building_id:
            return building_id
        
        # Добавляем детей в очередь для проверки
        nodes_to_check.extend(current_node.children)
    
    return None


def build_search_url(city: str, query: str) -> str:
    """
    Создает URL для поискового запроса.
    
    Args:
        city: Город (например, 'irkutsk')
        query: Поисковый запрос (например, 'ТЦ')
        
    Returns:
        Полный URL для поиска
    """
    city_enc = urllib.parse.quote_plus(city or "")
    query_enc = urllib.parse.quote_plus(query or "")
    return f"https://2gis.ru/{city_enc}/search/{query_enc}"


def build_inside_url(city: str, inside_id: str) -> str:
    """
    Создает URL для просмотра внутри здания.
    
    Args:
        city: Город (например, 'irkutsk')
        inside_id: ID внутри здания
        
    Returns:
        Полный URL для просмотра внутри здания
    """
    city_enc = urllib.parse.quote_plus(city or "")
    return f"https://2gis.ru/{city_enc}/inside/{inside_id}"


def find_building_links(dom_tree):
    """
    Находит все ссылки на здания в результатах поиска.
    
    Args:
        dom_tree: Дерево DOM текущей страницы
        
    Returns:
        Список ссылок на здания
    """
    def is_building_link(node):
        if node.local_name == 'a' and 'href' in node.attributes:
            href = node.attributes['href']
            # Ищем ссылки вида /city/firm/id или /city/inside/id
            # которые могут содержать ID зданий
            firm_match = re.search(r'/[^/]+/firm/(\d+)', href)
            inside_match = re.search(r'/[^/]+/inside/(\d+)', href)
            return bool(firm_match or inside_match)
        return False
    
    links = dom_tree.search(is_building_link)
    building_links = []
    for link_node in links:
        href = link_node.attributes['href']
        # Преобразуем относительный URL в абсолютный
        if href.startswith('/'):
            full_href = f"https://2gis.ru{href}"
        else:
            full_href = href
        building_links.append((link_node, full_href))
    
    return building_links


def find_inside_link(dom_tree) -> Optional[str]:
    """
    Находит ссылку "в здании" на странице фирмы.
    
    Args:
        dom_tree: Дерево DOM текущей страницы
        
    Returns:
        Ссылка "в здании" или None если не найдено
    """
    # Попробуем разные стратегии поиска ссылки "в здании"
    
    # Стратегия 1: Поиск по тексту элемента
    def by_text_content(node):
        if node.local_name == 'a' and 'href' in node.attributes:
            text_content = node.value.lower() if node.value else ""
            return 'здание' in text_content or 'в здании' in text_content or 'inside' in text_content.lower()
        return False
    
    links = dom_tree.search(by_text_content)
    if links:
        href = links[0].attributes['href']
        # Преобразуем относительный URL в абсолютный
        if href.startswith('/'):
            href = f"https://2gis.ru{href}"
        return href
    
    # Стратегия 2: Поиск по href (содержит inside)
    def by_href(node):
        if node.local_name == 'a' and 'href' in node.attributes:
            href = node.attributes['href'].lower()
            return '/inside/' in href
        return False
    
    links = dom_tree.search(by_href)
    if links:
        href = links[0].attributes['href']
        # Преобразуем относительный URL в абсолютный
        if href.startswith('/'):
            href = f"https://2gis.ru{href}"
        return href
    
    # Стратегия 3: Поиск по классам или атрибутам, которые могут указывать на "в здании"
    def by_attributes(node):
        if node.local_name == 'a' and 'href' in node.attributes:
            # Проверим атрибуты на наличие ключевых слов
            attrs_text = ' '.join([str(v).lower() for v in node.attributes.values()])
            return 'inside' in attrs_text or 'здание' in attrs_text or 'building' in attrs_text
        return False
    
    links = dom_tree.search(by_attributes)
    if links:
        href = links[0].attributes['href']
        # Преобразуем относительный URL в абсолютный
        if href.startswith('/'):
            href = f"https://2gis.ru{href}"
        return href
    
    return None


def find_first_org_in_building(dom_tree):
    """
    Находит первый элемент (организацию) во вкладке "в здании".
    
    Args:
        dom_tree: Дерево DOM текущей страницы
        
    Returns:
        Первый элемент организации или None если не найдено
    """
    # Ищем элементы, которые являются организациями в здании
    # Обычно это ссылки на фирмы внутри здания
    def is_building_org_link(node):
        if node.local_name == 'a' and 'href' in node.attributes:
            href = node.attributes['href']
            # Ищем ссылки вида /city/inside/building_id/firm/firm_id или /city/firm/firm_id?context=building_id
            # или другие варианты, где фигурирует идентификатор здания или фирмы внутри здания
            # Также может быть формат вроде /city/firm/firm_id?context=building_id_tab=inside
            return ('/firm/' in href and ('tab=inside' in href or '?context=' in href or '/inside/' in href))
        return False
    
    org_links = dom_tree.search(is_building_org_link)
    if org_links:
        return org_links[0]
    
    # Альтернативный подход - ищем любые ссылки на фирмы на текущей странице
    def is_any_firm_link(node):
        if node.local_name == 'a' and 'href' in node.attributes:
            href = node.attributes['href']
            # Ищем ссылки на фирмы
            firm_pattern = r'/[^/]+/firm/\d+'
            return re.search(firm_pattern, href) is not None
        return False
    
    firm_links = dom_tree.search(is_any_firm_link)
    if firm_links:
        return firm_links[0]
    
    return None


def extract_inside_ids_for_query(search_query: str, city: str, max_results: int = 1) -> List[str]:
    """
    Извлекает ID внутри зданий для заданного поискового запроса.
    
    Args:
        search_query: Поисковый запрос (например, 'ТЦ')
        city: Город для поиска
        max_results: Максимальное количество результатов для обработки
        
    Returns:
        Список ID внутри зданий
    """
    chrome_options = ChromeOptions()
    parser_options = ParserOptions()
    
    # Установим таймауты и параметры для корректной работы
    parser_options.max_records = max_results
    
    # Создаем удаленный интерфейс Chrome
    response_patterns = [r'https://catalog\.api\.2gis\.[^/]+/.*/items/byid']
    chrome_remote = ChromeRemote(chrome_options=chrome_options, response_patterns=response_patterns)
    chrome_remote.start()
    
    try:
        inside_ids = []
        
        # Создаем URL для поиска
        search_url = build_search_url(city, search_query)
        print(f"Открываем URL: {search_url}")
        
        # Переходим на страницу поиска
        chrome_remote.navigate(search_url, referer='https://google.com', timeout=30)
        
        # Ждем загрузки страницы
        time.sleep(3)
        
        # Получаем дерево DOM
        dom_tree = chrome_remote.get_document()
        
        # Находим все ссылки на здания
        processed_buildings = 0
        
        # Ищем ссылки на здания в результатах поиска
        building_links_with_nodes = find_building_links(dom_tree)
        
        print(f"Найдено {len(building_links_with_nodes)} потенциальных ссылок на здания")
        
        for link_node, full_href in building_links_with_nodes:
            if processed_buildings >= max_results:
                break
                
            print(f"Обрабатываем здание: {full_href}")
            
            # Переходим на страницу здания
            chrome_remote.navigate(full_href, timeout=30)
            time.sleep(2)
            
            # Получаем текущий URL после навигации
            current_url = chrome_remote.execute_script("window.location.href")
            print(f"Текущий URL после перехода: {current_url}")
            
            # Ищем ссылку "в здании"
            dom_tree = chrome_remote.get_document()
            inside_link = find_inside_link(dom_tree)
            
            if inside_link:
                print(f"Найдена ссылка 'в здании': {inside_link}")
                
                # Переходим по ссылке "в здании"
                chrome_remote.navigate(inside_link, timeout=30)
                time.sleep(2)
                
                # Получаем текущий URL после перехода по "в здании"
                current_url = chrome_remote.execute_script("window.location.href")
                print(f"Текущий URL после 'в здании': {current_url}")
                
                # Извлекаем ID из URL после перехода во вкладку "в здании"
                inside_id = extract_building_id_from_url(current_url)
                if inside_id:
                    print(f"Извлечен ID из ссылки 'в здании': {inside_id}")
                    inside_ids.append(inside_id)
                    processed_buildings += 1
                else:
                    print("Не удалось извлечь ID из ссылки 'в здании'")
            else:
                print("Ссылка 'в здании' не найдена")
        
        return inside_ids
        
    finally:
        chrome_remote.stop()


def main():
    parser = argparse.ArgumentParser(
        description="Извлечение ID внутри зданий из 2GIS для одиночных запросов."
    )
    parser.add_argument("--query", "-q", required=True, help="Поисковый запрос (например, 'ТЦ')")
    parser.add_argument("--city", "-c", default="irkutsk", help="Город для поиска (по умолчанию: irkutsk)")
    parser.add_argument("--out", "-o", default="2gis_inside_links.txt", 
                       help="Выходной файл для сохранения ссылок (по умолчанию: 2gis_inside_links.txt)")
    parser.add_argument("--limit", "-l", type=int, default=1, 
                       help="Максимальное количество результатов для обработки (по умолчанию: 1)")
    
    args = parser.parse_args()
    
    print(f"Поиск '{args.query}' в городе '{args.city}'...")
    
    try:
        # Извлекаем ID внутри зданий
        inside_ids = extract_inside_ids_for_query(args.query, args.city, args.limit)
        
        if not inside_ids:
            print("Не удалось извлечь ни одного ID")
            return
        
        print(f"Извлечено {len(inside_ids)} ID")
        
        # Создаем полные URL для сохранения
        links = [build_inside_url(args.city, inside_id) for inside_id in inside_ids]
        
        # Сохраняем в файл
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(out_path, "w", encoding="utf-8") as f:
            for link in links:
                f.write(link + "\n")
        
        print(f"Сохранено {len(links)} ссылок в {out_path}")
        
    except KeyboardInterrupt:
        print("\nОперация прервана пользователем")
    except Exception as e:
        print(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()