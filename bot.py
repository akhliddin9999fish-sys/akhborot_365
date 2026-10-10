"""
Публикует свежие материалы The Economist (заголовок + анонс + ссылка)
из RSS-лент в Telegram-канал, переводя текст на таджикский язык.
Уже опубликованное запоминается в posted.json.
"""
import html
import json
import os
import re
import time
from pathlib import Path

import feedparser
import requests
from deep_translator import GoogleTranslator

FEEDS = [
    f.strip()
    for f in (os.getenv("FEEDS") or "https://www.economist.com/latest/rss.xml").split(",")
    if f.strip()
]
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL = os.environ["TELEGRAM_CHANNEL"]  # например @my_channel
MAX_POSTS = int(os.getenv("MAX_POSTS", "10"))  # сколько постов за один запуск
LANG = os.getenv("LANG_TARGET", "tg")  # tg = таджикский
READ_MORE = "Мақоларо дар economist.com хонед"
STATE_FILE = Path("posted.json")

translator = GoogleTranslator(source="en", target=LANG)


def translate(text: str) -> str:
    if not text:
        return text
    for attempt in range(3):
        try:
            return translator.translate(text) or text
        except Exception as e:  # сбой перевода — пробуем ещё раз
            print(f"Ошибка перевода ({attempt + 1}/3): {e}")
            time.sleep(3)
    return text  # если перевод так и не удался, публикуем оригинал


def load_state() -> list[str]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return []


def save_state(links: list[str]) -> None:
    STATE_FILE.write_text(
        json.dumps(links[-2000:], ensure_ascii=False, indent=1), encoding="utf-8"
    )


def clean(text: str, limit: int = 350) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html.unescape(text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def collect_entries(posted: set[str]) -> list[dict]:
    items = {}
    for url in FEEDS:
        feed = feedparser.parse(url)
        for e in feed.entries:
            link = e.get("link")
            if not link or link in posted or link in items:
                continue
            items[link] = {
                "link": link,
                "title": clean(e.get("title", ""), 300),
                "summary": clean(e.get("summary", "")),
                "ts": time.mktime(e.published_parsed)
                if e.get("published_parsed")
                else 0,
            }
    # старые сначала, чтобы в канале был хронологический порядок
    return sorted(items.values(), key=lambda x: x["ts"])[-MAX_POSTS:]


def send(item: dict) -> None:
    title = html.escape(translate(item["title"]))
    summary = html.escape(translate(item["summary"]))
    text = f"<b>{title}</b>"
    if summary:
        text += f"\n\n{summary}"
    text += f'\n\n<a href="{item["link"]}">{READ_MORE}</a>'

    while True:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": CHANNEL,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=30,
        )
        if r.status_code == 429:  # Telegram просит подождать
            time.sleep(r.json().get("parameters", {}).get("retry_after", 30))
            continue
        if not r.ok:
            print("Ответ Telegram:", r.text)
        r.raise_for_status()
        return


def main() -> None:
    posted = load_state()
    new_items = collect_entries(set(posted))
    print(f"Новых материалов: {len(new_items)}")
    for item in new_items:
        send(item)
        posted.append(item["link"])
        save_state(posted)  # сохраняем после каждого поста
        time.sleep(3)


if __name__ == "__main__":
    main()
