"""Готовит site/data.json для Telegram-виджета (та же функция make_data, что у программы на ПК).
Запускается в GitHub Actions раз в день. Локально: python build_data.py --cache (берёт stats.json и builds.json)."""
import json
import os
import sys
import traceback

import app

OUT = os.path.join(app.HERE, "site", "data.json")


ERR = os.path.join(app.HERE, "site", "build_error.txt")


def main(use_cache, fallback=False):
    try:
        if use_cache:
            app.load_data()
            stats, builds = app.state["stats"], app.state["builds"]
        else:
            stats, builds = app.fetch_stats(), app.fetch_builds()
        assert stats and builds and stats["matches"] >= 1000 and len(builds["games"]) >= 100, "мало данных или кэш устарел"
        data = app.make_data(stats, builds)
    except Exception:
        # GitHub: если OpenDota не ответила, публикуем сайт с прошлыми данными из репозитория,
        # а текст ошибки кладём рядом — его видно по ссылке без входа в GitHub
        if not (fallback and os.path.exists(OUT)):
            raise
        with open(ERR, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        print(traceback.format_exc(), "\nОставляю прошлый site/data.json")
        return
    if os.path.exists(ERR):
        os.remove(ERR)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{OUT}: {os.path.getsize(OUT) // 1024} КБ, {len(data['heroes'])} героев, {len(data['builds'])} сборок, патч {data['patch']}")


if __name__ == "__main__":
    main("--cache" in sys.argv, "--fallback" in sys.argv)
