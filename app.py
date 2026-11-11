"""Dota Pick — программа для драфта. Без ИИ и ключей: открытые данные OpenDota.
Контрпики и синергия — по свежим пабликам Archon–Legend, сборки — по про-матчам текущего патча.
Запуск: «Dota Pick.bat» (или python app.py). Окно открывается в Edge без адресной строки.
Интерфейс общий с Telegram-виджетом: site/index.html, данные отдаются как /data.json."""
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bot import API, CONSUMABLES, get_json, heroes, norm, od
from heroes_data import CORE_ONLY, COUNTER_ITEMS, ORDER_RULES, RAW

HERE = os.path.dirname(os.path.abspath(__file__))
STATS_FILE = os.path.join(HERE, "stats.json")
PORT = 8765
CDN = "https://cdn.cloudflare.steamstatic.com"
N = 200  # id героев меньше 200
CHUNKS = 8  # 8 × 150 тыс. последних матчей ≈ сутки пабликов
MIN_GAMES = 15  # меньше игр на позиции — сборку не показываем
# промежуточные предметы: их покупают только по пути к большому, отдельно советовать не нужно
INTERMEDIATE = {"soul_booster", "lesser_crit", "buckler", "perseverance", "oblivion_staff", "ultimate_orb"}

state = {"stats": None, "loading": False, "progress": "", "error": "", "last_ping": time.time()}


# ---------- данные ----------

def explorer(sql):
    for attempt in range(3):  # OpenDota иногда отвечает ошибкой на ровном месте — пробуем ещё раз
        try:
            data = get_json(API + "explorer?" + urllib.parse.urlencode({"sql": sql}), timeout=120)
            if not data.get("err"):
                return data.get("rows") or []
            err = RuntimeError(data["err"])
        except OSError as e:
            err = e
        time.sleep(10)
    raise err


def fetch_stats():
    """Качает свежие ранговые матчи Archon–Legend и считает пары героев."""
    vg, vw, sg, sw = ([[0] * N for _ in range(N)] for _ in range(4))
    hg, hw = [0] * N, [0] * N
    upper, matches = None, 0
    for i in range(CHUNKS):
        state["progress"] = f"Загружаю матчи: {i}/{CHUNKS}"
        where = f"where match_id < {upper}" if upper else ""
        rows = explorer(f"select match_id, radiant_win w, radiant_team r, dire_team d from "
                        f"(select * from public_matches {where} order by match_id desc limit 150000) x "
                        f"where avg_rank_tier >= 41 and avg_rank_tier < 56 and lobby_type = 7 and duration > 600")
        if not rows:
            break
        upper = min(r["match_id"] for r in rows)
        for r in rows:
            for team, enemy, won in ((r["r"], r["d"], r["w"]), (r["d"], r["r"], not r["w"])):
                team = [h for h in team or [] if 0 < h < N]
                enemy = [h for h in enemy or [] if 0 < h < N]
                for a in team:
                    hg[a] += 1
                    hw[a] += won
                    for b in enemy:
                        vg[a][b] += 1
                        vw[a][b] += won
                    for b in team:
                        if b != a:
                            sg[a][b] += 1
                            sw[a][b] += won
            matches += 1
    return {"updated": time.time(), "matches": matches, "hg": hg, "hw": hw,
            "vg": vg, "vw": vw, "sg": sg, "sw": sw}


# Закупы по позициям: про-матчи текущего патча с логом покупок. Позиция — по линии и фарму в команде:
# двое с самым низким GPM — саппорты (меньший — 5), остальные по линии (лёгкая 1, мид 2, сложная 3).
# По каждому предмету: сколько игроков купили (n), медианная секунда покупки (t), сколько купили до старта (s).
BUILD_SQL = """
with p as (
  select pm.match_id, pm.player_slot, pm.hero_id, pm.lane_role, pm.purchase_log,
         row_number() over (partition by pm.match_id, pm.player_slot < 128 order by pm.gold_per_min) rk
  from player_matches pm join match_patch mp using(match_id)
  where mp.patch = '{patch}' and pm.purchase_log is not null
), q as (
  select *, case when rk = 1 then 5 when rk = 2 then 4
                 when lane_role = 2 then 2 when lane_role = 1 then 1 when lane_role = 3 then 3
                 when rk = 5 then 1 when rk = 4 then 2 else 3 end pos
  from p where hero_id between {lo} and {hi}
)
select hero_id h, pos, e->>'key' k, count(distinct (match_id, player_slot)) n,
       round(percentile_cont(0.5) within group (order by (e->>'time')::int))::int t,
       count(distinct case when (e->>'time')::int <= 0 then (match_id, player_slot) end) s
from q, unnest(purchase_log) e
group by 1, 2, 3 having count(distinct (match_id, player_slot)) >= 2
union all
select hero_id, pos, 'games', count(*), 0, 0 from q group by 1, 2
"""
BUILDS_VERSION = 2  # поменялся формат builds.json — старый кэш скачивается заново


