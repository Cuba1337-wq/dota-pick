"""Проверка логики данных (нужен интернет для справочников OpenDota): python test_app.py
Расчёт пиков в странице проверяется в браузере: та же формула, что была здесь раньше."""
import json

import app
import build_data

# Подсчёт пар героев на двух выдуманных матчах
fake = {"rows": [{"match_id": 10, "w": True, "r": [1, 2, 3, 4, 5], "d": [6, 7, 8, 9, 10]},
                 {"match_id": 9, "w": False, "r": [1, 6, 11, 12, 13], "d": [2, 14, 15, 16, 17]}]}
calls = []
real_get_json = app.get_json
app.get_json = lambda url, timeout=0: calls.append(url) or (fake if len(calls) == 1 else {"rows": []})
s = app.fetch_stats()
app.get_json = real_get_json
assert s["matches"] == 2
assert s["hg"][1] == 2 and s["hw"][1] == 1  # Anti-Mage: одна победа, одно поражение
assert s["vg"][1][6] == 1 and s["vw"][1][6] == 1  # AM против 6 в первом матче, выиграл
assert s["sg"][1][6] == 1 and s["sw"][1][6] == 0  # AM вместе с 6 во втором, проиграли
assert "match_id < 9" in app.urllib.parse.unquote_plus(calls[1])  # вторая порция начинается со старых матчей

# Сборка по минутам на выдуманных покупках: {предмет: [купили, медианная секунда, купили до старта]}
pop = {"tango": [90, -60, 90], "branches": [80, -50, 80], "magic_wand": [70, 240, 0],
       "blade_mail": [94, 750, 0], "blink": [97, 900, 0], "black_king_bar": [50, 1560, 0],
       "sange": [40, 1000, 0], "sange_and_yasha": [60, 1200, 0],  # Sange — часть S&Y, отдельно не нужен
       "soul_booster": [20, 1700, 0], "mystic_staff": [40, 1500, 0], "ward_observer": [50, 300, 10]}
b = app.timed_build(pop, 100)
core = [x[0] for x in b["core"]]
assert "Tango" in b["start"] and "Iron Branch" in b["start"]
assert core == ["Magic Wand", "Blade Mail", "Blink Dagger", "Sange and Yasha", "Black King Bar"], core  # по минутам
assert "Sange" not in core and "Mystic Staff" not in core and "Observer Ward" not in core
assert all(x[0] != "Soul Booster" for x in b["extra"])
assert [x[1] for x in b["core"]] == sorted(x[1] for x in b["core"])
# ручное правило: Legion Commander — сначала Blink, потом Blade Mail
lc = [x[0] for x in app.timed_build(pop, 100, "Legion Commander")["core"]]
assert lc.index("Blink Dagger") < lc.index("Blade Mail"), lc

# Данные для страницы (общие для программы на ПК и Telegram-виджета)
build_data.main(use_cache=True)
with open(build_data.OUT, encoding="utf-8") as f:
    d = json.load(f)
assert {"heroes", "stats", "games", "builds", "items", "counters", "core_only", "patch", "min_games", "rules"} <= set(d)
m = len(d["stats"]["hg"])
assert all(h["id"] < m and h["attr"] in ("str", "agi", "int", "all") and h["roles"] for h in d["heroes"])
assert all(len(row) == m for row in d["stats"]["vg"])
pudge5 = d["builds"]["14-5"]
assert pudge5["core"] and all(n in d["items"] for n, _, _ in pudge5["core"] + pudge5["extra"])
assert "Legion Commander" in d["rules"]
print("OK")
