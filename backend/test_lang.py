"""Тести backend/lang.py. Запуск з папки backend:  python -m pytest tests -v
Потрібен лише pytest (pip install pytest); бази, ключа YouTube і моделі не треба.
Перевірка «паритету» ще й запускає node, щоб порівняти з extension/src/lang.js (пропускається без node)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lang  # noqa: E402

THRESHOLD = 0.8


def hidden(snippet, in_catalog=False):
    l, c, _ = lang.source_language(snippet, in_catalog)
    return l == "ru" and c >= THRESHOLD


# ---------------------------------------------------------------- detect_text
@pytest.mark.parametrize("text,expected", [
    ("Что будет, если смешать колу и ментос", "ru"),
    ("Як приготувати борщ", "uk"),
    ("Їжак у лісі", "uk"),
    ("КАК Я БРОСИЛ ПИТЬ", "ru"),
    ("How to fix a laptop", "unknown"),
    ("Ремонт ноутбука", "unknown"),
    ("", "unknown"),
])
def test_detect_text(text, expected):
    assert lang.detect_text(text)[0] == expected


def test_single_foreign_letter_in_long_text_does_not_flip_language():
    # Довгий український опис із однією російською цитатою не повинен стати російським.
    text = "Як ми готували борщ у селі, і що з цього вийшло. Дуже смачно! Цитата: «Эх, хорошо»."
    assert lang.detect_text(text)[0] == "uk"


# ---------------------------------------------------------------- source_language: що ховаємо
def test_audio_ru_with_ru_text_is_hidden():
    assert hidden({"defaultAudioLanguage": "ru", "title": "Обзор телефона"})


def test_audio_ru_but_ukrainian_text_is_not_hidden_for_unknown_channel():
    # Автор лишив "ru" за замовчуванням, текст упевнено український: не ховаємо.
    s = {"defaultAudioLanguage": "ru", "title": "Як я навчився програмувати", "description": "Дуже корисне відео, що я зробив"}
    assert not hidden(s)


def test_audio_uk_with_ru_text_conflict_is_unknown_not_hidden():
    s = {"defaultAudioLanguage": "uk", "title": "Эксперимент: это было ужасно", "description": "Вы не поверите, что здесь было"}
    assert lang.source_language(s)[0] == "unknown"
    assert not hidden(s)


def test_audio_en_is_never_hidden():
    assert not hidden({"defaultAudioLanguage": "en", "title": "How to fix a laptop"})


def test_english_title_without_audio_is_not_hidden():
    assert not hidden({"title": "MrBeast $1 vs $1,000,000 hotel"})


def test_catalog_channel_without_signals_is_uk():
    assert lang.source_language({"title": "Ремонт ноутбука"}, in_catalog=True)[0] == "uk"


def test_zxx_und_mul_audio_falls_back_to_text():
    assert hidden({"defaultAudioLanguage": "zxx", "title": "ПОРТУГАЛИЯ УМИРАЕТ"})
    assert not hidden({"defaultAudioLanguage": "und", "title": "Ремонт ноутбука"})


# ---------------------------------------------------------------- audio=en, але текст український (нове правило)
UK_TEXT = {"title": "Як приготувати борщ: рецепт від бабусі", "description": "Дуже смачний борщ, який я готую вже багато років"}


def test_english_audio_default_with_ukrainian_text_is_uk_not_en():
    l, c, src = lang.source_language({"defaultAudioLanguage": "en", **UK_TEXT})
    assert l == "uk" and c >= THRESHOLD and src == "text-over-audio"


def test_english_audio_ukrainian_text_catalog_channel_is_more_confident():
    plain = lang.source_language({"defaultAudioLanguage": "en", **UK_TEXT}, in_catalog=False)[1]
    catalog = lang.source_language({"defaultAudioLanguage": "en", **UK_TEXT}, in_catalog=True)[1]
    assert catalog > plain


def test_english_audio_english_text_stays_en():
    assert lang.source_language({"defaultAudioLanguage": "en", "title": "How to fix a laptop"})[0] == "en"


def test_english_audio_russian_text_is_unknown_not_hidden():
    s = {"defaultAudioLanguage": "en", "title": "Эксперимент: это было ужасно", "description": "Вы не поверите, что здесь было"}
    assert lang.source_language(s)[0] == "unknown"
    assert not hidden(s)


@pytest.mark.xfail(reason="P3: одна українська літера в англійському тексті дає uk 0.85, а поріг text-over-audio теж 0.85 → англомовне відео вважається українським і панель не показується", strict=True)
def test_known_single_ukrainian_letter_does_not_flip_english_video():
    assert lang.source_language({"defaultAudioLanguage": "en", "title": "Top 10 їжак facts"})[0] == "en"


# ---------------------------------------------------------------- відомі проблеми (xfail = видно у звіті, не валить)
@pytest.mark.xfail(reason="«моя» є в RU_WORDS, але це й українське слово → українське відео без audio-мови ховається", strict=True)
def test_known_moya_is_not_russian():
    assert not hidden({"title": "Моя перша зарплата"})


@pytest.mark.xfail(reason="«ъ» є і в болгарській: болгарське відео вважається російським", strict=True)
def test_known_bulgarian_not_russian():
    assert not hidden({"title": "Български език за начинаещи"})


# ---------------------------------------------------------------- паритет з розширенням
TITLES = [
    "Что будет, если смешать колу и ментос", "КАК Я БРОСИЛ ПИТЬ", "Як приготувати борщ", "Їжак у лісі",
    "ПОРТУГАЛИЯ УМИРАЕТ", "Навчання програмування", "Решение задачи", "How to fix a laptop", "Ремонт ноутбука",
    "Гороскоп на неделю", "Огляд ноутбука Lenovo", "Моя країна", "Что це таке",
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node не знайдено")
def test_parity_with_extension_on_simple_titles():
    """Для коротких назв (без довгого опису) клієнт і сервер мають давати той самий висновок про ru/uk."""
    ext = Path(__file__).resolve().parents[2] / "extension" / "src" / "lang.js"
    js = f"const L=require({json.dumps(str(ext))});const t={json.dumps(TITLES)};" \
         "console.log(JSON.stringify(t.map(x=>{const r=L.quick(x);return r&&r.lang!=='unknown'&&r.confidence>=0.8?r.lang:'none'})))"
    out = json.loads(subprocess.check_output(["node", "-e", js], text=True, encoding="utf-8"))
    diffs = []
    for title, client in zip(TITLES, out):
        l, c, _ = lang.detect_text(title)
        server = l if l != "unknown" and c >= THRESHOLD else "none"
        if server != client:
            diffs.append((title, client, server))
    assert not diffs, f"клієнт і сервер розходяться (клієнт, сервер): {diffs}"
