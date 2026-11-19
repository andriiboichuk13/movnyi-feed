"""Публікує звіт агента Студії на сайт для авторів (сторінка /creators/studio).

Звіт записується в таблицю studio_reports у Supabase, звідки його читає сервер на Railway.

Запуск (з кореня репозиторію):
    backend\\.venv\\Scripts\\python studio\\publish_report.py            # останній звіт
    backend\\.venv\\Scripts\\python studio\\publish_report.py --file studio\\data\\reports\\20261119-234707-andrii.md
    backend\\.venv\\Scripts\\python studio\\publish_report.py --all      # усі звіти з папки

Повторна публікація того самого файла оновлює запис, а не дублює його.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

STUDIO_DIR = Path(__file__).resolve().parent
REPORTS_DIR = STUDIO_DIR / "data" / "reports"
sys.path.insert(0, str(STUDIO_DIR.parent / "backend"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(STUDIO_DIR.parent / "backend" / ".env")
import config as C  # noqa: E402

TABLE_SQL = """create table if not exists studio_reports (
    id bigserial primary key,
    created_at timestamptz not null default now(),
    source_file text unique not null,
    creator text,
    idea text not null,
    game text,
    model text,
    report_md text not null
)"""
HEADER = re.compile(r"^- (Креатор|Ідея|Модель|Дата|MCP [\w-]+): (.*)$")


def parse(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    meta, body_start = {}, 0
    for i, line in enumerate(lines):
        m = HEADER.match(line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
            body_start = i + 1
        elif meta and line.strip() and not line.startswith("#"):
            break
    body = "\n".join(lines[body_start:]).strip()
    if not meta.get("Ідея") or not body:
        raise ValueError(f"{path.name}: не схоже на звіт агента (немає ідеї або тексту)")
    return meta, body


def detect_game(text: str):
    games = json.loads((STUDIO_DIR / "config" / "games.json").read_text(encoding="utf-8"))["games"]
    hits = Counter()
    low = text.lower()
    for g in games:
        for alias in g["aliases"]:
            hits[g["name"]] += len(re.findall(r"(?<![\w])" + re.escape(alias) + r"(?![\w])", low))
    best = hits.most_common(1)
    return best[0][0] if best and best[0][1] else None


def publish(conn, path: Path):
    meta, body = parse(path)
    game = detect_game(meta["Ідея"] + "\n" + body[:3000])
    row = conn.execute(
        """insert into studio_reports (source_file, creator, idea, game, model, report_md)
           values (%s, %s, %s, %s, %s, %s)
           on conflict (source_file) do update set
             creator = excluded.creator, idea = excluded.idea, game = excluded.game,
             model = excluded.model, report_md = excluded.report_md
           returning id""",
        (path.name, meta.get("Креатор"), meta["Ідея"], game, meta.get("Модель"), body),
    ).fetchone()
    print(f"✓ {path.name} → звіт №{row[0]} ({game or 'гру не визначено'})")
    return row[0]


def main():
    p = argparse.ArgumentParser(description="Публікація звітів агента Студії на сайт")
    p.add_argument("--file", help="шлях до звіту (за замовчуванням — останній у studio/data/reports)")
    p.add_argument("--all", action="store_true", help="опублікувати всі звіти з папки")
    args = p.parse_args()

    files = sorted(REPORTS_DIR.glob("*.md"))
    if args.file:
        files = [Path(args.file)]
    elif not args.all:
        files = files[-1:]
    if not files:
        sys.exit(f"Звітів не знайдено в {REPORTS_DIR}. Спершу запустіть агента.")

    conn = C.connect_db()
    conn.execute(TABLE_SQL)
    ids = [publish(conn, f) for f in files]
    print(f"\nПереглянути: https://movnyi-feed-production.up.railway.app/creators/studio#{ids[-1]}")


if __name__ == "__main__":
    main()
