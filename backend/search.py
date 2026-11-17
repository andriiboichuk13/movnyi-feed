"""Перевірка бази: шукає найближчі за змістом українські відео до будь-якого тексту.

Запуск:  .venv\\Scripts\\python search.py "НОУТБУК ПО ЦЕНЕ АВТО"

Можна писати російською: багатомовна модель має знайти українські відео на ту саму тему.
Рахує схожість так само, як сервер: окремо назва з назвою і назва+опис з назвою+описом.
"""

import sys

import config as C


def main():
    query = " ".join(sys.argv[1:]).strip()
    if not query:
        sys.exit('Напишіть текст для пошуку, наприклад: python search.py "як вибрати ноутбук"')

    conn = C.connect_db()
    total = conn.execute("select count(*) from videos").fetchone()[0]
    if not total:
        sys.exit("База порожня. Спершу запустіть ingest.py.")

    model = C.load_model()
    qvec, tvec = C.embed(model, [C.embedding_text(query, [], ""), C.title_text(query)])
    w = C.TITLE_WEIGHT

    rows = conn.execute(
        f"""select v.video_id, v.title, c.title,
                   1 - (coalesce(v.title_embedding, v.embedding) <=> %(t)s) as title_sim,
                   1 - (v.embedding <=> %(q)s) as full_sim
            from videos v join channels c using (channel_id)
            order by ({w} * (coalesce(v.title_embedding, v.embedding) <=> %(t)s) + {1 - w} * (v.embedding <=> %(q)s))
            limit 5""",
        dict(t=tvec, q=qvec),
    ).fetchall()

    print(f"\nЗапит: {query}\nУ базі {total} відео. Найближчі:\n")
    for video_id, title, channel, title_sim, full_sim in rows:
        score = w * title_sim + (1 - w) * full_sim
        print(f"{score:.3f}  (назва {title_sim:.3f}, з описом {full_sim:.3f})  {title}")
        print(f"       {channel} · https://www.youtube.com/watch?v={video_id}\n")


if __name__ == "__main__":
    main()
