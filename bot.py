import json
import os
import re
import html
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
    "local llm", "ollama", "lm studio", "qwen", "deepseek", "llama", "gemma",
    "mistral", "mixtral", "phi", "vllm", "rag", "ai agent", "agentic",
    "inference", "gpu", "cuda", "tensorrt", "open source ai", "open-source ai",
    "open weights", "open-weight", "asr", "speech model", "rtx", "nvidia", "amd"
]

# Google News 側は broad すぎる "AI news" をやめる
SEARCH_QUERIES = [
    "local llm OR ollama OR lm studio OR vllm",
    "qwen OR deepseek OR llama OR gemma OR mistral",
    "gpu AI inference OR cuda OR tensorrt OR rtx"
]

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

MAX_FEED_ITEMS = 30
MAX_SEARCH_RESULTS_PER_QUERY = 8
MAX_FINAL_CANDIDATES = 20
MAX_OUTPUT_ITEMS = 5

HISTORY_FILE = "sent_articles.json"
MEMORY_FILE = "daily_memory.json"

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AI-News-Bot/1.0)"
}


def now_jst_str():
    return datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M:%S JST")


def normalize_text(text):
    return " ".join((text or "").lower().split())


def clean_text(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data if isinstance(data, list) else [])
    except Exception:
        return set()


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(history)), f, ensure_ascii=False, indent=2)


def load_memory():
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"last_topics": []}


def save_memory(memory):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)


def score_entry(title, summary):
    text = normalize_text(f"{title} {summary}")
    score = 0

    for kw in KEYWORDS:
        if kw in text:
            score += 2

    # 強めに拾いたい単語
    bonus_words = [
        "open", "model", "models", "inference", "agent", "agents",
        "voice", "speech", "gpu", "server", "api", "weights"
    ]
    for word in bonus_words:
        if word in text:
            score += 1

    # ノイズ寄りを少し減点
    penalty_words = [
        "gaming pc", "deal", "discount", "sale", "monitor", "keyboard", "mouse"
    ]
    for word in penalty_words:
        if word in text:
            score -= 2

    return score


def resolve_final_url(url):
    try:
        res = requests.get(
            url,
            headers=REQUEST_HEADERS,
            timeout=10,
            allow_redirects=True
        )
        return str(res.url)
    except Exception:
        return url


def suppress_discord_embeds(text):
    return re.sub(r"https?://[^\s>]+", lambda m: f"<{m.group(0)}>", text)


def split_message(text, max_len=1800):
    lines = text.splitlines(True)
    chunks = []
    current = ""

    for line in lines:
        if len(current) + len(line) > max_len:
            if current:
                chunks.append(current)
            current = line
        else:
            current += line

    if current:
        chunks.append(current)

    return chunks


def fetch_feed_items(feed_urls, sent_history):
    items = []

    for url in feed_urls:
        try:
            feed = feedparser.parse(url, request_headers=REQUEST_HEADERS)
        except Exception:
            continue

        for entry in getattr(feed, "entries", []):
            raw_link = getattr(entry, "link", "")
            if not raw_link:
                continue

            link = resolve_final_url(raw_link)
            if link in sent_history:
                continue

            title = clean_text(getattr(entry, "title", ""))
            summary = clean_text(getattr(entry, "summary", ""))

            score = score_entry(title, summary)
            if score <= 0:
                continue

            items.append({
                "title": title,
                "summary": summary,
                "link": link,
                "source": url,
                "score": score
            })

    return items[:MAX_FEED_ITEMS]


