"""Проверка логики программы без окна (нужен интернет): python test_app.py"""
import json

import app

# Подсчёт пар героев на двух выдуманных матчах
fake = {"rows": [{"match_id": 10, "w": True, "r": [1, 2, 3, 4, 5], "d": [6, 7, 8, 9, 10]},
                 {"match_id": 9, "w": False, "r": [1, 6, 11, 12, 13], "d": [2, 14, 15, 16, 17]}]}
calls = []
app.get_json = lambda url, timeout=0: calls.append(url) or (fake if len(calls) == 1 else {"rows": []})
s = app.fetch_stats()
assert s["matches"] == 2
assert s["hg"][1] == 2 and s["hw"][1] == 1  # Anti-Mage: одна победа, одно поражение
assert s["vg"][1][6] == 1 and s["vw"][1][6] == 1  # AM против 6 в первом матче, выиграл
assert s["sg"][1][6] == 1 and s["sw"][1][6] == 0  # AM вместе с 6 во втором, проиграли
assert s["vg"][2][1] == 1 and s["vw"][2][1] == 1  # 2 против AM во втором матче, выиграл
assert "match_id < 9" in app.urllib.parse.unquote_plus(calls[1])  # вторая порция начинается со старых матчей

# Реальные данные: подсказки и сборка
with open(app.STATS_FILE, encoding="utf-8") as f:
    app.state["stats"] = json.load(f)
by_id, _ = app.heroes()
picks = app.suggest([14, 44, 25], [64], 2, 5)  # против Pudge, PA, Lina; союзник Jakiro; мид
assert picks and all(2 in by_id[p["id"]]["pos"] for p in picks)
assert all(p["id"] not in (14, 44, 25, 64) for p in picks)
assert picks == sorted(picks, key=lambda p: -p["score"])
b = app.build(picks[0]["id"], [14, 44, 25], 2)
assert b["phases"] and all(i["img"].startswith("https://") for p in b["phases"] for i in p["items"])
assert b["team"]
sup = app.build(26, [44], 5)  # Lion саппорт против PA — без MKB
assert all(t["name"] != "Monkey King Bar" for t in sup["team"])
# Сборки по позициям: Pudge на миду и на пятёрке — разные закупы
with open("builds.json", encoding="utf-8") as f:
    app.state["builds"] = json.load(f)
mid, sup5 = app.build(14, [], 2), app.build(14, [], 5)
assert mid["pos"] == 2 and sup5["pos"] == 5 and "про-матчей" in mid["note"]
names = lambda b: {i["name"] for p in b["phases"] for i in p["items"]}
assert names(mid) != names(sup5)
assert "Tranquil Boots" in names(sup5)
auto = app.build(14, [], 0)  # «Любая» — самая частая позиция героя
assert auto["games"][auto["pos"]] == max(auto["games"].values())
rare = app.build(1, [], 5)  # Anti-Mage пятёркой не играют — общая сборка
assert "общая сборка" in rare["note"] and rare["phases"]

print("Мид против Pudge, PA, Lina:", [(by_id[p["id"]]["name"], round(p["score"] * 100, 1)) for p in picks])
print("OK")

# Данные для Telegram-виджета (site/data.json): всё, что читает страница, на месте
import build_data
build_data.main(use_cache=True)
with open(build_data.OUT, encoding="utf-8") as f:
    d = json.load(f)
assert {"heroes", "stats", "games", "builds", "items", "counters", "core_only", "patch", "min_games"} <= set(d)
m = len(d["stats"]["hg"])
assert all(h["id"] < m and h["attr"] in ("str", "agi", "int", "all") and h["roles"] for h in d["heroes"])
assert all(len(row) == m for row in d["stats"]["vg"])
assert d["builds"]["14-5"] and all(n in d["items"] for _, names in d["builds"]["14-5"] for n in names)
print("OK widget data")
