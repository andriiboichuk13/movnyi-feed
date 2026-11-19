"""Екстракція даних, токен-класифікація та семантичне групування для таблиці wishes.

Використовує:
1. Multilingual NER (`Babelscape/wikineural-multilingual-ner`) для токен-класифікації
   (PER, ORG, LOC, MISC) у назвах відео та побажаннях користувачів.
2. `intfloat/multilingual-e5-small` (спільна модель проєкту) для:
   - класифікації тем і підтем із записом у таблиці `topics` та `wishes`;
   - визначення тональності/наміру `user_text`;
   - агломеративного семантичного групування схожих запитів у контент-кластери.

Запуск синхронізації з CLI:
    .venv/bin/python wishes_pipeline.py --sync
"""

import argparse
import datetime as dt
import difflib
import logging
import re
import threading
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sklearn.cluster import AgglomerativeClustering

import config as C

log = logging.getLogger("movnyi-feed.wishes")

NER_MODEL_NAME = "Babelscape/wikineural-multilingual-ner"

# Ієрархія тем і підтем для українських YouTube-креаторів.
TOPIC_TAXONOMY: List[Dict[str, Any]] = [
    {
        "name": "Ігри та кіберспорт",
        "description": "відеоігри, проходження, стріми, консолі, PlayStation, Xbox, Elden Ring, Dota, CS",
        "subtopics": [
            ("Проходження і стріми", "геймплей, нарізки стрімів, боси, траї, спідрани"),
            ("Огляди ігор та консолей", "огляд гри, PlayStation, Xbox, Nintendo, новинки ігор"),
            ("Кіберспорт та гайди", "турніри, патчі, гайди, білди, рейтингові матчі"),
        ],
    },
    {
        "name": "Кулінарія та їжа",
        "description": "рецепти, приготування їжі, страви, випічка, пельмені, кухня, дегустації",
        "subtopics": [
            ("Домашні рецепти", "як варити, смажити, пекти, покрокові рецепти страв"),
            ("Огляди їжі та закладів", "дегустація, стрітфуд, ресторани, порівняння продуктів"),
        ],
    },
    {
        "name": "Технології та гаджети",
        "description": "смартфони, ноутбуки, комп'ютери, комплектуючі, ШІ, програмування, автотехніка",
        "subtopics": [
            ("Огляди гаджетів", "смартфони, iPhone, ноутбуки, навушники, техніка"),
            ("ПК, залізо та софт", "відеокарти, процесори, збірки ПК, програми, ШІ"),
        ],
    },
    {
        "name": "Освіта та наука",
        "description": "наукпоп, історія, фізика, біологія, математика, мови, пояснення складних тем",
        "subtopics": [
            ("Наукпоп та факти", "космос, фізика, медицина, дослідження, як це працює"),
            ("Історія та культура", "історичні події, біографії, Україна, світова історія"),
        ],
    },
    {
        "name": "Кіно, аніме та серіали",
        "description": "фільми, серіали, аніме, розбори сюжетів, рецензії, трейлери, дубляж",
        "subtopics": [
            ("Розбори та огляди фільмів", "кіноогляд, пояснення фіналу, добірки фільмів"),
            ("Аніме та мультфільми", "аніме, огляди серій, манґа, анімація"),
        ],
    },
    {
        "name": "Авто та транспорт",
        "description": "автомобілі, тест-драйви, ремонт авто, електромобілі, мотоцикли",
        "subtopics": [
            ("Тест-драйви та огляди", "автоогляд, порівняння машин, електрокари"),
            ("Ремонт і тюнінг", "СТО, автосервіс, добірки вживаних авто"),
        ],
    },
    {
        "name": "Спорт та здоров'я",
        "description": "тренування, фітнес, футбол, бокс, харчування, психологія, здоров'я",
        "subtopics": [
            ("Фітнес і тренування", "зал, вправи, схуднення, біг, харчування"),
            ("Спортивні події", "футбол, бокс, ММА, огляди матчів"),
        ],
    },
    {
        "name": "Музика та творчість",
        "description": "пісні, кавери, кліпи, розбір музики, інструменти, малювання, дизайн",
        "subtopics": [
            ("Музика і кавери", "українська музика, кавери, біти, вокал"),
            ("Творчість і DIY", "малювання, хендмейд, дизайн, фото, монтаж"),
        ],
    },
    {
        "name": "Гумор, шоу та влоги",
        "description": "комедія, скетчі, реакції, подкасти, інтерв'ю, подорожі, лайфстайл",
        "subtopics": [
            ("Шоу, подкасти та інтерв'ю", "подкасти, розмови, реакції, розважальні шоу"),
            ("Подорожі та влоги", "подорожі, країни, міста, щоденні влоги"),
        ],
    },
    {
        "name": "Дитячий контент",
        "description": "мультики, дитячі пісні, розвиваючі відео, іграшки, казки для дітей",
        "subtopics": [
            ("Мультики та казки", "анімація для дітей, казки, пісеньки"),
            ("Розвиваючі відео", "абетка, лічба, досліди для дітей"),
        ],
    },
]