def fetch_search_results(queries, sent_history):
    items = []

    for q in queries:
        rss = f"https://news.google.com/rss/search?q={quote(q)}"
        try:
            feed = feedparser.parse(rss, request_headers=REQUEST_HEADERS)
        except Exception:
            continue

        count = 0
        for entry in getattr(feed, "entries", []):
            raw_link = getattr(entry, "link", "")
            if not raw_link:
                continue

            link = resolve_final_url(raw_link)
            if link in sent_history:
                continue

            title = clean_text(getattr(entry, "title", ""))
            summary = clean_text(getattr(entry, "summary", ""))

            score = score_entry(title, summary)
            if score <= 0:
                continue

            items.append({
                "title": title,
                "summary": summary,
                "link": link,
                "source": f"google_news:{q}",
                "score": score
            })

            count += 1
            if count >= MAX_SEARCH_RESULTS_PER_QUERY:
                break

    return items


def dedupe_items(items):
    seen_links = set()
    seen_titles = set()
    unique = []

    for item in items:
        link = item["link"].strip()
        title_key = normalize_text(item["title"])

        if link in seen_links:
            continue
        if title_key in seen_titles:
            continue

        seen_links.add(link)
        seen_titles.add(title_key)
        unique.append(item)

    return unique


def pick_best_items(items, max_items=MAX_OUTPUT_ITEMS):
    items = sorted(items, key=lambda x: x["score"], reverse=True)
    return items[:max_items]


def summarize_japanese(client, title, summary, link):
    prompt = f"""
以下のニュースを日本語で要約してください。

要件:
- 必ず日本語
- 2文以内
- できるだけ自然でわかりやすく
- 誇張しない
- タイトルの直訳だけで終わらせない
- 40〜90文字くらいを目安
- URLは出さない

タイトル:
{title}

概要:
{summary}

URL:
{link}
""".strip()

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": "あなたはニュース要約アシスタントです。必ず簡潔で自然な日本語だけで答えてください。"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.3,
    )

    text = response.choices[0].message.content.strip()
    text = clean_text(text)
    return text


def fallback_japanese_text(title, summary):
    # API失敗時の最低限フォールバック
    base = clean_text(summary) or clean_text(title)
    if not base:
        return "AI関連ニュースを取得しました。"
    return f"{base[:85]}..." if len(base) > 85 else base


def build_discord_message(client, items):
    message = f"📡 AIニュース速報 ({now_jst_str()})\n\n"

    for i, item in enumerate(items, 1):
        try:
            jp_summary = summarize_japanese(
                client=client,
                title=item["title"],
                summary=item["summary"],
                link=item["link"]
            )
        except Exception as e:
            print(f"[WARN] summarize failed: {e}")
            jp_summary = fallback_japanese_text(item["title"], item["summary"])

        safe_link = suppress_discord_embeds(item["link"])
        message += f"{i}. {jp_summary}\n{safe_link}\n\n"

    return message.strip()


def post_to_discord(message):
    if not DISCORD_WEBHOOK_URL:
        print("Webhookなし")
        return

    for chunk in split_message(message):
        res = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": chunk},
            headers=REQUEST_HEADERS,
            timeout=30
        )
        res.raise_for_status()


def main():
    print("start")

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY が設定されていません")

    client = OpenAI(api_key=OPENAI_API_KEY)

    history = load_history()
    memory = load_memory()

    feed_items = fetch_feed_items(RSS_FEEDS, history)
    search_items = fetch_search_results(SEARCH_QUERIES, history)

    items = feed_items + search_items
    items = dedupe_items(items)
    items = sorted(items, key=lambda x: x["score"], reverse=True)
    items = items[:MAX_FINAL_CANDIDATES]
    items = pick_best_items(items, MAX_OUTPUT_ITEMS)

    if not items:
        message = f"📡 AIニュース速報 ({now_jst_str()})\n\n今日は条件に合うニュースが見つかりませんでした。"
        print(message)
        post_to_discord(message)
        return

    # 軽いメモリ更新
    memory["last_topics"] = [item["title"] for item in items[:5]]
    save_memory(memory)

    message = build_discord_message(client, items)

    print(message)
    post_to_discord(message)

    for item in items:
        history.add(item["link"])

    save_history(history)


if __name__ == "__main__":
    main()
