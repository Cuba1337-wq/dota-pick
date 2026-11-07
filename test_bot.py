"""Проверка без Телеграма (нужен интернет для OpenDota): python test_bot.py"""
from collections import Counter

import bot
from heroes_data import RAW

# Нет одинаковых прозвищ у разных героев
aliases = [bot.norm(a) for line in RAW.strip().splitlines() for a in line.split("|")[2].split(",")]
dupes = [a for a, n in Counter(aliases).items() if n > 1]
assert not dupes, dupes

by_id, index = bot.heroes()
names = {h["name"] for h in by_id.values()}
listed = {line.split("|")[0] for line in RAW.strip().splitlines()}
assert listed <= names, listed - names  # опечатки в именах


def ids(text):
    found, unknown = bot.parse_heroes(text)
    return [by_id[i]["name"] for i in found], unknown


assert ids("пудж войд спирит лион") == (["Pudge", "Void Spirit", "Lion"], []), ids("пудж войд спирит лион")
assert ids("войд")[0] == ["Faceless Void"]
assert ids("Шадоу финд, анти-маг, QoP")[0] == ["Shadow Fiend", "Anti-Mage", "Queen of Pain"]
assert ids("джагернаутт")[0] == ["Juggernaut"]  # опечатка
assert ids("войда лиона войд спирита")[0] == ["Faceless Void", "Lion", "Void Spirit"]  # падежи
assert ids("абырвалг")[1] == ["абырвалг"]

# Позиции словами
for text, pos in [("мид", 2), ("на миду", 2), ("пятёрка", 5), ("полная поддержка", 5), ("саппорт", 5),
                  ("семи саппорт", 4), ("четверка", 4), ("керри", 1), ("единичка", 1), ("оффлейн", 3),
                  ("тройка", 3), ("3", 3)]:
    assert bot.extract_pos(text)[0] == pos, (text, bot.extract_pos(text))


def kind(text):
    reply = bot.handle(1, text)
    return reply.split("</b>")[0].replace("<b>", "")


# Что бот понимает из фразы: контрпик или сборка
cases = {
    "мид против пудж, войд, лион": "Пик на 2 (мид)",
    "2 пудж войд лион": "Пик на 2 (мид)",
    "пятёрка против сларка и медузы": "Пик на 5 (фулл-саппорт)",
    "я играю на полной поддержке против акса, союзники джакиро": "Пик на 5 (фулл-саппорт)",
    "пуч на миду": "Сборка: Pudge на 2 (мид)",
    "пудж полная поддержка против войда": "Сборка: Pudge на 5 (фулл-саппорт)",
    "пудж пятерка": "Сборка: Pudge на 5 (фулл-саппорт)",
    "инвокер": "Сборка: Invoker",
    "билд инвокер против пудж, фантомка": "Сборка: Invoker",
    "против пудж войд": "На какую позицию пикаешь? Например: <code>мид против пудж, войд</code>\n/pos — что такое позиции",
}
for text, want in cases.items():
    assert kind(text) == want, (text, kind(text))

print(bot.handle(1, "я играю на полной поддержке против акса, союзники джакиро"))
print("-" * 40)
print(bot.handle(1, "пудж полная поддержка против войда, фантомки"))
print("OK")

# Сборка против всей команды
team = bot.parse_heroes("фантомка, лион, лина, пудж, шторм")[0]
core = dict(bot.team_items(team, support=False))
sup = dict(bot.team_items(team, support=True))
assert "Black King Bar" in core and len(core["Black King Bar"]) >= 2, core  # BKB закрывает нескольких
assert not set(sup) & bot.CORE_ONLY, sup  # саппорту не советуем коровые предметы
reply = bot.handle(1, "керри против фантомка, лион, лина, пудж, шторм")
assert "Против этой команды" in reply and len(reply) < 4096, len(reply)
print(reply)
print("OK team")