SENTIMENT_PROTOTYPES: List[Tuple[str, str]] = [
    ("Позитивний / Хочу ще", "гарний вибір, чудово, супер, дякую, хочу ще, класно, топ"),
    ("Негативний / Не підійшло", "погано, не підходить, жах, неякісно, нерелевантно, не те, марно"),
    ("Запит на тему", "зробіть відео про, хочу українською, бракує контенту, шукаю україномовний канал"),
]

LEVEL_LABELS = {
    0: "Відео вже українське",
    1: "Є точний укр. аналог",
    2: "Шукали на YouTube укр.",
    3: "Є близька укр. тема",
    4: "Знайдено лише англ.",
    5: "Українського відео не знайдено",
    None: "Статус не вказано",
}

_TECH_TOKEN_RE = re.compile(
    r"\b(?:[A-Z][a-zA-Z0-9]+(?:\s+\d{1,3}[A-Za-z]*)?|[A-Z]{2,}[0-9]*)\b"
)
_CYR_CAP_PHRASE_RE = re.compile(r"\b[А-ЯІЇЄҐЁ][а-яіїєґё]{2,}(?:\s+[А-ЯІЇЄҐЁ][а-яіїєґё]{2,}){0,2}\b")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яІіЇїЄєҐґЁё0-9]{3,}")
_STOPWORDS = {
    "как", "это", "моя", "мой", "для", "всех", "що", "який", "яка", "яке",
    "про", "під", "над", "при", "без", "або", "але", "вже", "ще", "тут",
    "там", "коли", "чому", "навіщо", "гарний", "вибір", "хочу", "брат",
    "варить", "правильно", "нарезка", "траев", "этого", "мої", "твоя",
}

_models_lock = threading.Lock()
_embed_model = None
_ner_pipeline = None
_ner_failed = False
_token_cache: Dict[Tuple[str, str, bool], List[Dict[str, Any]]] = {}
_cache_lock = threading.Lock()
_analytics_cache: Dict[Tuple[Any, ...], Tuple[float, Dict[str, Any]]] = {}
ANALYTICS_CACHE_TTL_SECONDS = 180
MAX_WISHES_LIMIT = 200


def set_embed_model(model) -> None:
    """Дозволяє перевикористати вже завантажену модель із server.py."""
    global _embed_model
    with _models_lock:
        if model is not None:
            _embed_model = model


def get_embed_model():
    """Ліниве завантаження спільної ембединг-моделі проєкту."""
    global _embed_model
    with _models_lock:
        if _embed_model is None:
            log.info("Завантажую модель ембедингів %s…", C.MODEL_NAME)
            _embed_model = C.load_model()
        return _embed_model


def get_ner_pipeline():
    """Ліниве завантаження моделі токен-класифікації (з fallback при помилці)."""
    global _ner_pipeline, _ner_failed
    with _models_lock:
        if _ner_failed:
            return None
        if _ner_pipeline is None:
            try:
                from transformers import pipeline

                log.info("Завантажую модель токен-класифікації %s…", NER_MODEL_NAME)
                _ner_pipeline = pipeline(
                    "token-classification",
                    model=NER_MODEL_NAME,
                    aggregation_strategy="simple",
                )
            except Exception as exc:
                log.warning("Не вдалося завантажити NER-модель (%s), використовую евристичний екстрактор.", exc)
                _ner_failed = True
                return None
        return _ner_pipeline


def _prepare_text_for_ner(text: str) -> str:
    """Нормалізує суцільний КАПС, щоб NER краще розпізнавав сутності."""
    return C.normalize_caps((text or "").strip())


