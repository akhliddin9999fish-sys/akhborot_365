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
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def translate_google(text: str) -> str:
    r = requests.get(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "en", "tl": LANG, "dt": "t", "q": text},
        headers=HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    return "".join(part[0] for part in r.json()[0] if part and part[0])


def translate_mymemory(text: str) -> str:
    r = requests.get(
        "https://api.mymemory.translated.net/get",
        params={"q": text, "langpair": f"en|{LANG}"},
        headers=HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("responseStatus") != 200:
        raise RuntimeError(data.get("responseDetails"))
    return data["responseData"]["translatedText"]


def translate(text: str) -> str | None:
    """Возвращает перевод или None, если ни один сервис не сработал."""
    if not text:
        return ""
    for service in (translate_google, translate_mymemory):
        for attempt in range(2):
            try:
                result = service(text).strip()
                if result and result.lower() != text.lower():
                    return result
                raise RuntimeError("пустой или непереведённый ответ")
            except Exception as e:
                print(f"Перевод через {service.__name__} не удался ({attempt + 1}/2): {e}")
                time.sleep(2)
    return None


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


def send(item: dict) -> bool:
    """Публикует пост. Возвращает False, если перевести не удалось."""
    title = translate(item["title"])
    summary = translate(item["summary"])
    if title is None or summary is None:
        print(f"Пропускаю (нет перевода), попробую в следующий раз: {item['link']}")
        return False

    text = f"<b>{html.escape(title)}</b>"
    if summary:
        text += f"\n\n{html.escape(summary)}"
    text += f'\n\n<a href="{item["link"]}">{READ_MORE}</a>'

    while True:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHANNEL, "text": text, "parse_mode": "HTML"},
            timeout=30,
        )
        if r.status_code == 429:  # Telegram просит подождать
            time.sleep(r.json().get("parameters", {}).get("retry_after", 30))
            continue
        if not r.ok:
            print("Ответ Telegram:", r.text)
        r.raise_for_status()
        return True


def main() -> None:
    posted = load_state()
    new_items = collect_entries(set(posted))
    print(f"Новых материалов: {len(new_items)}")
    failed = 0
    for item in new_items:
        if send(item):
            posted.append(item["link"])
            save_state(posted)  # сохраняем после каждого поста
            time.sleep(3)
        else:
            failed += 1
            if failed >= 2:  # сервисы перевода недоступны — не тратим время
                print("Перевод недоступен, остальное опубликую при следующей проверке.")
                break
    save_state(posted)


if __name__ == "__main__":
    main()
