"""Dota Pick — программа для драфта. Без ИИ и ключей: открытые данные OpenDota.
Контрпики и синергия считаются по свежим пабликам Archon–Legend, сборки — по популярным закупам.
Запуск: start_app.bat (или python app.py). Окно открывается в Edge без адресной строки."""
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

from bot import API, POS_NAMES, get_json, heroes, item_build, norm, od, team_items

HERE = os.path.dirname(os.path.abspath(__file__))
STATS_FILE = os.path.join(HERE, "stats.json")
PORT = 8765
CDN = "https://cdn.cloudflare.steamstatic.com"
N = 200  # id героев меньше 200
CHUNKS = 8  # 8 × 150 тыс. последних матчей ≈ сутки пабликов
PRIOR = 100  # сглаживание: при малом числе игр тянем к ожидаемому винрейту

state = {"stats": None, "loading": False, "progress": "", "error": "", "last_ping": time.time()}


# ---------- данные ----------

def fetch_stats():
    """Качает свежие ранговые матчи Archon–Legend и считает пары героев."""
    vg, vw, sg, sw = ([[0] * N for _ in range(N)] for _ in range(4))
    hg, hw = [0] * N, [0] * N
    upper, matches = None, 0
    for i in range(CHUNKS):
        state["progress"] = f"Загружаю матчи: {i}/{CHUNKS}"
        where = f"where match_id < {upper}" if upper else ""
        sql = (f"select match_id, radiant_win w, radiant_team r, dire_team d from "
               f"(select * from public_matches {where} order by match_id desc limit 150000) x "
               f"where avg_rank_tier >= 41 and avg_rank_tier < 56 and lobby_type = 7 and duration > 600")
        data = get_json(API + "explorer?" + urllib.parse.urlencode({"sql": sql}), timeout=120)
        if data.get("err"):
            raise RuntimeError(data["err"])
        rows = data.get("rows") or []
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
select hero_id h, pos, case when (e->>'time')::int <= 0 then 0 when (e->>'time')::int < 900 then 1
            when (e->>'time')::int < 1800 then 2 else 3 end ph,
       e->>'key' k, count(distinct (match_id, player_slot)) n