def extract_tokens(
    video_title: Optional[str],
    user_text: Optional[str] = None,
    use_ner: bool = True,
) -> List[Dict[str, Any]]:
    """Витягує класифіковані токени/сутності з назви відео й тексту побажання.

    Повертає список словників: {token, entity_group, score, source}.
    """
    cache_key = ((video_title or "").strip(), (user_text or "").strip(), use_ner)
    if cache_key in _token_cache:
        return _token_cache[cache_key]

    ner = get_ner_pipeline() if use_ner else None
    results: List[Dict[str, Any]] = []
    seen = set()

    for source_name, raw_text in (("title", video_title), ("wish", user_text)):
        cleaned = _prepare_text_for_ner(raw_text or "")
        if not cleaned:
            continue

        # Важкий BERT-NER запускаємо для назв відео (де містяться іменовані сутності).
        if ner is not None and source_name == "title":
            try:
                entities = ner(cleaned[:400])
                for ent in entities:
                    word = (ent.get("word") or "").strip(" ,.!?:;\"'()[]«»")
                    if len(word) < 2 or word.lower() in _STOPWORDS:
                        continue
                    group = str(ent.get("entity_group") or "MISC")
                    key = (word.lower(), group)
                    if key not in seen:
                        seen.add(key)
                        results.append({
                            "token": word,
                            "entity_group": group,
                            "score": round(float(ent.get("score") or 0.0), 3),
                            "source": source_name,
                        })
            except Exception as exc:
                log.debug("NER inference failed: %s", exc)

        # Додатковий екстрактор технічних/продуктових токенів (PlayStation 5, PS5, RTX 4090 тощо).
        for match in _TECH_TOKEN_RE.finditer(raw_text or ""):
            token = match.group(0).strip()
            if len(token) < 2 or token.lower() in _STOPWORDS:
                continue
            if not any(t["token"].lower() == token.lower() for t in results):
                seen.add((token.lower(), "TECH"))
                results.append({
                    "token": token,
                    "entity_group": "TECH",
                    "score": 0.9,
                    "source": source_name,
                })

        # Fallback, якщо NER недоступний або нічого не знайшов у тексті.
        if ner is None:
            for match in _CYR_CAP_PHRASE_RE.finditer(cleaned):
                token = match.group(0).strip()
                if len(token) >= 3 and token.lower() not in _STOPWORDS:
                    key = (token.lower(), "KEY")
                    if key not in seen:
                        seen.add(key)
                        results.append({
                            "token": token,
                            "entity_group": "KEY",
                            "score": 0.75,
                            "source": source_name,
                        })

    # Якщо зовсім нічого не знайдено (напр. суцільний капс без іменованих сутностей),
    # беремо найбільш інформативні слова як KEY-токени.
    if not results and video_title:
        for w in _WORD_RE.findall((video_title or "").lower()):
            if w not in _STOPWORDS and not w.isdigit():
                results.append({
                    "token": w,
                    "entity_group": "KEY",
                    "score": 0.65,
                    "source": "title",
                })
                if len(results) >= 3:
                    break

    _token_cache[cache_key] = results
    return results


def _encode_texts(texts: List[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, C.EMBED_DIM), dtype=np.float32)
    model = get_embed_model()
    prefixed = [f"{C.EMBED_PREFIX}{C.normalize_caps(t)}" for t in texts]
    vecs = model.encode(prefixed, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vecs, dtype=np.float32)


def _get_topic_embeddings() -> Tuple[List[Dict[str, Any]], np.ndarray]:
    """Готує плоский список (topic_name, subtopic_name, text) та їх ембединги."""
    entries: List[Dict[str, Any]] = []
    texts: List[str] = []
    for topic in TOPIC_TAXONOMY:
        t_name = topic["name"]
        t_desc = topic["description"]
        for sub_name, sub_desc in topic["subtopics"]:
            entries.append({
                "topic_name": t_name,
                "subtopic_name": sub_name,
            })
            texts.append(f"{t_name}: {sub_name}. {t_desc}. {sub_desc}")
    return entries, _encode_texts(texts)


def ensure_topics_seeded(conn) -> Tuple[Dict[str, int], Dict[Tuple[str, str], int]]:
    """Гарантує наявність таксономії тем і підтем у таблиці `topics`."""
    existing = conn.execute("select id, name, parent_id from topics").fetchall()
    topic_map: Dict[str, int] = {}
    subtopic_map: Dict[Tuple[str, str], int] = {}
    id_to_name: Dict[int, str] = {}

    for tid, name, parent_id in existing:
        id_to_name[tid] = name
        if parent_id is None:
            topic_map[name] = tid

    for tid, name, parent_id in existing:
        if parent_id is not None and parent_id in id_to_name:
            subtopic_map[(id_to_name[parent_id], name)] = tid

    for topic in TOPIC_TAXONOMY:
        t_name = topic["name"]
        if t_name not in topic_map:
            row = conn.execute(
                "insert into topics (name, parent_id, description) values (%s, null, %s) returning id",
                (t_name, topic["description"]),
            ).fetchone()
            topic_map[t_name] = row[0]
        parent_id = topic_map[t_name]
        for sub_name, sub_desc in topic["subtopics"]:
            if (t_name, sub_name) not in subtopic_map:
                row = conn.execute(
                    "insert into topics (name, parent_id, description) values (%s, %s, %s) returning id",
                    (sub_name, parent_id, sub_desc),
                ).fetchone()
                subtopic_map[(t_name, sub_name)] = row[0]

    return topic_map, subtopic_map


