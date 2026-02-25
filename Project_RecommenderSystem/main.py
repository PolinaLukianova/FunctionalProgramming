from flask import Flask, render_template_string, request, send_file
import json
import csv
import io
import os
from functools import reduce

BASE_DIR = os.path.dirname(__file__)
BOOK_DATABASE_PATH = os.path.join(BASE_DIR, "data_rus.json")


def load_book_database(filepath):
    """Загрузить базу данных книг из JSON-файла."""
    with open(filepath, encoding='utf-8') as file:
        return json.load(file)


BOOK_DATABASE = load_book_database(BOOK_DATABASE_PATH)

AVAILABLE_GENRES = sorted(set(
    (book.get("genre") or "").strip()
    for book in BOOK_DATABASE if book.get("genre")
))
AVAILABLE_AUTHORS = sorted(set(
    (book.get("author") or "").strip()
    for book in BOOK_DATABASE if book.get("author")
))



def normalize_text(text):
    """Нормализовать текст: привести к нижнему регистру и убрать пробелы."""
    return text.strip().lower() if text else ""


def build_user_preferences(selected_genres, selected_authors, entered_keywords):
    """
    Сформировать словарь предпочтений пользователя
    на основе выбранных жанров, авторов и ключевых слов.
    """
    return {
        "genres": list(map(normalize_text, selected_genres or [])),
        "authors": list(map(normalize_text, selected_authors or [])),
        "keywords": list(map(normalize_text, entered_keywords or [])),
    }



def calculate_genre_score(book, user_preferences):
    """Оценить совпадение жанра книги с предпочтениями пользователя."""
    return 1.0 if normalize_text(book.get("genre")) in user_preferences["genres"] else 0.0


def calculate_author_score(book, user_preferences):
    """Оценить совпадение автора книги с предпочтениями пользователя."""
    return 1.5 if normalize_text(book.get("author")) in user_preferences["authors"] else 0.0


def calculate_keyword_score(book, user_preferences):
    """
    Оценить совпадение ключевых слов пользователя
    с текстовыми полями книги (название, описание, жанр, автор, ключевые слова).
    """
    book_text = " ".join([
        str(book.get("title", "")),
        str(book.get("description", "")),
        str(book.get("genre", "")),
        str(book.get("author", "")),
        " ".join(book.get("keywords", [])),
    ]).lower()
    matched_count = sum(1 for kw in user_preferences["keywords"] if kw and kw in book_text)
    return 0.5 * matched_count


def calculate_relevance_score(book, user_preferences):
    """
    Вычислить общий рейтинг соответствия книги предпочтениям пользователя.
    Композиция функций оценки: жанр + автор + ключевые слова.
    """
    scoring_functions = [
        calculate_genre_score,
        calculate_author_score,
        calculate_keyword_score,
    ]
    return reduce(
        lambda total, func: total + func(book, user_preferences),
        scoring_functions,
        0.0
    )


def filter_books_by_genre_and_author(books, selected_genres, selected_authors):
    """
    Отфильтровать книги по выбранным жанрам и/или авторам.
    Если выбраны и жанры, и авторы — возвращаются книги, подходящие хотя бы по одному критерию.
    Если ничего не выбрано — возвращаются все книги.
    """
    if not selected_genres and not selected_authors:
        return books

    genres_set = set(genre.lower() for genre in (selected_genres or []))
    authors_set = set(author.lower() for author in (selected_authors or []))

    def matches_criteria(book):
        book_genre = (book.get("genre") or "").lower()
        book_author = (book.get("author") or "").lower()
        if genres_set and authors_set:
            return (book_genre in genres_set) or (book_author in authors_set)
        if genres_set:
            return book_genre in genres_set
        if authors_set:
            return book_author in authors_set
        return True

    return list(filter(matches_criteria, books))


def filter_books_by_year(books, year_threshold):
    """
    Отфильтровать книги, выпущенные после указанного года.
    Генератор используется для эффективной обработки больших объёмов данных.
    """
    if not year_threshold:
        return books

    def year_generator(book_list, threshold):
        for book in book_list:
            try:
                if book.get("year") and int(book["year"]) > threshold:
                    yield book
            except (ValueError, TypeError):
                pass

    return list(year_generator(books, year_threshold))