def fetch_builds():
    patch = explorer("select patch from match_patch order by match_id desc limit 1")[0]["patch"]
    games, pop = {}, {}
    chunks = range(1, N, 10)  # по 10 героев: больше — база OpenDota не успевает
    for i, lo in enumerate(chunks):
        state["progress"] = f"Загружаю сборки: {i}/{len(chunks)}"
        for r in explorer(BUILD_SQL.format(patch=patch, lo=lo, hi=lo + 9)):
            key = f"{r['h']}-{r['pos']}"
            if r["k"] == "games":
                games[key] = int(r["n"])
            else:
                pop.setdefault(key, {})[r["k"]] = [int(r["n"]), int(r["t"]), int(r["s"])]
    return {"v": BUILDS_VERSION, "updated": time.time(), "patch": patch, "games": games, "pop": pop}


DATASETS = (  # (ключ в state, файл, сколько живёт, как скачать, проверка)
    ("stats", STATS_FILE, 86400, lambda: fetch_stats(), lambda d: d["matches"] >= 1000),
    ("builds", os.path.join(HERE, "builds.json"), 7 * 86400, lambda: fetch_builds(),
     lambda d: d.get("v") == BUILDS_VERSION and len(d["games"]) >= 100),
)


def load_data():
    """Берёт кэш с диска; что устарело — обновляет в фоне (старое пока работает)."""
    stale = []
    for key, path, ttl, fetch, ok in DATASETS:
        try:
            with open(path, encoding="utf-8") as f:
                state[key] = json.load(f)
        except (OSError, ValueError):
            pass
        if state.get(key) and not ok(state[key]):
            state[key] = None  # кэш старого формата не используем
        if not state.get(key) or time.time() - state[key]["updated"] > ttl:
            stale.append((key, path, fetch, ok))
    if not stale:
        return

    def work():
        state["loading"], state["error"] = True, ""
        for key, path, fetch, ok in stale:
            try:
                d = fetch()
                if not ok(d):
                    raise RuntimeError("слишком мало данных, попробуй позже")
                with open(path + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(d, f)
                os.replace(path + ".tmp", path)  # старый файл не портим, если загрузка оборвалась
                state[key] = d
            except Exception as e:
                state["error"] = f"Не удалось обновить данные: {e}"
                traceback.print_exc()
        state["loading"] = False

    threading.Thread(target=work, daemon=True).start()


def timed_build(pop, games, hero_name=""):
    """Сборка по минутам: старт, основные предметы по порядку покупки, предметы по ситуации."""
    items = od("constants/items")
    parts = {c for it in items.values() for c in (it.get("components") or [])}

    def usable(k):
        it = items.get(k, {})
        return it.get("dname") and not k.startswith("recipe") and k not in CONSUMABLES

    start = sorted((k for k, (n, t, s) in pop.items()  # на старте расходники (Tango и т.п.) нужны
                    if items.get(k, {}).get("dname") and not k.startswith("recipe") and s >= 0.2 * games),
                   key=lambda k: -pop[k][2])[:6]
    # большие предметы: без расходников, без голых компонентов (Mystic Staff), купленные после старта
    cand = {k: (n / games, t) for k, (n, t, s) in pop.items()
            if usable(k) and t > 60 and (items[k].get("cost") or 0) >= 400
            and not (k in parts and not items[k].get("created") and k != "blink")}
    # Sange не показываем, если популярен Sange and Yasha, в который он собирается
    inside = {c for k, (share, _) in cand.items() if share >= 0.25 for c in (items[k].get("components") or [])}
    cand = {k: v for k, v in cand.items() if k not in inside - {"blink", "ultimate_scepter"}}
    ranked = sorted(cand, key=lambda k: -cand[k][0])
    core = sorted([k for k in ranked if cand[k][0] >= 0.3][:8], key=lambda k: cand[k][1])
    # по ситуации — только готовые предметы, без промежуточных (Soul Booster, Crystalys)
    extra = sorted([k for k in ranked if k not in core and cand[k][0] >= 0.08 and k not in INTERMEDIATE][:6],
                   key=lambda k: cand[k][1])

    names = [items[k]["dname"] for k in core]
    minutes = [round(cand[k][1] / 60) for k in core]
    for first, then, _ in ORDER_RULES.get(hero_name, []):  # ручные правила порядка важнее статистики
        if first in names and then in names and names.index(first) > names.index(then):
            k = core.pop(names.index(first))
            core.insert(names.index(then), k)
            names = [items[k]["dname"] for k in core]
    # минуты остаются по возрастанию: это «когда обычно появляется N-й большой предмет»
    row = lambda k, m: [items[k]["dname"], m, round(cand[k][0] * 100)]
    return {"start": [items[k]["dname"] for k in start],
            "core": [row(k, m) for k, m in zip(core, minutes)],
            "extra": [row(k, round(cand[k][1] / 60)) for k in extra]}


def hero_list():
    aliases = {}
    for line in RAW.strip().splitlines():
        name, _, al = line.split("|")
        aliases[name] = [norm(a) for a in al.split(",")]
    by_id, _ = heroes()
    out = []
    for h in od("heroStats"):
        me = by_id[h["id"]]
        out.append({"id": h["id"], "name": me["name"], "img": CDN + h["img"].split("?")[0],
                    "pos": sorted(me["pos"]), "attr": h["primary_attr"], "roles": me["roles"],
                    "aliases": [norm(me["name"]), norm(h["name"].replace("npc_dota_hero_", ""))]
                    + aliases.get(me["name"], [])})
    return sorted(out, key=lambda x: x["name"])


def make_data(stats, builds):
    """Всё, что нужно странице, одним объектом: и для программы на ПК, и для Telegram-виджета."""
    hero_ls = hero_list()
    names = {h["id"]: h["name"] for h in hero_ls}
    m = max(names) + 1  # обрезаем таблицы до реальных id героев
    ready = {key: timed_build(builds["pop"].get(key, {}), n, names.get(int(key.split("-")[0]), ""))
             for key, n in builds["games"].items() if n >= MIN_GAMES}
    used = ({x[0] for b in ready.values() for x in b["core"] + b["extra"]} | {n for b in ready.values() for n in b["start"]}
            | {n for v in COUNTER_ITEMS.values() for n in v})
    items = od("constants/items")
    img = {it["dname"]: CDN + it["img"].split("?")[0] for it in items.values() if it.get("dname") and it.get("img")}
    return {
        "updated": stats["updated"], "matches": stats["matches"], "patch": builds["patch"], "min_games": MIN_GAMES,
        "heroes": hero_ls,
        "stats": {k: [row[:m] for row in stats[k][:m]] if isinstance(stats[k][0], list) else stats[k][:m]
                  for k in ("hg", "hw", "vg", "vw", "sg", "sw")},
        "games": builds["games"], "builds": ready,
        "items": {n: img.get(n, "") for n in used},
        "counters": COUNTER_ITEMS, "core_only": sorted(CORE_ONLY),
        "rules": {hero: [tip for _, _, tip in rules] for hero, rules in ORDER_RULES.items()},
    }


_data_cache = {}


def data_json():
    """make_data для программы на ПК; пересчитывается, только когда обновились данные."""
    key = (state["stats"]["updated"], state["builds"]["updated"])
    if _data_cache.get("key") != key:
        _data_cache.update(key=key, body=json.dumps(make_data(state["stats"], state["builds"]), ensure_ascii=False).encode())
    return _data_cache["body"]


# ---------- сервер ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path == "/api/bye":  # окно закрыли — выключаемся, если оно не откроется снова
            state["last_ping"] = time.time() - 170
        self.send(200, {})

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/":
                with open(os.path.join(HERE, "site", "index.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html; charset=utf-8")
            if path == "/api/ping":
                state["last_ping"] = time.time()
                return self.send(200, {"loading": state["loading"], "progress": state["progress"], "error": state["error"]})
            if path == "/data.json":
                if not (state.get("stats") and state.get("builds")):
                    return self.send(503, {"progress": state["progress"] or "Загружаю данные…", "error": state["error"]})
                return self.send(200, data_json())
            self.send(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            self.send(500, {"error": str(e)})


def open_window(url):
    for edge in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(edge):
            profile = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "DotaPick", "window")
            subprocess.Popen([edge, f"--app={url}", "--window-size=1320,880", f"--user-data-dir={profile}",
                              "--no-first-run"])
            return
    webbrowser.open(url)


def main():
    if sys.stderr is None:  # запуск через pythonw — консоли нет, ошибки пишем в app.log
        sys.stdout = sys.stderr = open(os.path.join(HERE, "app.log"), "a", encoding="utf-8", buffering=1)
    url = f"http://127.0.0.1:{PORT}/"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:  # программа уже запущена — просто открываем окно
        return open_window(url)
    load_data()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    open_window(url)
    print("Dota Pick работает:", url)
    while time.time() - state["last_ping"] < 180:  # окно не пингует 3 минуты — выключаемся
        time.sleep(5)


if __name__ == "__main__":
    main()
