"""Готовит site/data.json для Telegram-виджета: всё, что нужно странице, в одном файле.
Запускается в GitHub Actions раз в день. Локально: python build_data.py --cache (берёт stats.json и builds.json)."""
import json
import os
import sys

import app
from bot import heroes, item_build, od
from heroes_data import CORE_ONLY, COUNTER_ITEMS

OUT = os.path.join(app.HERE, "site", "data.json")


def main(use_cache):
    if use_cache:
        app.load_data()  # берёт кэш с диска (и обновит в фоне, если он устарел)
        stats, builds = app.state["stats"], app.state["builds"]
    else:
        stats, builds = app.fetch_stats(), app.fetch_builds()
    assert stats["matches"] >= 1000 and len(builds["games"]) >= 100, "слишком мало данных"

    by_id, _ = heroes()
    m = max(by_id) + 1  # обрезаем таблицы до реальных id героев
    hero_list = app.hero_list()
    for h in hero_list:
        h["roles"] = by_id[h["id"]]["roles"]

    items = od("constants/items")
    img = {it["dname"]: app.CDN + it["img"].split("?")[0] for it in items.values() if it.get("dname") and it.get("img")}
    ready = {}  # готовые сборки «герой-позиция», если игр достаточно
    for key, n in builds["games"].items():
        if n >= app.MIN_GAMES:
            ready[key] = [[t, names] for t, names in item_build(int(key.split("-")[0]), builds["pop"].get(key, {})) if names]
    used = {n for b in ready.values() for _, names in b for n in names} | {n for v in COUNTER_ITEMS.values() for n in v}

    data = {
        "updated": stats["updated"], "matches": stats["matches"], "patch": builds["patch"], "min_games": app.MIN_GAMES,
        "heroes": hero_list,
        "stats": {k: [row[:m] for row in stats[k][:m]] if isinstance(stats[k][0], list) else stats[k][:m]
                  for k in ("hg", "hw", "vg", "vw", "sg", "sw")},
        "games": builds["games"], "builds": ready,
        "items": {n: img.get(n, "") for n in used},
        "counters": COUNTER_ITEMS, "core_only": sorted(CORE_ONLY),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{OUT}: {os.path.getsize(OUT) // 1024} КБ, {len(hero_list)} героев, {len(ready)} сборок, патч {data['patch']}")


if __name__ == "__main__":
    main("--cache" in sys.argv)
