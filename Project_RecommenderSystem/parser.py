"""
Модуль загрузки данных книг из OpenLibrary API.
Используется для формирования базы данных книг рекомендательной системы.
"""

import requests
import time
import json
from typing import Dict, List
from tqdm import tqdm

SEARCH_API_URL = "https://openlibrary.org/search.json"
WORK_DETAIL_URL = "https://openlibrary.org{work_key}.json"


def fetch_api_response(url, params=None, max_retries=3):
    """
    Выполнить GET-запрос к API с повторными попытками при ошибке.
    Возвращает JSON-ответ или пустой словарь при неудаче.
    """
    for _ in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                return response.json()
        except requests.RequestException:
            time.sleep(1)
    return {}


def parse_search_result(raw_record: Dict) -> Dict:
    """
    Преобразовать сырую запись из результатов поиска
    в стандартный формат книги для базы данных.
    """
    title = raw_record.get('title') or ''
    authors = raw_record.get('author_name') or []
    publication_year = raw_record.get('first_publish_year')
    return {
        "title": title.strip(),
        "author": ", ".join(authors) if authors else "",
        "year": int(publication_year) if publication_year else None,
        "genre": "",
        "description": "",
    }


def enrich_book_metadata(book: Dict, work_key: str) -> Dict:
    """
    Дополнить запись книги метаданными (жанр, описание)
    из детальной информации о произведении.
    """
    if not work_key:
        return book

    work_data = fetch_api_response(WORK_DETAIL_URL.format(work_key=work_key))
    if not work_data:
        return book

    subjects = work_data.get("subjects", [])
    book["genre"] = subjects[0] if subjects else ""

    description = work_data.get("description")
    if isinstance(description, dict):
        description = description.get("value", "")
    elif not isinstance(description, str):
        description = ""
    book["description"] = description.strip()[:200]

    return book


def download_book_catalog(limit=1000, search_query="fiction"):
    """
    Загрузить каталог книг из OpenLibrary API.
    Использует генератор для постраничной загрузки результатов.
    Удаляет дубликаты по названию и автору.
    """
    collected_books: List[Dict] = []
    current_page = 1
    progress_bar = tqdm(total=limit, desc="Загрузка книг из OpenLibrary")

    while len(collected_books) < limit:
        params = {"q": search_query, "page": current_page, "limit": 100}
        api_data = fetch_api_response(SEARCH_API_URL, params)
        documents = api_data.get("docs", [])
        if not documents:
            break
        for document in documents:
            book = parse_search_result(document)
            work_key = document.get("key")
            book = enrich_book_metadata(book, work_key)
            if book["title"]:
                collected_books.append(book)
                progress_bar.update(1)
                if len(collected_books) >= limit:
                    break
        current_page += 1
        time.sleep(0.2)
    progress_bar.close()

    seen_books = set()
    unique_books = []
    for book in collected_books:
        book_key = (book["title"].lower(), book["author"].lower())
        if book_key not in seen_books:
            seen_books.add(book_key)
            unique_books.append(book)
    return unique_books[:limit]


import json
from pathlib import Path

def main():
    """Точка входа: загрузить книги и сохранить в JSON-файл."""
    
    base_dir = Path(__file__).resolve().parent
    books_file = base_dir / "books.json"
    book_catalog = download_book_catalog(limit=100, search_query="fiction")
    with open(books_file, "w", encoding="utf-8") as output_file:
        json.dump(book_catalog, output_file, ensure_ascii=False, indent=2)
    print(f"Сохранено {len(book_catalog)} книг в {books_file}")


if __name__ == "__main__":
    main()