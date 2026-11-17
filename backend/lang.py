"""Визначення мови відео за метаданими. Та сама логіка, що й у розширенні (src/lang.js),
плюс поле defaultAudioLanguage з YouTube API, яке розширенню недоступне.
"""

import re

UK_ONLY = re.compile(r"[іїєґІЇЄҐ]")
RU_ONLY = re.compile(r"[ыэёъЫЭЁЪ]")
WORD = re.compile(r"[\u0400-\u04FF'’ʼ]+")

RU_WORDS = {
    "как", "что", "чтобы", "это", "этот", "эта", "его", "она", "они", "мы", "вы", "ты",
    "или", "если", "только", "когда", "где", "почему", "зачем", "сейчас", "теперь",
    "очень", "тоже", "нет", "меня", "тебя", "себя", "мой", "моя", "свой", "твой",
    "который", "которые", "будет", "было", "был", "были", "сделать", "сегодня",
    "лучший", "лучшие", "самый", "ничего", "хорошо", "лет", "пить", "есть", "можно",
    "нужно", "надо", "всегда", "никогда", "видео", "обзор", "новый", "новые",
    "из", "еще", "чем", "мне", "кто", "куда", "здесь", "всех", "всем", "хуже",
    "лучше", "больше", "меньше", "жизнь", "человек", "деньги", "бесплатно",
}
UK_WORDS = {
    "що", "щоб", "як", "це", "цей", "ця", "ці", "але", "або", "від", "якщо", "коли",
    "чому", "дуже", "зараз", "мене", "буде", "було", "був", "він", "вона", "вони",
    "може", "можна", "треба", "завжди", "ніколи", "найкращий", "огляд", "новий",
    "нові", "сьогодні", "років", "рік", "також", "тільки", "лише", "своїми",
    "хто", "де", "куди", "мені", "ще", "чим", "життя", "людина", "гроші", "краще",
    "гірше", "більше", "менше", "всіх", "безкоштовно",
}
RU_ENDINGS = [
    re.compile(r"[а-я]{2,}ая$"),
    re.compile(r"[а-я]{3,}ия$"),
    re.compile(r"[а-я]{2,}ние$"),
    re.compile(r"[а-я]{2,}[ауяе]ет$"),
    re.compile(r"[а-я]{2,}[ауяе]ют$"),
    re.compile(r"[а-я]{2,}тся$"),
    re.compile(r"[а-я]{3,}ость$"),
]
UK_ENDINGS = [re.compile(r"[а-я]{2,}ння$")]


def detect_text(text: str):
    """Мова тексту: (lang, confidence, source). lang: uk | ru | unknown."""
    text = text or ""
    uk = len(UK_ONLY.findall(text))
    ru = len(RU_ONLY.findall(text))

    # Довгий текст (назва + опис) майже завжди містить «особливі» літери.
    # Одиничні вкраплення іншої мови (цитата, назва) не мають переважувати.
    if uk and uk >= 3 * ru:
        return "uk", 0.9 if uk >= 2 else 0.85, "letters"
    if ru and ru >= 3 * uk:
        return "ru", 0.9 if ru >= 2 else 0.85, "letters"

    ru_w = uk_w = 0
    for w in WORD.findall(text.lower()):
        if w in RU_WORDS or any(r.search(w) for r in RU_ENDINGS):
            ru_w += 1
        if w in UK_WORDS or any(r.search(w) for r in UK_ENDINGS):
            uk_w += 1
    if uk_w and uk_w >= 3 * ru_w:
        return "uk", min(0.95, 0.8 + 0.05 * uk_w), "words"
    if ru_w and ru_w >= 3 * uk_w:
        return "ru", min(0.95, 0.8 + 0.05 * ru_w), "words"
    return "unknown", 0.0, "none"


def video_language(snippet: dict):
    """Мова відео з каталогу: (lang, source).

    1. defaultAudioLanguage — мова мовлення, яку вказав автор.
    2. Текст назви й опису.
    Автори інколи лишають у defaultAudioLanguage неправильне значення за замовчуванням,
    тому якщо там «ru», а текст упевнено український, віримо тексту.
    """
    audio = (snippet.get("defaultAudioLanguage") or "").lower()
    text = (snippet.get("title") or "") + "\n" + (snippet.get("description") or "")[:500]
    text_lang, text_conf, text_src = detect_text(text)

    if audio.startswith("uk"):
        return "uk", "defaultAudioLanguage"
    if audio.startswith("ru"):
        if text_lang == "uk" and text_conf >= 0.85:
            return "uk", "text-over-audio"
        return "ru", "defaultAudioLanguage"
    if audio and audio not in ("zxx", "und", "mul") and not audio.startswith(("uk", "ru")):
        # Наприклад "en": англомовне відео на українському каналі.
        if text_lang == "uk" and text_conf >= 0.9:
            return "uk", "text-over-audio"
        return audio[:2], "defaultAudioLanguage"

    if text_lang != "unknown" and text_conf >= 0.8:
        return text_lang, text_src
    return "unknown", "none"


CYRILLIC = re.compile(r"[\u0400-\u04FF]")
LATIN = re.compile(r"[A-Za-z]")


def source_language(snippet: dict, in_catalog: bool = False):
    """Мова будь-якого відео з YouTube (не лише з каталогу): (lang, confidence, source).

    lang: uk | ru | en | other | unknown. Розширення ховає лише ru з confidence ≥ 0.8,
    тому при суперечливих сигналах краще повернути unknown, ніж помилково сказати ru.
    in_catalog: канал відео є в нашому перевіреному каталозі українських каналів.
    """
    audio = (snippet.get("defaultAudioLanguage") or "").lower()
    title = snippet.get("title") or ""
    text = title + "\n" + (snippet.get("description") or "")[:500]
    t_lang, t_conf, t_src = detect_text(text)
    strong_text = t_lang in ("uk", "ru") and t_conf >= 0.85

    if audio.startswith("uk"):
        if t_lang == "ru" and t_conf >= 0.9:
            return "unknown", 0.5, "conflict"
        return "uk", 0.95, "defaultAudioLanguage"
    if audio.startswith("ru"):
        # Українські автори інколи лишають «ru» в налаштуваннях за замовчуванням.
        if t_lang == "uk" and t_conf >= 0.85:
            return ("uk", 0.85, "catalog") if in_catalog else ("unknown", 0.5, "conflict")
        return "ru", 0.95, "defaultAudioLanguage"
    if audio.startswith("en"):
        # Багато україномовних авторів не міняють мову звуку з англійської за замовчуванням.
        # Український текст важить більше: помилкове «uk» лише сховає панель.
        if t_lang == "uk" and t_conf >= 0.85:
            return "uk", 0.9 if in_catalog else 0.85, "text-over-audio"
        if strong_text:
            return "unknown", 0.5, "conflict"
        return "en", 0.9, "defaultAudioLanguage"
    if audio and audio not in ("zxx", "und", "mul"):
        if strong_text:
            return t_lang, 0.85, t_src
        return "other", 0.9, "defaultAudioLanguage"

    if t_lang != "unknown":
        return t_lang, t_conf, t_src
    if in_catalog:
        return "uk", 0.85, "catalog"
    if LATIN.search(title) and not CYRILLIC.search(text):
        return "en", 0.7, "latin-only"  # нижче порогу: лише підказка
    return "unknown", 0.0, "none"
