"""Пороги й доменні правила Студії. Усі числа тут, щоб їх було видно й легко калібрувати."""

# Схожість текстів (модель e5-small; шкала стиснута, «фон» нерелевантних відео ≈ 0.80–0.83).
TOPIC_MIN_SIMILARITY = 0.80   # тема/ідея ↔ відео: нижня межа, нижче якої відео точно «не про це»
# Відносний поріг: модель e5 вважає тексти однією мовою трохи ближчими незалежно від теми, тож
# україномовна ідея «схожа» майже на всі українські відео. Тому відео рахується «про це», якщо його
# схожість перевищує медіану схожості відео ТІЄЇ Ж МОВИ щонайменше на TOPIC_MARGIN.
TOPIC_MARGIN = 0.02
MIN_FOR_BASELINE = 10         # менше відео мовою — медіана ненадійна, лишається лише нижня межа
COVERED_SIMILARITY = 0.86     # іноземне відео ↔ українське: тема вже покрита українською
CLUSTER_DISTANCE = 0.12       # групування іноземних відео в одну підтему (1 − косинусна схожість)

# Оцінка ідеї: межі рівнів.
DEMAND_HIGH_VIEWS = 1_000_000     # сумарні перегляди схожих іноземних відео за період
DEMAND_MEDIUM_VIEWS = 100_000
COMPETITION_LOW_MAX = 1           # схожих українських відео (за весь час)
COMPETITION_MEDIUM_MAX = 5
FRESH_DAYS = 30                   # «свіже» = опубліковано за останні 30 днів
FRESHNESS_HIGH_SHARE = 0.30
FRESHNESS_MEDIUM_SHARE = 0.10

# Покриття ніші.
MIN_FOREIGN_FOR_VERDICT = 5       # менше іноземних відео — замало даних для висновку
UNDERSERVED_RATIO = 0.10          # українських ≤ 10% від іноземних — ніша недопокрита
SATURATED_RATIO = 0.50            # українських ≥ 50% від іноземних — ніша насичена

# Пакування відео (обмеження YouTube і рекомендації).
TITLE_MAX = 100                   # жорсткий ліміт YouTube
TITLE_SHORT = 20
TITLE_VISIBLE = 70                # довші назви обрізаються в пошуку й рекомендаціях
TAGS_MAX_CHARS = 500              # жорсткий ліміт YouTube на сумарну довжину тегів
HASHTAGS_MAX = 15                 # якщо більше, YouTube ігнорує всі хештеги відео
DESCRIPTION_HEAD = 150            # перші символи опису видно без розгортання


def topic_threshold(sims, floor: float, margin: float):
    """Поріг «відео про цю тему» для відео однієї мови: max(floor, медіана + margin)."""
    import numpy as np

    if len(sims) < MIN_FOR_BASELINE:
        return floor
    return max(floor, float(np.median(sims)) + margin)


def demand_level(total_views: int) -> str:
    if total_views >= DEMAND_HIGH_VIEWS:
        return "high"
    if total_views >= DEMAND_MEDIUM_VIEWS:
        return "medium"
    return "low"


def competition_level(uk_count: int) -> str:
    if uk_count <= COMPETITION_LOW_MAX:
        return "low"
    if uk_count <= COMPETITION_MEDIUM_MAX:
        return "medium"
    return "high"


def freshness_level(fresh_share, has_foreign: bool) -> str:
    if not has_foreign:
        return "unknown"
    if fresh_share >= FRESHNESS_HIGH_SHARE:
        return "high"
    if fresh_share >= FRESHNESS_MEDIUM_SHARE:
        return "medium"
    return "low"


def idea_verdict(demand: str, competition: str, freshness: str) -> str:
    if demand == "low":
        return "weak_demand"
    if competition == "high":
        return "crowded"
    if competition == "low" and demand in ("high", "medium"):
        return "strong_opportunity"
    if demand == "high" and competition == "medium":
        return "worth_trying"
    return "moderate"


def coverage_verdict(uk: int, foreign: int) -> str:
    if foreign < MIN_FOREIGN_FOR_VERDICT:
        return "insufficient_data"
    if uk <= max(1, UNDERSERVED_RATIO * foreign):
        return "underserved"
    if uk >= SATURATED_RATIO * foreign:
        return "saturated"
    return "competitive"
