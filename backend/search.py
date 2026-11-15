"""Перевірка бази: шукає найближчі за змістом українські відео до будь-якого тексту.

Запуск:  .venv\\Scripts\\python search.py "НОУТБУК ПО ЦЕНЕ АВТО"

Можна писати російською: багатомовна модель має знайти українські відео на ту саму тему.
Це та сама логіка, на якій працюватиме GET /alternatives.
"""

import sys

from config import connect_db, embed, load_model


def main():
    query = " ".join(sys.argv[1:]).strip()
    if not query:
        sys.exit('Напишіть текст для пошуку, наприклад: python search.py "як вибрати ноутбук"')

    conn = connect_db()
    total = conn.execute("select count(*) from videos").fetchone()[0]
    if not total:
        sys.exit("База порожня. Спершу запустіть ingest.py.")

    model = load_model()
    vector = embed(model, [query])[0]

    rows = conn.execute(
        """select v.video_id, v.title, c.title, 1 - (v.embedding <=> %s) as score
           from videos v join channels c using (channel_id)
           order by v.embedding <=> %s
           limit 5""",
        (vector, vector),
    ).fetchall()

    print(f"\nЗапит: {query}\nУ базі {total} відео. Найближчі:\n")
    for video_id, title, channel, score in rows:
        print(f"{score:.3f}  {title}")
        print(f"       {channel} · https://www.youtube.com/watch?v={video_id}\n")
    print("Чим ближче схожість до 1, тим ближча тема. Поріг для «тієї самої теми» підберемо на тестових запитах.")


if __name__ == "__main__":
    main()
