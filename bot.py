import json
import os
import re
import textwrap
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import feedparser
import requests
from openai import OpenAI

RSS_FEEDS = [
    "https://feeds.feedburner.com/venturebeat/SZYF",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://www.tomshardware.com/feeds/all",
]

KEYWORDS = [
    "local llm","ollama","lm studio","qwen","deepseek","llama","gemma",
    "mistral","mixtral","phi","vllm","rag","ai agent","agentic",
    "inference","gpu","cuda","tensorrt","open source ai","open-source ai",
]

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

MAX_INITIAL_CANDIDATES = 20
MAX_SEARCH_QUERIES = 3
MAX_SEARCH_RESULTS_PER_QUERY = 5
MAX_FINAL_CANDIDATES = 30
MAX_OUTPUT_ITEMS = 5

HISTORY_FILE = "sent_articles.json"
MEMORY_FILE = "daily_memory.json"

# 🔥 これが重要（統一ヘッダー）
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AI-News-Bot/1.0)"
}


def now_jst_str():
    return datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M:%S JST")


def normalize_text(text):
    return " ".join((text or "").lower().split())


def clean_summary_text(summary):
    summary = re.sub(r"<[^>]+>", " ", summary or "")
    summary = re.sub(r"\s+", " ", summary).strip()
    return summary


def extract_json_object(text):
    text = text.strip()
    try:
        return json.loads(text)
    except:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))

    raise ValueError("JSON抽出失敗")


def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except:
        return set()


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(history), f, ensure_ascii=False, indent=2)


def load_memory():
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"last_topics": []}


def save_memory(memory):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)


def score_entry(title, summary):
    text = normalize_text(f"{title} {summary}")
    return sum(1 for kw in KEYWORDS if kw in text)


def resolve_final_url(url):
    try:
        res = requests.get(url, headers=REQUEST_HEADERS, timeout=10)
        return str(res.url)
    except:
        return url


def suppress_discord_embeds(text):
    return re.sub(r"https?://[^\s>]+", lambda m: f"<{m.group(0)}>", text)


# 🔥 User-Agent追加
def fetch_feed_items(feed_urls, sent_history):
    items = []

    for url in feed_urls:
        feed = feedparser.parse(url, request_headers=REQUEST_HEADERS)

        for entry in feed.entries:
            link = getattr(entry, "link", "")
            if not link or link in sent_history:
                continue

            title = entry.title
            summary = clean_summary_text(getattr(entry, "summary", ""))

            if score_entry(title, summary) <= 0:
                continue

            items.append({
                "title": title,
                "summary": summary,
                "link": link,
                "score": 1
            })

    return items


def fetch_search_results(queries, sent_history):
    items = []

    for q in queries:
        rss = f"https://news.google.com/rss/search?q={quote(q)}"
        feed = feedparser.parse(rss, request_headers=REQUEST_HEADERS)

        for entry in feed.entries:
            link = resolve_final_url(entry.link)
            if link in sent_history:
                continue

            items.append({
                "title": entry.title,
                "summary": clean_summary_text(getattr(entry, "summary", "")),
                "link": link,
                "score": 1
            })

    return items


def post_to_discord(message):
    if not DISCORD_WEBHOOK_URL:
        print("Webhookなし")
        return

    for chunk in split_message(message):
        res = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": chunk},
            headers=REQUEST_HEADERS,  # 🔥 ここ追加
            timeout=30
        )
        res.raise_for_status()


def split_message(text, max_len=1800):
    lines = text.splitlines(True)
    chunks, current = [], ""

    for line in lines:
        if len(current) + len(line) > max_len:
            chunks.append(current)
            current = line
        else:
            current += line

    if current:
        chunks.append(current)

    return chunks


def main():
    print("start")

    if not OPENAI_API_KEY:
        raise RuntimeError("APIキーなし")

    client = OpenAI(api_key=OPENAI_API_KEY)

    history = load_history()
    memory = load_memory()

    items = fetch_feed_items(RSS_FEEDS, history)
    queries = ["AI news"]

    items += fetch_search_results(queries, history)
    items = items[:5]

    message = "📡 AIニュース速報\n\n"

    for i, item in enumerate(items, 1):
        message += f"{i}. {item['title']}\n{item['link']}\n\n"

    print(message)
    post_to_discord(message)

    for item in items:
        history.add(item["link"])

    save_history(history)


if __name__ == "__main__":
    main()