def classify_wish_topics(
    texts: List[str],
) -> Tuple[List[Dict[str, Any]], np.ndarray]:
    """Класифікує список текстів побажань за темами/підтемами.

    Повертає (список {topic_name, subtopic_name, confidence}, матриця ембедингів побажань).
    """
    if not texts:
        return [], np.zeros((0, C.EMBED_DIM), dtype=np.float32)

    wish_vecs = _encode_texts(texts)
    entries, topic_vecs = _get_topic_embeddings()
    sims = wish_vecs @ topic_vecs.T

    classifications: List[Dict[str, Any]] = []
    for row_sims in sims:
        best_idx = int(np.argmax(row_sims))
        raw_sim = float(row_sims[best_idx])
        # Переводимо косинусну схожість e5 (зазвичай 0.72–0.92) у зрозумілу шкалу впевненості [0, 1].
        confidence = float(np.clip((raw_sim - 0.70) / 0.22, 0.05, 0.99))
        classifications.append({
            "topic_name": entries[best_idx]["topic_name"],
            "subtopic_name": entries[best_idx]["subtopic_name"],
            "confidence": round(confidence, 3),
        })
    return classifications, wish_vecs


def classify_sentiments(user_texts: List[Optional[str]]) -> List[str]:
    """Класифікує тональність / намір поля `user_text`."""
    non_empty_indices = [i for i, t in enumerate(user_texts) if (t or "").strip()]
    results = ["Без коментаря"] * len(user_texts)
    if not non_empty_indices:
        return results

    proto_labels = [p[0] for p in SENTIMENT_PROTOTYPES]
    proto_vecs = _encode_texts([p[1] for p in SENTIMENT_PROTOTYPES])
    text_vecs = _encode_texts([(user_texts[i] or "").strip() for i in non_empty_indices])
    sims = text_vecs @ proto_vecs.T

    for idx_pos, orig_idx in enumerate(non_empty_indices):
        best = int(np.argmax(sims[idx_pos]))
        results[orig_idx] = proto_labels[best]
    return results


def _is_unmet(level_reached: Optional[int]) -> bool:
    """Рівні 4–5 (або невідомо) означають, що україномовного відео не знайдено."""
    return level_reached is None or level_reached in (4, 5)


def ensure_wishes_columns(conn) -> None:
    """Додає колонки для збереження NER-сутностей і тональності, якщо їх ще немає."""
    conn.execute("alter table if exists wishes add column if not exists extracted_tokens jsonb")
    conn.execute("alter table if exists wishes add column if not exists sentiment text")


def invalidate_analytics_cache() -> None:
    with _cache_lock:
        _analytics_cache.clear()


def group_wishes_semantically(
    wish_records: List[Dict[str, Any]],
    wish_vecs: np.ndarray,
    distance_threshold: float = 0.12,
) -> List[Dict[str, Any]]:
    """Групує схожі побажання у семантичні кластери для контент-ідей креаторів."""
    n = len(wish_records)
    if n == 0:
        return []

    if n == 1:
        labels = [0]
    else:
        clustering = AgglomerativeClustering(
            n_clusters=None,
            metric="cosine",
            linkage="complete",
            distance_threshold=distance_threshold,
        )
        labels = clustering.fit_predict(wish_vecs).tolist()

    grouped: Dict[int, List[int]] = defaultdict(list)
    for idx, cid in enumerate(labels):
        grouped[int(cid)].append(idx)
        wish_records[idx]["cluster_id"] = int(cid)

    clusters: List[Dict[str, Any]] = []
    for cid, indices in grouped.items():
        items = [wish_records[i] for i in indices]
        topic_counts = Counter(it["topic_name"] for it in items if it.get("topic_name"))
        top_topic = topic_counts.most_common(1)[0][0] if topic_counts else "Різне"

        token_counts = Counter()
        for it in items:
            for tok in it.get("tokens", []):
                token_counts[tok["token"]] += 1

        top_tokens = [t for t, _ in token_counts.most_common(4)]
        if top_tokens:
            cluster_label = f"{top_topic}: {', '.join(top_tokens[:3])}"
        else:
            first_title = (items[0].get("video_title") or "Запит без назви").strip()
            cluster_label = first_title[:55] + ("…" if len(first_title) > 55 else "")

        unmet = sum(1 for it in items if _is_unmet(it.get("level_reached")))
        avg_conf = round(
            float(np.mean([it["topic_confidence"] for it in items if it.get("topic_confidence") is not None] or [0.0])),
            3,
        )
        clusters.append({
            "cluster_id": cid,
            "label": cluster_label,
            "topic_name": top_topic,
            "count": len(items),
            "unmet_count": unmet,
            "avg_confidence": avg_conf,
            "top_tokens": top_tokens,
            "sample_wishes": [
                {
                    "id": it["id"],
                    "video_id": it["video_id"],
                    "video_title": it["video_title"],
                    "has_user_text": it["has_user_text"],
                    "level_reached": it["level_reached"],
                }
                for it in items[:5]
            ],
        })

    clusters.sort(key=lambda c: (-c["count"], -c["unmet_count"], c["cluster_id"]))
    return clusters