def sort_recommendations(books, sort_key="relevance_score", descending=True):
    """
    Отсортировать список рекомендаций по указанному критерию:
    - 'relevance_score' — по рейтингу соответствия
    - 'title' — по алфавиту
    - 'year' — по году публикации
    """
    sort_map = {
        "title": lambda b: b.get("title", "").lower(),
        "year": lambda b: b.get("year") or 0,
        "relevance_score": lambda b: b.get("relevance_score", 0),
    }
    key_fn = sort_map.get(sort_key, sort_map["relevance_score"])
    return sorted(books, key=key_fn, reverse=descending)


def generate_scored_books(books, user_preferences):
    """
    Генератор: для каждой книги вычисляет рейтинг соответствия
    и отдаёт книгу с добавленным полем relevance_score.
    """
    for book in books:
        score = calculate_relevance_score(book, user_preferences)
        yield dict(book, relevance_score=score)


def generate_recommendations(book_database, user_preferences, selected_genres,
                             selected_authors, year_threshold, sort_key):
    """
    Основная функция формирования рекомендаций:
    1. Фильтрация по жанрам и авторам
    2. Фильтрация по году
    3. Оценка рейтинга соответствия (через генератор)
    4. Сортировка результатов
    """
    filtered_books = filter_books_by_genre_and_author(book_database, selected_genres, selected_authors)
    filtered_books = filter_books_by_year(filtered_books, year_threshold)

    has_preferences = bool(
        user_preferences["genres"] or
        user_preferences["authors"] or
        user_preferences["keywords"]
    )

    scored_books = list(generate_scored_books(filtered_books, user_preferences))

    if has_preferences:
        scored_books = [b for b in scored_books if b["relevance_score"] > 0]

    if not has_preferences and year_threshold is None:
        return []

    descending = sort_key != "title"
    return sort_recommendations(scored_books, sort_key=sort_key, descending=descending)


app = Flask(__name__)

