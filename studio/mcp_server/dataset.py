"""Датасет Студії в пам'яті: відео, вектори, схожість, часові вікна.

Сервер працює лише з локальними файлами (data/dataset/), у мережу не ходить.
«Сьогодні» для часових вікон — момент збирання датасету (manifest.built_at), а не поточна дата:
так результати інструментів детерміновані й однакові на кожному запуску демо.
"""

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

STUDIO_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = STUDIO_DIR.parent / "backend"
DEFAULT_DATASET_DIR = STUDIO_DIR / "data" / "dataset"
GAMES_PATH = STUDIO_DIR / "config" / "games.json"

sys.path.insert(0, str(BACKEND_DIR))
import config as C  # noqa: E402  (модель, префікс, нормалізація — як у сервері розширення)


class DatasetError(Exception):
    """Датасет відсутній або пошкоджений."""


def parse_time(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


class Dataset:
    def __init__(self, path: Path = DEFAULT_DATASET_DIR, embedder=None):
        path = Path(path)
        try:
            with open(path / "manifest.json", encoding="utf-8") as f:
                self.manifest = json.load(f)
            with open(path / "videos.jsonl", encoding="utf-8") as f:
                self.videos = [json.loads(line) for line in f if line.strip()]
            self.emb_full = np.load(path / "emb_full.npy").astype(np.float32)
            self.emb_title = np.load(path / "emb_title.npy").astype(np.float32)
        except FileNotFoundError as e:
            raise DatasetError(f"Не знайдено файл датасету: {e.filename}. Запустіть studio/data_tools/build_dataset.py") from e
        if not (len(self.videos) == len(self.emb_full) == len(self.emb_title)):
            raise DatasetError("Кількість відео й векторів не збігається: перебудуйте датасет.")

        with open(GAMES_PATH, encoding="utf-8") as f:
            self.games = {g["slug"]: g for g in json.load(f)["games"]}
        self.as_of = parse_time(self.manifest["built_at"])
        self.published = np.array([parse_time(v["published_at"]).timestamp() for v in self.videos])
        self.views = np.array([v["views"] for v in self.videos], dtype=np.int64)
        self.lang = np.array([v["lang"] for v in self.videos])
        self.game = np.array([v["game"] for v in self.videos])
        self.by_id = {v["video_id"]: i for i, v in enumerate(self.videos)}
        self._embedder = embedder

    # ------------------------------------------------------------ вибірки

    def rows(self, game: str, langs=("uk", "ru", "en"), window_days=None, min_views=0):
        mask = (self.game == game) & np.isin(self.lang, list(langs)) & (self.views >= min_views)
        if window_days is not None:
            cutoff = (self.as_of - dt.timedelta(days=window_days)).timestamp()
            mask &= self.published >= cutoff
        return np.nonzero(mask)[0]

    def age_days(self, rows):
        return (self.as_of.timestamp() - self.published[rows]) / 86400.0

    # ------------------------------------------------------------ схожість

    def embed_query(self, text: str):
        """(вектор повного тексту, вектор назви) для довільного тексту — так само, як для відео."""
        if self._embedder is None:
            model = C.load_model()
            self._embedder = lambda texts: C.embed(model, texts)
        vecs = np.asarray(self._embedder([C.embedding_text(text, [], ""), C.title_text(text)]), dtype=np.float32)
        return vecs[0], vecs[1]

    def query_similarity(self, qfull, qtitle, rows):
        w = C.TITLE_WEIGHT
        return w * (self.emb_title[rows] @ qtitle) + (1 - w) * (self.emb_full[rows] @ qfull)

    def pair_similarity(self, rows_a, rows_b):
        """Матриця схожості «відео ↔ відео» (len(a) × len(b)), та сама формула, що й у сервері розширення."""
        w = C.TITLE_WEIGHT
        return w * (self.emb_title[rows_a] @ self.emb_title[rows_b].T) + (1 - w) * (self.emb_full[rows_a] @ self.emb_full[rows_b].T)

    # ------------------------------------------------------------ подання

    def ref(self, i, similarity=None):
        v = self.videos[int(i)]
        out = {
            "video_id": v["video_id"],
            "title": v["title"],
            "channel": v["channel_title"],
            "lang": v["lang"],
            "views": v["views"],
            "published_at": v["published_at"][:10],
            "url": f"https://www.youtube.com/watch?v={v['video_id']}",
        }
        if similarity is not None:
            out["similarity"] = round(float(similarity), 3)
        return out
