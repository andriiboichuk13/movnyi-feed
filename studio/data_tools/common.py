"""Спільне для скриптів збору даних Студії: шляхи, налаштування ігор, доступ до модулів бекенду."""

import json
import sys
from pathlib import Path

STUDIO_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = STUDIO_DIR.parent
BACKEND_DIR = REPO_DIR / "backend"
RAW_DIR = STUDIO_DIR / "data" / "raw"
DATASET_DIR = STUDIO_DIR / "data" / "dataset"
GAMES_PATH = STUDIO_DIR / "config" / "games.json"

# Перевикористовуємо код бекенду: YouTube API, визначення мови, очищення описів, модель.
# Секрети теж беремо з backend/.env, щоб не дублювати їх.
sys.path.insert(0, str(BACKEND_DIR))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")


def load_games():
    with open(GAMES_PATH, encoding="utf-8") as f:
        return json.load(f)["games"]


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def append_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
