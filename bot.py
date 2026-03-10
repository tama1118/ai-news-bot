import os
import textwrap
from datetime import datetime, timezone

import feedparser
import requests
from openai import OpenAI

RSS_FEEDS = [
    "https://feeds.feedburner.com/venturebeat/SZYF",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://www.tomshardware.com/feeds/all",
]

KEYWORDS = [
    "local llm",
    "ollama",
    "lm studio",
    "qwen",
    "deepseek",
    "llama",
    "gemma",
    "vllm",
    "rag",
    "ai agent",
    "inference",
    "gpu",
]

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MAX_CANDIDATES = 20
MAX_OUTPUT_ITEMS = 5


def now_jst() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def normalize_text(text: str) -> str:
    return " ".join((text or "").lower().split())


def score_entry(title: str, summary: str) -> int:
    text = normalize_text(f"{title} {summary}")
    score = 0
    for kw in KEYWORDS:
        if kw in text:
            score += 1
    return score


def fetch_rss_items() -> list[dict]:
    items = []
    for url in RSS_FEEDS:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            title = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            link = getattr(entry, "link", "")
            scored = score_entry(title, summary)
            if scored > 0:
                items.append(
                    {
                        "title": title,
                        "summary": summary,
                        "link": link,
                        "score": scored,
                        "source": getattr(feed.feed, "title", url),
                    }
                )

    seen = set()
    unique_items = []
    for item in sorted(items, key=lambda x: x["score"], reverse=True):
        key = normalize_text(item["title"])
        if key in seen:
            continue
        seen.add(key)
        unique_items.append(item)

    return unique_items[:MAX_CANDIDATES]


def build_prompt(items: list[dict]) -> str:
    bullets = []
    for i, item in enumerate(items, start=1):
        bullets.append(
            textwrap.dedent(
                f"""
                [{i}]
                title: {item['title']}
                source: {item['source']}
                link: {item['link']}
                summary: {item['summary']}
                score: {item['score']}
                """
            ).strip()
        )

    joined = "\n\n".join(bullets)
    return textwrap.dedent(
        f"""
        あなたはAI/ローカルLLM/GPUニュースの編集者です。
        次の候補記事から、本当に重要なものだけを最大{MAX_OUTPUT_ITEMS}件選んで、日本語で配信文を作ってください。

        要件:
        - 日本語で出力
        - 誇張しない
        - ローカルLLM / 推論 / GPU / AIエージェント寄りの記事を優先
        - 同じ話題の重複はまとめる
        - 各項目は以下の形式
          1. 見出し
             - 何が起きたか
             - なぜ重要か
             - リンク
        - 最後に「今日のひとこと」を1つ入れる
        - 文章は実用的で簡潔

        候補記事:
        {joined}
        """
    ).strip()


def summarize_with_openai(items: list[dict]) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY が設定されていません。")
    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = build_prompt(items)

    response = client.responses.create(
        model=OPENAI_MODEL,
        input=prompt,
    )
    return response.output_text.strip()


def post_to_discord(message: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL 未設定のため、Discord送信をスキップします。")
        return

    chunks = []
    current = ""
    for line in message.splitlines(True):
        if len(current) + len(line) > 1800:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)

    for chunk in chunks:
        res = requests.post(DISCORD_WEBHOOK_URL, json={"content": chunk}, timeout=30)
        res.raise_for_status()


def main() -> None:
    print(f"[{now_jst()}] ニュース収集開始")
    items = fetch_rss_items()
    if not items:
        print("候補記事が見つかりませんでした。")
        return

    digest = summarize_with_openai(items)
    header = f"📡 AIニュース速報 ({now_jst()})\n"
    final_message = header + "\n" + digest

    print(final_message)
    post_to_discord(final_message)
    print("完了")


if __name__ == "__main__":
    main()