def process_unprocessed_wishes(conn, force: bool = False) -> int:
    """Класифікує необроблені (або всі, якщо force=True) рядки `wishes` і записує в Supabase."""
    from psycopg.types.json import Jsonb

    ensure_wishes_columns(conn)
    topic_map, subtopic_map = ensure_topics_seeded(conn)
    where_clause = "" if force else "where processed_at is null or topic_id is null or extracted_tokens is null"
    rows = conn.execute(
        f"select id, video_title, user_text from wishes {where_clause} order by id asc"
    ).fetchall()

    if not rows:
        return 0

    texts = [f"{r[1] or ''} {r[2] or ''}".strip() or "невідома тема" for r in rows]
    classifications, _ = classify_wish_topics(texts)
    sentiments = classify_sentiments([r[2] for r in rows])
    now = dt.datetime.now(dt.timezone.utc)

    for (wish_id, title, user_text), cls, sent in zip(rows, classifications, sentiments):
        t_id = topic_map.get(cls["topic_name"])
        s_id = subtopic_map.get((cls["topic_name"], cls["subtopic_name"]))
        tokens = extract_tokens(title, user_text, use_ner=True)
        conn.execute(
            """update wishes
               set topic_id = %s,
                   subtopic_id = %s,
                   topic_confidence = %s,
                   extracted_tokens = %s,
                   sentiment = %s,
                   processed_at = %s
               where id = %s""",
            (t_id, s_id, cls["confidence"], Jsonb(tokens), sent, now, wish_id),
        )
    invalidate_analytics_cache()
    return len(rows)


