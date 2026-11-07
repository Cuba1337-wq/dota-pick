"""Телеграм-бот для пика в Dota 2: контрпики и сборки по данным OpenDota.
Без внешних библиотек. Токен: переменная TG_TOKEN или файл token.txt рядом."""
import difflib
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request

from heroes_data import CORE_ONLY, COUNTER_ITEMS, POS_NAMES, RAW

HERE = os.path.dirname(os.path.abspath(__file__))
POOL_FILE = os.path.join(HERE, "pool.json")
API = "https://api.opendota.com/api/"
BRACKETS = ("4", "5")  # Archon, Legend
PRIOR = 20  # сглаживание: у матчапа с малым числом игр вес меньше
_cache = {}


def get_json(url, data=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "dota-pick-bot"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def od(path):
    """Запрос к OpenDota с кэшем на сутки."""
    hit = _cache.get(path)
    if hit and time.time() - hit[0] < 86400:
        return hit[1]
    data = get_json(API + path)
    _cache[path] = (time.time(), data)
    return data


def norm(s):
    s = s.lower().replace("ё", "е").replace("-", " ")
    return " ".join(re.sub(r"[^\w ]", "", s).split())


def roles_to_pos(roles):
    pos = set()
    if "Carry" in roles:
        pos.add(1)
    if "Support" in roles:
        pos |= {4, 5}
    if not pos and ("Initiator" in roles or "Durable" in roles):
        pos.add(3)
    return pos or {2}


def heroes():
    """id -> {id, name, pos, wr}; плюс индекс названий -> id."""
    known = {}
    for line in RAW.strip().splitlines():
        name, pos, aliases = line.split("|")
        known[name] = ({int(p) for p in pos}, aliases.split(","))
    by_id, index = {}, {}
    for h in od("heroStats"):
        pos, aliases = known.get(h["localized_name"], (roles_to_pos(h["roles"]), []))
        picks = sum(h.get(b + "_pick") or 0 for b in BRACKETS)
        wins = sum(h.get(b + "_win") or 0 for b in BRACKETS)
        by_id[h["id"]] = {"id": h["id"], "name": h["localized_name"], "pos": pos, "roles": h["roles"],
                          "wr": wins / picks if picks else 0.5}
        for a in [h["localized_name"], h["name"].replace("npc_dota_hero_", "")] + aliases:
            index.setdefault(norm(a), h["id"])
    return by_id, index


def parse_heroes(text):
    """Текст -> (список id, список непонятых слов). Через запятую или через пробел."""
    _, index = heroes()
    found, unknown = [], []

    multi = [k for k in index if " " in k]

    def fuzzy(key, keys, cutoff):
        m = difflib.get_close_matches(key, keys, 1, cutoff)
        return index[m[0]] if m else None

    for part in re.split(r"[,;\n]+", text):
        words = norm(part).split()
        i = 0
        while i < len(words):
            for n in (3, 2):  # сначала длинные названия: "войд спирит" раньше "войд"
                hid = index.get(" ".join(words[i:i + n]))
                if hid:
                    break
            if not hid and i + 1 < len(words):  # "войд спирита" — падеж в названии из двух слов
                n, hid = 2, fuzzy(" ".join(words[i:i + 2]), multi, 0.85)
            if not hid:
                n, hid = 1, index.get(words[i])
            if not hid:  # "лиона", "джагернаутт" — падежи и опечатки
                n, hid = 1, fuzzy(words[i], index.keys(), 0.75)
            found.append(hid) if hid else unknown.append(words[i])
            i += n
    return list(dict.fromkeys(found)), unknown


def counterpick(pos, enemies, exclude=(), pool=()):
    """Кандидаты на позицию, отсортированные по преимуществу против врагов + винрейту на Archon–Legend."""
    by_id, _ = heroes()
    mus, base = {}, {}
    for e in enemies:
        data = od(f"heroes/{e}/matchups")
        mus[e] = {m["hero_id"]: m for m in data}
        games = sum(m["games_played"] for m in data)
        base[e] = sum(m["wins"] for m in data) / games if games else 0.5  # общий винрейт врага
    rows = []
    for h in by_id.values():
        if pos not in h["pos"] or h["id"] in exclude or h["id"] in enemies:
            continue
        advs = []
        for e in enemies:
            # wins в ответе — победы врага; преимущество = насколько враг проигрывает нам чаще обычного
            m = mus[e].get(h["id"], {"games_played": 0, "wins": 0})
            enemy_wr = (m["wins"] + PRIOR * base[e]) / (m["games_played"] + PRIOR)
            advs.append(base[e] - enemy_wr)
        score = (sum(advs) / len(advs) if advs else 0) + (h["wr"] - 0.5)
        rows.append((score, h, advs))
    rows.sort(key=lambda r: -r[0])
    return [r for r in rows if r[1]["id"] in pool], rows


def pct(x):
    return f"{x * 100:+.1f}%"


def fmt_counterpick(pos, enemies, allies, pool):
    by_id, _ = heroes()
    mine, best = counterpick(pos, enemies, allies, pool)

    def line(score, h, advs):
        vs = ", ".join(f"{by_id[e]['name']} {pct(a)}" for e, a in zip(enemies, advs))
        return f"<b>{html.escape(h['name'])}</b> {pct(score)}" + (f"\n   <i>{html.escape(vs)}</i>" if vs else "")

    out = [f"<b>Пик на {pos} ({POS_NAMES[pos]})</b> против: "
           + html.escape(", ".join(by_id[e]["name"] for e in enemies) or "—")]
    if mine:
        out.append("\n⭐ <b>Из твоего пула:</b>")
        out += [line(*r) for r in mine[:3]]
    out.append("\n🔥 <b>Лучшие варианты:</b>")
    out += [f"{i}. " + line(*r) for i, r in enumerate(best[:5], 1)]
    top = (mine or best)[0][1]
    out.append("\n" + "—" * 12 + "\n")
    out.append(fmt_build(top["id"], enemies, pos))
    others = [r[1]["name"] for r in (mine + best) if r[1]["id"] != top["id"]][:1]
    if others:
        out.append(f"\nСборка на другого: <code>{html.escape(others[0])} {POS_NAMES[pos]} против "
                   + html.escape(", ".join(by_id[e]["name"] for e in enemies)) + "</code>")
    return "\n".join(out)


CONSUMABLES = {"tpscroll", "tango", "flask", "clarity", "enchanted_mango", "faerie_fire", "smoke_of_deceit", "dust",
               "ward_observer", "ward_sentry", "ward_dispenser", "blood_grenade", "tome_of_knowledge"}


def item_build(hero_id, pop=None):
    """pop — {фаза: {id предмета: сколько раз купили}}; по умолчанию общая популярность героя."""
    pop = pop or od(f"heroes/{hero_id}/itemPopularity")
    items, ids = od("constants/items"), od("constants/item_ids")
    parts = {c for it in items.values() for c in (it.get("components") or [])}
    # дорогие компоненты (Mystic Staff, Reaver...) — не итоговые предметы; Blink исключение
    raw_parts = {k for k in parts if not items.get(k, {}).get("created") and k != "blink"} | {"soul_booster"}
    # Sange не показываем, если в популярных уже есть Sange and Yasha, в который он собирается
    popular = [ids.get(i, "") for p in ("mid_game_items", "late_game_items")
               for i, _ in sorted(pop.get(p, {}).items(), key=lambda x: -x[1])[:25]]
    raw_parts |= {c for k in popular for c in (items.get(k, {}).get("components") or [])} - {"ultimate_scepter", "blink"}
    seen, phases = set(), []
    for phase, title, n, min_cost in (("start_game_items", "Старт", 6, 0),
                                      ("early_game_items", "Ранняя игра", 5, 400),
                                      ("mid_game_items", "Мид гейм", 5, 1800),
                                      ("late_game_items", "Поздняя игра", 5, 2500)):
        got = []
        for iid, _ in sorted(pop.get(phase, {}).items(), key=lambda x: -x[1]):
            key = ids.get(iid, "")
            it = items.get(key, {})
            name = it.get("dname")
            if (not name or key.startswith("recipe") or (it.get("cost") or 0) < min_cost or name in seen
                    or (min_cost and (key in raw_parts or key in CONSUMABLES))):
                continue
            seen.add(name)
            got.append(name)
            if len(got) == n:
                break
        phases.append((title, got))
    return phases


def fmt_build(hero_id, enemies, pos=None):
    by_id, _ = heroes()
    h = by_id[hero_id]
    where = f" на {pos} ({POS_NAMES[pos]})" if pos else ""
    out = [f"<b>Сборка: {html.escape(h['name'])}{where}</b> (винрейт Archon–Legend: {h['wr'] * 100:.1f}%)"]
    if pos and pos not in h["pos"]:
        out.append("<i>На этой позиции героя играют редко — предметы ниже общие для героя.</i>")
    for title, got in item_build(hero_id):
        if got:
            out.append(f"\n<b>{title}:</b> " + html.escape(", ".join(got)))
    support = pos in (4, 5) if pos else h["pos"] <= {4, 5}
    counters = team_items(enemies, support)
    if counters:
        out.append("\n🛡 <b>Против этой команды</b> (сверху — то, что закрывает сразу нескольких):")
        out += [f"• <b>{html.escape(item)}</b> — {html.escape(', '.join(why))}" for item, why in counters]
    return "\n".join(out)


def team_items(enemies, support):
    """Ситуативные предметы против всей вражеской команды: предмет -> против кого он нужен."""
    by_id, _ = heroes()
    why = {}
    for e in enemies:
        for item in COUNTER_ITEMS.get(by_id[e]["name"], []):
            why.setdefault(item, []).append(by_id[e]["name"])
    for role, label, core_items, sup_items in (
            ("Nuker", "много магии", ["Black King Bar", "Pipe of Insight"], ["Glimmer Cape", "Pipe of Insight"]),
            ("Disabler", "много контроля", ["Black King Bar", "Linken's Sphere"], ["Lotus Orb", "Force Staff"])):
        n = sum(role in by_id[e]["roles"] for e in enemies)
        if n >= 4:  # у большинства героев эти роли есть, поэтому порог высокий
            for item in sup_items if support else core_items:
                why.setdefault(item, []).append(f"{label} ({n} из {len(enemies)})")
    rows = [(item, w) for item, w in why.items() if not (support and item in CORE_ONLY)]
    rows.sort(key=lambda r: -len(r[1]))
    return rows[:7]


def load_pools():
    try:
        with open(POOL_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_pools(pools):
    with open(POOL_FILE, "w", encoding="utf-8") as f:
        json.dump(pools, f, ensure_ascii=False, indent=1)


HELP = """<b>Пиши обычной фразой — можно надиктовать голосом.</b>

🎯 <b>Кого пикнуть + сборка против всей команды</b> — позиция, «против» и враги (лучше всех пятерых):
<code>мид против пудж, войд, лион, лина, акс</code>
<code>пятёрка против сларк медуза</code>
<code>керри против акс лион, союзники джакиро</code> — союзников не посоветую

🛠 <b>Сборка на твоего героя</b> — сначала герой, потом позиция:
<code>пудж мид против войд, лион</code>
<code>пудж полная поддержка</code>
<code>инвокер</code> — просто сборка без врагов

⭐ <b>Твой пул</b> — эти герои показываются первыми:
<code>/pool мид инвокер шторм пак</code> — задать
<code>/pool</code> — посмотреть, <code>/pool мид</code> — очистить

📖 /pos — что такое позиции 1–5

Позицию можно писать как угодно: <i>1, единичка, керри · 2, мид · 3, тройка, оффлейн · 4, четвёрка, семи-саппорт · 5, пятёрка, полная поддержка, саппорт</i>
Статистика OpenDota, винрейты Archon–Legend."""

POS_HELP = """<b>Позиции в Доте</b>

<b>1 — керри</b> (лёгкая линия). Главный фармер команды: слабый в начале, к концу игры выносит всех. Ему отдают фарм.
<i>Anti-Mage, Phantom Assassin, Juggernaut, Medusa, Faceless Void</i>

<b>2 — мид</b> (центральная линия, один на один). Быстро качается, рано ходит драться и задаёт темп игры.
<i>Invoker, Storm Spirit, Shadow Fiend, Queen of Pain, Puck</i>

<b>3 — оффлейн</b> (сложная линия). Живучий герой, стоит против вражеского керри, начинает драки.
<i>Axe, Mars, Centaur, Bristleback, Tidehunter</i>

<b>4 — семи-саппорт</b> (частичная поддержка). Бегает по карте, ганкает, контролит. Фарма немного больше, чем у пятёрки.
<i>Earth Spirit, Tusk, Rubick, Hoodwink, Clockwerk</i>

<b>5 — фулл-саппорт</b> (полная поддержка). Стоит с керри, ставит варды, спасает союзников, почти не фармит.
<i>Crystal Maiden, Lion, Witch Doctor, Dazzle, Lich</i>"""

COMMANDS = [
    {"command": "help", "description": "Как писать боту: контрпик и сборка"},
    {"command": "pos", "description": "Что такое позиции 1–5"},
    {"command": "pool", "description": "Твой пул героев: /pool мид инвокер шторм"},
    {"command": "start", "description": "Начать и показать примеры"},
]

# Как можно назвать позицию. Порядок важен: «семи саппорт» проверяем раньше «саппорт».
POS_WORDS = [
    (4, r"(?:семи|полу|частичн\w*)[\s-]*(?:саппорт|сапорт|поддержк)\w*|четв[её]р\w*|роум\w*"),
    (5, r"(?:(?:фулл?|полн\w*|хард)[\s-]*)?(?:саппорт|сапорт|поддержк)\w*|пят[её]р\w*"),
    (1, r"(?:хард[\s-]*)?(?:керри|кэрри|карри)\w*|единичк\w*|единиц\w*|одн[её]рк\w*|с[еэ]йф\w*"),
    (2, r"мид\w*|двойк\w*|центр\w*"),
    (3, r"офф?л[ае]йн\w*|тройк\w*|хардл[ае]йн\w*"),
    (0, r"[1-5]"),
]
FILLERS = (r"на|за|в|во|и|а|я|мне|мы|у|них|с|играю|играем|буду|позици\w*|поз|pos|кого|что|лучше|"
           r"пикнуть|пикать|взять|брать|враги|врагов|есть|линии|линию|лайн\w*|роль|ролью|герой|героя")
ALLY_SPLIT = r"/|(?<!\w)(?:союзник\w*|у нас|наши|свои|тиммейт\w*)(?!\w)"
VS_SPLIT = r"(?<!\w)(?:против|vs|контра)(?!\w)"


def strip_fillers(s):
    return re.sub(rf"(?<!\w)(?:{FILLERS})(?!\w)", " ", s, flags=re.I)


def extract_pos(text):
    """-> (позиция или None, позиция стоит в начале фразы?, текст без слова позиции)."""
    for pos, pat in POS_WORDS:
        m = re.search(rf"(?<!\w)(?:{pat})(?!\w)", text, re.I)
        if m:
            at_start = not strip_fillers(text[:m.start()]).strip(" ,.")
            return pos or int(m.group()), at_start, text[:m.start()] + " " + text[m.end():]
    return None, False, text


def names_reply(unknown):
    return "\n\n❓ Не понял: " + html.escape(", ".join(unknown)) if unknown else ""


def handle(chat_id, text):
    text = text.strip()
    low = text.lower()
    if low.startswith(("/start", "/help")):
        return HELP
    if low.startswith("/pos"):
        return POS_HELP

    if low.startswith("/pool"):
        pools = load_pools()
        mine = pools.setdefault(str(chat_id), {})
        pos, _, rest = extract_pos(re.sub(r"^/pool\S*", "", text))
        if not pos:
            by_id, _ = heroes()
            lines = [f"{p} ({POS_NAMES[int(p)]}): " + ", ".join(by_id[i]["name"] for i in ids)
                     for p, ids in sorted(mine.items()) if ids]
            return "<b>Твой пул:</b>\n" + html.escape("\n".join(lines)) if lines else "Пул пуст.\n\n" + HELP
        ids, unknown = parse_heroes(strip_fillers(rest))
        mine[str(pos)] = ids
        save_pools(pools)
        return f"Пул на {pos} ({POS_NAMES[pos]}) сохранён: {len(ids)} героев." + names_reply(unknown)

    pos, pos_first, rest = extract_pos(text)
    rest, n_kw = re.subn(r"(?<!\w)(?:билд|сборк\w*|build)(?!\w)", " ", rest, flags=re.I)
    main_part, *ally_part = re.split(ALLY_SPLIT, rest, maxsplit=1, flags=re.I)
    allies, unk_a = parse_heroes(strip_fillers(ally_part[0])) if ally_part else ([], [])
    head, *tail = re.split(VS_SPLIT, main_part, maxsplit=1, flags=re.I)
    mine, unknown = parse_heroes(strip_fillers(head))
    enemies, unk_e = parse_heroes(strip_fillers(tail[0])) if tail else ([], [])
    unknown += unk_e + unk_a

    if not tail and mine and pos and pos_first and not n_kw:
        mine, enemies = [], mine  # «мид пудж войд лион» — позиция первой, значит дальше враги
    if mine:  # перед «против» есть герой — это твой герой, нужна сборка
        return fmt_build(mine[0], enemies + mine[1:], pos) + names_reply(unknown)
    if not pos:
        return ("На какую позицию пикаешь? Например: <code>мид против пудж, войд</code>\n"
                "/pos — что такое позиции" + names_reply(unknown))
    pool = load_pools().get(str(chat_id), {}).get(str(pos), [])
    return fmt_counterpick(pos, enemies, allies, pool) + names_reply(unknown)


def read_token():
    tok = os.environ.get("TG_TOKEN")
    if not tok:
        with open(os.path.join(HERE, "token.txt"), encoding="utf-8") as f:
            tok = f.read().strip()
    return tok


def main():
    api = f"https://api.telegram.org/bot{read_token()}/"

    def tg(method, **params):
        return get_json(api + method, urllib.parse.urlencode(params).encode(), timeout=70)

    me = tg("getMe")["result"]
    tg("setMyCommands", commands=json.dumps(COMMANDS, ensure_ascii=False))  # меню с описаниями
    print(f"Бот @{me['username']} запущен. Ctrl+C — остановить.")
    offset = 0
    while True:
        try:
            updates = tg("getUpdates", offset=offset, timeout=50)["result"]
        except Exception as e:  # сеть пропала — ждём и пробуем снова
            print("Ошибка сети:", e)
            time.sleep(5)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            if not msg.get("text"):
                continue
            chat = msg["chat"]["id"]
            try:
                reply = handle(chat, msg["text"])
            except Exception as e:
                reply = "Ошибка: " + html.escape(str(e))
            try:
                tg("sendMessage", chat_id=chat, text=reply, parse_mode="HTML")
            except Exception as e:
                print("Не отправилось:", e)


if __name__ == "__main__":
    main()