HTML_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Рекомендательная система книг</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    background: #f0f4f8;
    min-height: 100vh;
    padding: 0;
    color: #2d3748;
  }

  .header {
    background: linear-gradient(135deg, #1a365d 0%, #2a4a7f 50%, #3182ce 100%);
    color: #fff;
    padding: 28px 40px;
    text-align: center;
    box-shadow: 0 4px 15px rgba(0,0,0,0.15);
  }

  .header h1 {
    font-size: 1.8em;
    letter-spacing: 0.5px;
    margin-bottom: 6px;
  }

  .header p {
    font-size: 0.95em;
    opacity: 0.9;
  }

  .container {
    max-width: 1200px;
    margin: 25px auto;
    padding: 0 20px;
  }

  .card {
    background: #fff;
    border-radius: 12px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.07);
    padding: 25px 30px;
    margin-bottom: 25px;
  }

  .card-title {
    font-size: 1.1em;
    font-weight: 700;
    color: #1a365d;
    margin-bottom: 15px;
    padding-bottom: 8px;
    border-bottom: 2px solid #e2e8f0;
  }

  .form-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
  }

  @media (max-width: 768px) {
    .form-grid { grid-template-columns: 1fr; }
  }

  .section-label {
    font-size: 0.9em;
    font-weight: 600;
    color: #4a5568;
    margin-bottom: 4px;
  }

  .select-links {
    margin: 4px 0 6px;
  }

  .select-links a {
    font-size: 0.8em;
    color: #3182ce;
    text-decoration: none;
  }

  .select-links a:hover {
    text-decoration: underline;
  }

  .genre-list, .author-list {
    max-height: 220px;
    overflow-y: auto;
    padding: 8px;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    background: #f7fafc;
  }

  .genre-list::-webkit-scrollbar,
  .author-list::-webkit-scrollbar {
    width: 6px;
  }

  .genre-list::-webkit-scrollbar-thumb,
  .author-list::-webkit-scrollbar-thumb {
    background: #cbd5e0;
    border-radius: 3px;
  }

  .genre-list label, .author-list label {
    display: block;
    padding: 3px 6px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.9em;
    color: #4a5568;
    transition: background 0.15s;
  }

  .genre-list label:hover, .author-list label:hover {
    background: #edf2f7;
    color: #1a365d;
  }

  .search-input {
    width: 100%;
    padding: 8px 12px;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    font-size: 0.9em;
    margin-bottom: 8px;
    outline: none;
    transition: border-color 0.2s;
  }

  .search-input:focus {
    border-color: #3182ce;
  }

  .extra-params {
    display: flex;
    flex-wrap: wrap;
    gap: 18px;
    align-items: flex-end;
  }

  .param-group {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .param-group label {
    font-size: 0.85em;
    font-weight: 600;
    color: #4a5568;
  }

  .param-group input, .param-group select {
    padding: 8px 12px;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    font-size: 0.9em;
    outline: none;
    transition: border-color 0.2s;
    background: #fff;
  }

  .param-group input:focus, .param-group select:focus {
    border-color: #3182ce;
  }

  .btn-search {
    background: #2b6cb0;
    color: #fff;
    border: none;
    padding: 12px 35px;
    font-size: 1em;
    font-weight: 600;
    border-radius: 8px;
    cursor: pointer;
    transition: background 0.2s, box-shadow 0.2s;
    margin-top: 12px;
  }

  .btn-search:hover {
    background: #1a4e8a;
    box-shadow: 0 4px 12px rgba(43, 108, 176, 0.3);
  }

  .results-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 12px;
  }

  .results-count {
    font-size: 0.9em;
    color: #718096;
  }

  .table-wrapper {
    max-height: 550px;
    overflow-y: auto;
    border-radius: 8px;
    border: 1px solid #e2e8f0;
  }

  .table-wrapper::-webkit-scrollbar {
    width: 6px;
  }

  .table-wrapper::-webkit-scrollbar-thumb {
    background: #cbd5e0;
    border-radius: 3px;
  }

  table {
    width: 100%;
    border-collapse: collapse;
  }

  thead {
    position: sticky;
    top: 0;
    z-index: 1;
  }

  th {
    background: #2b6cb0;
    color: #fff;
    padding: 10px 8px;
    font-size: 0.85em;
    text-align: left;
    font-weight: 600;
    white-space: nowrap;
  }

  td {
    padding: 9px 8px;
    border-bottom: 1px solid #edf2f7;
    font-size: 0.88em;
    vertical-align: top;
    color: #2d3748;
  }

  tr:nth-child(even) { background: #f7fafc; }
  tr:hover td { background: #edf2f7; }

  .desc-cell {
    max-width: 350px;
    line-height: 1.4;
  }

  .score-cell {
    font-weight: 700;
    color: #2b6cb0;
    text-align: center;
  }

  .check-cell {
    text-align: center;
  }

  .check-cell input[type="checkbox"] {
    width: 17px;
    height: 17px;
    cursor: pointer;
    accent-color: #2b6cb0;
  }

  .save-actions {
    margin-top: 15px;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
  }

  .btn-save-json {
    background: #276749;
    color: #fff;
    border: none;
    padding: 10px 25px;
    font-size: 0.95em;
    font-weight: 600;
    border-radius: 8px;
    cursor: pointer;
    transition: background 0.2s, box-shadow 0.2s;
  }

  .btn-save-json:hover {
    background: #1e5238;
    box-shadow: 0 4px 12px rgba(39, 103, 73, 0.3);
  }

  .btn-save-csv {
    background: #744210;
    color: #fff;
    border: none;
    padding: 10px 25px;
    font-size: 0.95em;
    font-weight: 600;
    border-radius: 8px;
    cursor: pointer;
    transition: background 0.2s, box-shadow 0.2s;
  }

  .btn-save-csv:hover {
    background: #5a3408;
    box-shadow: 0 4px 12px rgba(116, 66, 16, 0.3);
  }

  .no-results {
    text-align: center;
    padding: 40px;
    color: #a0aec0;
    font-size: 1em;
  }

  .footer {
    text-align: center;
    color: #a0aec0;
    font-size: 0.8em;
    margin-top: 20px;
    padding-bottom: 20px;
  }
</style>

<script>
function filterAuthorList() {
    var query = document.getElementById("authorSearchField").value.toLowerCase();
    document.querySelectorAll(".author-item").forEach(function(el) {
        el.style.display = el.dataset.name.includes(query) ? "" : "none";
    });
}

function filterGenreList() {
    var query = document.getElementById("genreSearchField").value.toLowerCase();
    document.querySelectorAll(".genre-item").forEach(function(el) {
        el.style.display = el.dataset.name.includes(query) ? "" : "none";
    });
}

function selectAllGenres(checked) {
    document.querySelectorAll('input[name="selected_genres"]').forEach(function(cb) {
        if (cb.closest('.genre-item').style.display !== 'none') {
            cb.checked = checked;
        }
    });
}

function selectAllAuthors(checked) {
    document.querySelectorAll('input[name="selected_authors"]').forEach(function(cb) {
        if (cb.closest('.author-item').style.display !== 'none') {
            cb.checked = checked;
        }
    });
}

function selectAllBooks(checked) {
    document.querySelectorAll('input[name="selected_books"]').forEach(function(cb) {
        cb.checked = checked;
    });
}
</script>
</head>

<body>

<div class="header">
  <h1>Рекомендательная система книг</h1>
  <p>Подбор книг на основе предпочтений пользователя</p>
</div>

<div class="container">

<form method="post">
<div class="card">
  <div class="card-title">Ваши предпочтения</div>
  <div class="form-grid">

    <div>
      <div class="section-label">Жанры</div>
      <div class="select-links">
        <a href="#" onclick="selectAllGenres(true); return false;">Выбрать все</a>
        &nbsp;|&nbsp;
        <a href="#" onclick="selectAllGenres(false); return false;">Снять все</a>
      </div>
      <input id="genreSearchField" class="search-input" onkeyup="filterGenreList()" placeholder="Поиск жанра...">
      <div class="genre-list">
        {% for genre in available_genres %}
        <div class="genre-item" data-name="{{ genre.lower() }}">
          <label><input type="checkbox" name="selected_genres" value="{{ genre }}"
            {% if genre in form_genres %}checked{% endif %}> {{ genre }}</label>
        </div>
        {% endfor %}
      </div>
    </div>

    <div>
      <div class="section-label">Авторы</div>
      <div class="select-links">
        <a href="#" onclick="selectAllAuthors(true); return false;">Выбрать все</a>
        &nbsp;|&nbsp;
        <a href="#" onclick="selectAllAuthors(false); return false;">Снять все</a>
      </div>
      <input id="authorSearchField" class="search-input" onkeyup="filterAuthorList()" placeholder="Поиск автора...">
      <div class="author-list">
        {% for author in available_authors %}
        <div class="author-item" data-name="{{ author.lower() }}">
          <label><input type="checkbox" name="selected_authors" value="{{ author }}"
            {% if author in form_authors %}checked{% endif %}> {{ author }}</label>
        </div>
        {% endfor %}
      </div>
    </div>

  </div>
</div>

<div class="card">
  <div class="card-title">Дополнительные параметры</div>
  <div class="extra-params">
    <div class="param-group">
      <label>Ключевые слова (через запятую)</label>
      <input name="user_keywords" placeholder="например: любовь, война" style="width:280px;"
        value="{{ form_keywords }}">
    </div>
    <div class="param-group">
      <label>Год публикации после</label>
      <input name="year_threshold" type="number" placeholder="например: 1950" style="width:150px;"
        value="{{ form_year }}">
    </div>
    <div class="param-group">
      <label>Сортировать по</label>
      <select name="sort_criteria">
        <option value="relevance_score" {% if form_sort == 'relevance_score' %}selected{% endif %}>Рейтингу соответствия</option>
        <option value="title" {% if form_sort == 'title' %}selected{% endif %}>Алфавиту (название)</option>
        <option value="year" {% if form_sort == 'year' %}selected{% endif %}>Году публикации</option>
      </select>
    </div>
  </div>
  <br>
  <button type="submit" class="btn-search">Найти рекомендации</button>
</div>
</form>

{% if recommended_books is not none %}
<div class="card">
  {% if recommended_books %}
  <div class="results-header">
    <div class="card-title" style="border:none; margin:0; padding:0;">Результаты рекомендаций</div>
    <div class="results-count">Найдено книг: {{ recommended_books|length }}</div>
  </div>

  <form method="post" action="/save_reading_list">
    <div class="select-links" style="margin-bottom:8px;">
      <a href="#" onclick="selectAllBooks(true); return false;">Выбрать все</a>
      &nbsp;|&nbsp;
      <a href="#" onclick="selectAllBooks(false); return false;">Снять все</a>
    </div>

    <div class="table-wrapper">
    <table>
      <thead>
      <tr>
        <th>No</th>
        <th>Название</th>
        <th>Автор</th>
        <th>Жанр</th>
        <th>Год</th>
        <th>Описание</th>
        <th>Рейтинг</th>
        <th>Выбрать</th>
      </tr>
      </thead>
      <tbody>
      {% for book in recommended_books %}
      <tr>
        <td>{{ loop.index }}</td>
        <td><strong>{{ book.title }}</strong></td>
        <td>{{ book.author }}</td>
        <td>{{ book.genre }}</td>
        <td>{{ book.year }}</td>
        <td class="desc-cell">{{ book.description }}</td>
        <td class="score-cell">{{ "%.2f"|format(book.relevance_score) }}</td>
        <td class="check-cell"><input type="checkbox" name="selected_books" value="{{ loop.index0 }}"></td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
    </div>

    <input type="hidden" name="books_payload" value='{{ recommended_books | tojson | safe }}'>

    <div class="save-actions">
      <button type="submit" name="save_format" value="json" class="btn-save-json">Сохранить в JSON</button>
      <button type="submit" name="save_format" value="csv" class="btn-save-csv">Сохранить в CSV</button>
      <span style="font-size:0.85em; color:#718096;">-- выбранные книги будут сохранены в файл</span>
    </div>
  </form>

  {% else %}
  <div class="no-results">
    По вашим предпочтениям ничего не найдено. Попробуйте изменить параметры поиска.
  </div>
  {% endif %}
</div>
{% endif %}

</div>
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def recommendation_page():
    """Главная страница рекомендательной системы."""
    recommended_books = None
    form_genres = []
    form_authors = []
    form_keywords = ""
    form_year = ""
    form_sort = "relevance_score"

    if request.method == "POST":
        selected_genres = request.form.getlist("selected_genres")
        selected_authors = request.form.getlist("selected_authors")
        raw_keywords = request.form.get("user_keywords", "")
        user_keywords = [kw.strip() for kw in raw_keywords.split(",") if kw.strip()]
        year_threshold_raw = request.form.get("year_threshold", "")
        sort_criteria = request.form.get("sort_criteria", "relevance_score")

        form_genres = selected_genres
        form_authors = selected_authors
        form_keywords = raw_keywords
        form_year = year_threshold_raw
        form_sort = sort_criteria

        try:
            year_threshold = int(year_threshold_raw) if year_threshold_raw else None
        except ValueError:
            year_threshold = None

        user_preferences = build_user_preferences(selected_genres, selected_authors, user_keywords)

        recommended_books = generate_recommendations(
            book_database=BOOK_DATABASE,
            user_preferences=user_preferences,
            selected_genres=selected_genres,
            selected_authors=selected_authors,
            year_threshold=year_threshold,
            sort_key=sort_criteria,
        )

    return render_template_string(
        HTML_TEMPLATE,
        recommended_books=recommended_books,
        available_genres=AVAILABLE_GENRES,
        available_authors=AVAILABLE_AUTHORS,
        form_genres=form_genres,
        form_authors=form_authors,
        form_keywords=form_keywords,
        form_year=form_year,
        form_sort=form_sort,
    )


@app.route("/save_reading_list", methods=["POST"])
def save_reading_list():
    """
    Сохранить выбранные книги в файл (JSON или CSV).
    Формирует «Список прочитать» из отмеченных пользователем книг.
    """
    books_payload = json.loads(request.form.get("books_payload") or "[]")
    selected_indices = request.form.getlist("selected_books")
    save_format = request.form.get("save_format", "json")

    reading_list = []
    for index in selected_indices:
        try:
            reading_list.append(books_payload[int(index)])
        except (IndexError, ValueError):
            pass

    if not reading_list:
        reading_list = books_payload

    if save_format == "csv":
        buffer = io.StringIO()
        fieldnames = ["title", "author", "genre", "year", "description", "relevance_score"]
        csv_writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction='ignore')
        csv_writer.writeheader()
        for book in reading_list:
            csv_writer.writerow(book)
        byte_buffer = io.BytesIO(buffer.getvalue().encode("utf-8-sig"))
        return send_file(
            byte_buffer,
            mimetype="text/csv",
            as_attachment=True,
            download_name="reading_list.csv",
        )
    else:
        byte_buffer = io.BytesIO(
            json.dumps(reading_list, ensure_ascii=False, indent=2).encode("utf-8")
        )
        return send_file(
            byte_buffer,
            mimetype="application/json",
            as_attachment=True,
            download_name="reading_list.json",
        )


if __name__ == "__main__":
    app.run(debug=True)