def find_closest_topic_syntactic(
    query_text: str,
    topics: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Знаходить топік, назва якого синтаксично найближча до пошукового запиту."""
    q = (query_text or "").strip().lower()
    if not q or not topics:
        return None

    best_topic: Optional[Dict[str, Any]] = None
    best_score = -1.0
    q_words = [w for w in re.split(r"\s+", q) if w]

    for topic in topics:
        name = (topic.get("name") or "").strip()
        name_low = name.lower()
        if not name_low:
            continue
        full_ratio = difflib.SequenceMatcher(None, q, name_low).ratio()
        name_words = [w for w in re.split(r"[\s,·—-]+", name_low) if len(w) >= 2]
        word_ratio = max(
            (
                difflib.SequenceMatcher(None, qw, nw).ratio()
                for qw in q_words
                for nw in name_words
            ),
            default=0.0,
        )
        score = max(full_ratio, word_ratio)
        if score > best_score:
            best_score = score
            best_topic = {
                "id": topic["id"],
                "name": name,
                "similarity": round(score, 3),
            }

    return best_topic


def _fetch_wishes_rows(
    conn,
    days: Optional[int] = None,
    topic_id: Optional[int] = None,
    level: Optional[int] = None,
    query: Optional[str] = None,
):
    conditions: List[str] = []
    params: List[Any] = []

    if days is not None and days > 0:
        conditions.append("w.created_at >= now() - (%s * interval '1 day')")
        params.append(days)
    if topic_id is not None:
        conditions.append("w.topic_id = %s")
        params.append(topic_id)
    if level is not None:
        conditions.append("w.level_reached = %s")
        params.append(level)
    if query and query.strip():
        like = f"%{query.strip()}%"
        conditions.append("w.video_title ilike %s")
        params.append(like)

    where_sql = f"where {' and '.join(conditions)}" if conditions else ""

    return conn.execute(
        f"""
        select
            w.id,
            w.created_at,
            w.install_id,
            w.video_id,
            w.video_title,
            w.user_text,
            w.level_reached,
            w.topic_id,
            t.name as topic_name,
            w.subtopic_id,
            st.name as subtopic_name,
            w.topic_confidence,
            w.processed_at,
            w.extracted_tokens,
            w.sentiment
        from wishes w
        left join topics t on t.id = w.topic_id
        left join topics st on st.id = w.subtopic_id
        {where_sql}
        order by w.created_at desc
        limit {MAX_WISHES_LIMIT}
        """,
        params,
    ).fetchall()


def build_creators_analytics(
    conn,
    days: Optional[int] = None,
    topic_id: Optional[int] = None,
    level: Optional[int] = None,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    """Збирає агреговану аналітику по таблиці `wishes` для вебсайту креаторів."""
    cache_key = (days, topic_id, level, (query or "").strip().lower())
    now_ts = time.monotonic()
    with _cache_lock:
        cached = _analytics_cache.get(cache_key)
        if cached and (now_ts - cached[0]) < ANALYTICS_CACHE_TTL_SECONDS:
            return cached[1]

    # Отримуємо доступні теми для фільтру у UI та синтаксичного fallback-пошуку.
    available_topics = [
        {"id": r[0], "name": r[1]}
        for r in conn.execute("select id, name from topics where parent_id is null order by name").fetchall()
    ]

    rows = _fetch_wishes_rows(conn, days=days, topic_id=topic_id, level=level, query=query)
    search_suggestion: Optional[Dict[str, Any]] = None

    if not rows and query and query.strip():
        closest = find_closest_topic_syntactic(query, available_topics)
        if closest is not None:
            fallback_rows = _fetch_wishes_rows(
                conn,
                days=days,
                topic_id=closest["id"],
                level=level,
                query=None,
            )
            if fallback_rows:
                rows = fallback_rows
                search_suggestion = {
                    "query": query.strip(),
                    "matched_topic_id": closest["id"],
                    "matched_topic_name": closest["name"],
                    "similarity": closest["similarity"],
                }

    if not rows:
        result = {
            "summary": {
                "total_wishes": 0,
                "unique_installs": 0,
                "processed_wishes": 0,
                "unmet_demand": 0,
                "with_user_text": 0,
                "top_topic": None,
                "avg_confidence": 0.0,
            },
            "search_suggestion": search_suggestion,
            "available_topics": available_topics,
            "topics": [],
            "clusters": [],
            "tokens": [],
            "sentiments": [],
            "levels": [],
            "timeline": [],
            "wishes": [],
        }
        with _cache_lock:
            _analytics_cache[cache_key] = (now_ts, result)
        return result

    texts_for_model = [f"{r[4] or ''} {r[5] or ''}".strip() or "невідома тема" for r in rows]
    wish_vecs = _encode_texts(texts_for_model)

    unprocessed_indices = [i for i, r in enumerate(rows) if r[8] is None]
    live_cls_map: Dict[int, Dict[str, Any]] = {}
    if unprocessed_indices:
        live_cls, _ = classify_wish_topics([texts_for_model[i] for i in unprocessed_indices])
        for i, cls in zip(unprocessed_indices, live_cls):
            live_cls_map[i] = cls

    unsentiment_indices = [i for i, r in enumerate(rows) if r[14] is None]
    live_sent_map: Dict[int, str] = {}
    if unsentiment_indices:
        live_sents = classify_sentiments([rows[i][5] for i in unsentiment_indices])
        for i, s in zip(unsentiment_indices, live_sents):
            live_sent_map[i] = s

    wish_records: List[Dict[str, Any]] = []
    unique_installs = set()

    for idx, r in enumerate(rows):
        (
            w_id,
            created_at,
            install_id,
            video_id,
            video_title,
            user_text,
            level_reached,
            t_id,
            t_name,
            st_id,
            st_name,
            t_conf,
            processed_at,
            db_tokens,
            db_sentiment,
        ) = r
        unique_installs.add(install_id)

        # Якщо запис ще не оброблено у БД, показуємо легкий fallback без завантаження NER.
        effective_topic = t_name or live_cls_map[idx]["topic_name"]
        effective_subtopic = st_name or live_cls_map[idx]["subtopic_name"]
        effective_conf = round(float(t_conf), 3) if t_conf is not None else live_cls_map[idx]["confidence"]
        tokens = db_tokens if isinstance(db_tokens, list) else extract_tokens(video_title, user_text, use_ner=False)
        sentiment = db_sentiment or live_sent_map.get(idx, "Без коментаря")

        wish_records.append({
            "id": w_id,
            "created_at": created_at.isoformat() if created_at else None,
            "date": created_at.date().isoformat() if created_at else None,
            "video_id": video_id,
            "video_title": video_title,
            "has_user_text": bool((user_text or "").strip()),
            "level_reached": level_reached,
            "level_label": LEVEL_LABELS.get(level_reached, "Статус не вказано"),
            "topic_id": t_id,
            "topic_name": effective_topic,
            "subtopic_id": st_id,
            "subtopic_name": effective_subtopic,
            "topic_confidence": effective_conf,
            "processed_at": processed_at.isoformat() if processed_at else None,
            "sentiment": sentiment,
            "tokens": tokens,
            "_install_id": install_id,
        })

    clusters = group_wishes_semantically(wish_records, wish_vecs)

    # Агрегація по темах, підтемах, наративах і ключових об'єктах інтересу.
    topic_agg: Dict[str, Dict[str, Any]] = {}
    for w in wish_records:
        t_name = w["topic_name"]
        if t_name not in topic_agg:
            topic_agg[t_name] = {
                "id": w["topic_id"],
                "name": t_name,
                "count": 0,
                "unmet_count": 0,
                "conf_sum": 0.0,
                "viewers": set(),
                "subtopics": Counter(),
                "sentiments": Counter(),
                "subjects": Counter(),
                "items": [],
            }
        entry = topic_agg[t_name]
        entry["count"] += 1
        entry["viewers"].add(w["_install_id"])
        if _is_unmet(w["level_reached"]):
            entry["unmet_count"] += 1
        entry["conf_sum"] += w["topic_confidence"] or 0.0
        if w["subtopic_name"]:
            entry["subtopics"][w["subtopic_name"]] += 1
        entry["sentiments"][w["sentiment"]] += 1
        seen_tok = set()
        for tok in w["tokens"]:
            name_clean = (tok.get("token") or "").strip()
            if name_clean and name_clean.lower() not in seen_tok:
                seen_tok.add(name_clean.lower())
                entry["subjects"][name_clean] += 1
        entry["items"].append(w)

    total = len(wish_records)
    max_topic_cnt = max((e["count"] for e in topic_agg.values()), default=1)
    topics_list = []
    for entry in topic_agg.values():
        cnt = entry["count"]
        unmet_rate = round(entry["unmet_count"] / cnt * 100, 1)
        opportunity_score = round(min(100.0, (cnt / max_topic_cnt) * 55.0 + unmet_rate * 0.45))

        # Групуємо запити всередині теми по наративах (за кластером + підтемою).
        nar_groups: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for it in entry["items"]:
            nar_groups[int(it.get("cluster_id") or 0)].append(it)

        narratives = []
        for nar_items in nar_groups.values():
            n_cnt = len(nar_items)
            n_unmet = sum(1 for it in nar_items if _is_unmet(it["level_reached"]))
            n_sub = Counter(it["subtopic_name"] for it in nar_items if it.get("subtopic_name")).most_common(1)
            sub_label = n_sub[0][0] if n_sub else entry["name"]
            n_sent = Counter(it["sentiment"] for it in nar_items).most_common(1)
            dominant_intent = n_sent[0][0] if n_sent else "Без коментаря"

            subj_cnt = Counter()
            for it in nar_items:
                for tok in it["tokens"]:
                    t_str = (tok.get("token") or "").strip()
                    if t_str:
                        subj_cnt[t_str] += 1
            top_subjs = [s for s, _ in subj_cnt.most_common(4)]
            if top_subjs:
                nar_title = f"{sub_label}: {', '.join(top_subjs[:3])}"
            else:
                sample_t = (nar_items[0].get("video_title") or sub_label).strip()
                nar_title = sample_t[:60] + ("…" if len(sample_t) > 60 else "")

            seen_vids = set()
            sample_videos = []
            for it in nar_items:
                vid = it["video_id"]
                if vid not in seen_vids:
                    seen_vids.add(vid)
                    sample_videos.append({
                        "video_id": vid,
                        "video_title": it["video_title"],
                    })
                if len(sample_videos) >= 3:
                    break

            narratives.append({
                "title": nar_title,
                "subtopic": sub_label,
                "demand": n_cnt,
                "unmet_rate": round(n_unmet / n_cnt * 100, 1),
                "viewer_intent": dominant_intent,
                "subjects": top_subjs,
                "sample_videos": sample_videos,
            })
        narratives.sort(key=lambda x: (-x["demand"], -x["unmet_rate"]))

        topics_list.append({
            "id": entry["id"],
            "name": entry["name"],
            "count": cnt,
            "share": round(cnt / total * 100, 1),
            "unique_viewers": len(entry["viewers"]),
            "unmet_count": entry["unmet_count"],
            "unmet_rate": unmet_rate,
            "opportunity_score": opportunity_score,
            "avg_confidence": round(entry["conf_sum"] / cnt, 3),
            "subtopics": [
                {"name": s_name, "count": s_cnt, "share": round(s_cnt / cnt * 100, 1)}
                for s_name, s_cnt in entry["subtopics"].most_common()
            ],
            "key_subjects": [
                {"name": s_name, "count": s_cnt}
                for s_name, s_cnt in entry["subjects"].most_common(8)
            ],
            "sentiments": [
                {"label": s_lbl, "count": s_cnt, "share": round(s_cnt / cnt * 100, 1)}
                for s_lbl, s_cnt in entry["sentiments"].most_common()
            ],
            "narratives": narratives,
        })
    topics_list.sort(key=lambda x: (-x["count"], -x["opportunity_score"]))

    for w in wish_records:
        w.pop("_install_id", None)

    # Агрегація токенів / іменованих сутностей.
    token_agg: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for w in wish_records:
        for tok in w["tokens"]:
            key = (tok["token"].lower(), tok["entity_group"])
            if key not in token_agg:
                token_agg[key] = {
                    "token": tok["token"],
                    "entity_group": tok["entity_group"],
                    "count": 0,
                    "score_sum": 0.0,
                    "topics": Counter(),
                    "wish_ids": [],
                }
            agg = token_agg[key]
            agg["count"] += 1
            agg["score_sum"] += tok["score"]
            agg["topics"][w["topic_name"]] += 1
            if w["id"] not in agg["wish_ids"]:
                agg["wish_ids"].append(w["id"])

    tokens_list = [
        {
            "token": v["token"],
            "entity_group": v["entity_group"],
            "count": v["count"],
            "avg_score": round(v["score_sum"] / v["count"], 3),
            "topics": [t for t, _ in v["topics"].most_common(3)],
            "wish_ids": v["wish_ids"][:10],
        }
        for v in token_agg.values()
    ]
    tokens_list.sort(key=lambda x: (-x["count"], -x["avg_score"], x["token"]))

    # Тональність / намір user_text.
    sent_counts = Counter(w["sentiment"] for w in wish_records)
    sentiments_list = [
        {"label": label, "count": cnt, "share": round(cnt / total * 100, 1)}
        for label, cnt in sent_counts.most_common()
    ]

    # Розподіл за рівнями альтернатив.
    lvl_counts = Counter(w["level_reached"] for w in wish_records)
    levels_list = [
        {
            "level": lvl if lvl is not None else -1,
            "label": LEVEL_LABELS.get(lvl, f"Рівень {lvl}"),
            "count": cnt,
            "share": round(cnt / total * 100, 1),
        }
        for lvl, cnt in sorted(lvl_counts.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))
    ]

    # Часовий ряд по днях.
    day_buckets: Dict[str, Dict[str, int]] = defaultdict(lambda: {"count": 0, "unmet_count": 0})
    for w in wish_records:
        if w["date"]:
            day_buckets[w["date"]]["count"] += 1
            if _is_unmet(w["level_reached"]):
                day_buckets[w["date"]]["unmet_count"] += 1
    timeline_list = [
        {"date": d, "count": v["count"], "unmet_count": v["unmet_count"]}
        for d, v in sorted(day_buckets.items())
    ]

    processed_count = sum(1 for w in wish_records if w["processed_at"] is not None)
    unmet_demand = sum(1 for w in wish_records if _is_unmet(w["level_reached"]))
    with_user_text = sum(1 for w in wish_records if w["has_user_text"])
    avg_confidence = round(float(np.mean([w["topic_confidence"] for w in wish_records])), 3)

    result = {
        "summary": {
            "total_wishes": total,
            "unique_installs": len(unique_installs),
            "processed_wishes": processed_count,
            "unmet_demand": unmet_demand,
            "with_user_text": with_user_text,
            "top_topic": topics_list[0]["name"] if topics_list else None,
            "avg_confidence": avg_confidence,
        },
        "search_suggestion": search_suggestion,
        "available_topics": available_topics,
        "topics": topics_list,
        "clusters": clusters,
        "tokens": tokens_list[:40],
        "sentiments": sentiments_list,
        "levels": levels_list,
        "timeline": timeline_list,
        "wishes": wish_records,
    }
    with _cache_lock:
        _analytics_cache[cache_key] = (now_ts, result)
    return result


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Класифікація і групування таблиці wishes для креаторів")
    parser.add_argument("--sync", action="store_true", help="Обробити необроблені записи wishes і оновити Supabase")
    parser.add_argument("--force", action="store_true", help="Перекласифікувати всі записи wishes заново")
    args = parser.parse_args()

    with C.connect_db() as conn:
        if args.sync or args.force:
            updated = process_unprocessed_wishes(conn, force=args.force)
            log.info("Оброблено записів wishes: %d", updated)
        analytics = build_creators_analytics(conn)
        log.info(
            "Готово: %d побажань, %d тем, %d кластерів, %d токенів",
            analytics["summary"]["total_wishes"],
            len(analytics["topics"]),
            len(analytics["clusters"]),
            len(analytics["tokens"]),
        )


if __name__ == "__main__":
    main()