from q, unnest(purchase_log) e
group by 1, 2, 3, 4 having count(distinct (match_id, player_slot)) >= 2
union all
select hero_id, pos, -1, 'games', count(*) from q group by 1, 2
"""
PHASES = ("start_game_items", "early_game_items", "mid_game_items", "late_game_items")
MIN_GAMES = 15  # меньше игр на позиции — показываем общую сборку героя


def explorer(sql):
    data = get_json(API + "explorer?" + urllib.parse.urlencode({"sql": sql}), timeout=120)
    if data.get("err"):
        raise RuntimeError(data["err"])
    return data.get("rows") or []


def fetch_builds():
    items = od("constants/items")
    patch = explorer("select patch from match_patch order by match_id desc limit 1")[0]["patch"]
    games, pop = {}, {}
    for i, lo in enumerate(range(1, N, 20)):
        state["progress"] = f"Загружаю сборки по позициям: {i}/{len(range(1, N, 20))}"
        for r in explorer(BUILD_SQL.format(patch=patch, lo=lo, hi=lo + 19)):
            key = f"{r['h']}-{r['pos']}"
            if r["ph"] == -1:
                games[key] = int(r["n"])
            elif r["k"] in items:
                pop.setdefault(key, {}).setdefault(PHASES[r["ph"]], {})[str(items[r["k"]]["id"])] = int(r["n"])
    return {"updated": time.time(), "patch": patch, "games": games, "pop": pop}


DATASETS = (  # (ключ в state, файл, сколько живёт, как скачать, проверка)
    ("stats", STATS_FILE, 86400, lambda: fetch_stats(), lambda d: d["matches"] >= 1000),
    ("builds", os.path.join(HERE, "builds.json"), 7 * 86400, lambda: fetch_builds(), lambda d: len(d["games"]) >= 100),
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


def rate(g, w, expected):
    return (w + PRIOR * expected) / (g + PRIOR)


def base_wr(s, h):
    return rate(s["hg"][h], s["hw"][h], 0.5)


def advantage(s, a, b):
    """Насколько a выигрывает у b чаще, чем ожидается по их общим винрейтам."""
    exp = min(max(base_wr(s, a) - base_wr(s, b) + 0.5, 0.01), 0.99)
    return rate(s["vg"][a][b], s["vw"][a][b], exp) - exp


def synergy(s, a, b):
    exp = min(max(base_wr(s, a) + base_wr(s, b) - 0.5, 0.01), 0.99)
    return rate(s["sg"][a][b], s["sw"][a][b], exp) - exp


def evaluate(s, h, enemies, allies):
    vs = [{"id": e, "adv": advantage(s, h, e), "games": s["vg"][h][e]} for e in enemies]
    syn = [{"id": a, "adv": synergy(s, h, a), "games": s["sg"][h][a]} for a in allies]
    meta = base_wr(s, h) - 0.5
    score = sum(x["adv"] for x in vs) + 0.5 * sum(x["adv"] for x in syn) + 0.5 * meta  # главное — матчапы
    return {"id": h, "score": score, "meta": meta, "wr": base_wr(s, h), "games": s["hg"][h], "vs": vs, "syn": syn}


def suggest(enemies, allies, pos, limit=10):
    s = state["stats"]
    by_id, _ = heroes()
    taken = set(enemies) | set(allies)
    rows = [evaluate(s, h, enemies, allies) for h in by_id
            if h not in taken and (not pos or pos in by_id[h]["pos"]) and s["hg"][h] >= 100]
    rows.sort(key=lambda r: -r["score"])
    return rows[:limit]


def build(hero, enemies, pos):
    """Сборка на позицию (pos=0 — самая частая позиция героя) + предметы против команды."""
    by_id, _ = heroes()
    items = od("constants/items")
    img = {it["dname"]: CDN + it["img"] for it in items.values() if it.get("dname") and it.get("img")}
    b = state.get("builds") or {"games": {}, "pop": {}, "patch": "?"}
    games = {p: b["games"].get(f"{hero}-{p}", 0) for p in range(1, 6)}
    if not pos:
        pos = max(games, key=games.get) if any(games.values()) else min(by_id[hero]["pos"])
    if games[pos] >= MIN_GAMES:
        phases = item_build(hero, b["pop"].get(f"{hero}-{pos}", {}))
        note = f"Закупы на {pos} позиции · {games[pos]} про-матчей патча {b['patch']}"
    else:
        phases = item_build(hero)
        note = f"На {pos} позиции героя почти не играют ({games[pos]} игр) — показана общая сборка"
    return {
        "pos": pos, "games": games, "note": note,
        "phases": [{"title": t, "items": [{"name": n, "img": img.get(n, "")} for n in names]}
                   for t, names in phases if names],
        "team": [{"name": n, "img": img.get(n, ""), "why": why} for n, why in team_items(enemies, pos in (4, 5))],
    }


def hero_list():
    from heroes_data import RAW
    aliases = {}
    for line in RAW.strip().splitlines():
        name, _, al = line.split("|")
        aliases[name] = [norm(a) for a in al.split(",")]
    out = []
    for h in od("heroStats"):
        me = heroes()[0][h["id"]]
        out.append({"id": h["id"], "name": me["name"], "img": CDN + h["img"].split("?")[0],
                    "pos": sorted(me["pos"]), "attr": h["primary_attr"],
                    "aliases": [norm(me["name"]), norm(h["name"].replace("npc_dota_hero_", ""))]
                    + aliases.get(me["name"], [])})
    return sorted(out, key=lambda x: x["name"])


# ---------- сервер ----------

def ids(q, key):
    return [int(x) for x in (q.get(key, [""])[0] or "").split(",") if x.strip().isdigit()]


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
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        try:
            if url.path == "/":
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html; charset=utf-8")
            if url.path == "/api/ping":
                state["last_ping"] = time.time()
                s = state["stats"]
                return self.send(200, {"ready": bool(s), "patch": (state.get("builds") or {}).get("patch", ""), "loading": state["loading"], "progress": state["progress"],
                                       "error": state["error"], "matches": s["matches"] if s else 0,
                                       "updated": s["updated"] if s else 0})
            if url.path == "/api/heroes":
                return self.send(200, {"heroes": hero_list(), "positions": POS_NAMES})
            if url.path == "/api/suggest":
                if not state["stats"]:
                    return self.send(503, {"error": "Данные ещё загружаются"})
                enemies, allies, me = ids(q, "enemies"), ids(q, "allies"), ids(q, "me")
                pos = int(q.get("pos", ["0"])[0] or 0)
                s = state["stats"]
                out = {"me": None, "picks": []}
                if me:
                    out["me"] = evaluate(s, me[0], enemies, allies)
                    out["me"]["build"] = build(me[0], enemies, pos)
                else:
                    out["picks"] = suggest(enemies, allies, pos)
                    if out["picks"]:
                        out["picks"][0]["build"] = build(out["picks"][0]["id"], enemies, pos)
                return self.send(200, out)
            if url.path == "/api/build":
                return self.send(200, build(ids(q, "hero")[0], ids(q, "enemies"), int(q.get("pos", ["0"])[0] or 0)))
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
