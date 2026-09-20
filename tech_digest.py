#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import html
import json
import logging
import os
import re
import smtplib
from datetime import datetime
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from zoneinfo import ZoneInfo

import feedparser
import requests
from dotenv import load_dotenv

load_dotenv()

TIMEZONE = ZoneInfo("Asia/Shanghai")
TODAY = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
EMAIL_FROM = os.getenv("EMAIL_FROM", "15827508425@163.com")
EMAIL_TO = os.getenv("EMAIL_TO", "15827508425@163.com")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.163.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") or os.getenv("QQ_SMTP_AUTH_CODE")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv(
    "OPENAI_BASE_URL", "https://api.openai.com/v1/chat/completions"
)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
DIGEST_COUNT = 20

RSS_FEEDS = [
    {"name": "MIT Technology Review", "url": "https://www.technologyreview.com/feed/"},
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml"},
    {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index"},
    {"name": "Wired", "url": "https://www.wired.com/feed/rss"},
    {"name": "Nature", "url": "https://www.nature.com/nature.rss"},
    {"name": "NASA", "url": "https://www.nasa.gov/rss/dyn/breaking_news.rss"},
    {"name": "Google AI Blog", "url": "https://blog.research.google/feeds/posts/default"},
    {"name": "Microsoft Research", "url": "https://www.microsoft.com/en-us/research/feed/"},
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def entry_date(entry) -> str:
    for key in ("published", "updated", "created"):
        if entry.get(key):
            return entry[key]
    return ""


def entry_timestamp(entry) -> float:
    import calendar
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        if entry.get(key):
            return calendar.timegm(entry[key])
    return 0


def item_id(title: str, link: str) -> str:
    return hashlib.sha256(f"{title}|{link}".encode()).hexdigest()


def fetch_articles() -> list[dict]:
    articles, seen = [], set()
    headers = {"User-Agent": "Global-Tech-Digest/1.0"}

    for feed_info in RSS_FEEDS:
        try:
            logging.info("Fetching %s", feed_info["name"])
            response = requests.get(feed_info["url"], headers=headers, timeout=20)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            for entry in feed.entries[:30]:
                title = clean_text(entry.get("title", ""))
                link = entry.get("link", "").strip()
                if not title or not link:
                    continue
                identifier = item_id(title, link)
                if identifier in seen:
                    continue
                seen.add(identifier)
                articles.append({
                    "id": identifier,
                    "source": feed_info["name"],
                    "title": title,
                    "link": link,
                    "summary": clean_text(entry.get("summary") or entry.get("description") or "")[:1500],
                    "published": entry_date(entry),
                    "timestamp": entry_timestamp(entry),
                })
        except Exception as exc:
            logging.warning("Failed to fetch %s: %s", feed_info["name"], exc)

    articles.sort(key=lambda article: article["timestamp"], reverse=True)
    return articles[:100]


def fallback_digest(articles: list[dict]) -> list[dict]:
    return [
        {
            "rank": index,
            "category": "Technology",
            "title_cn": article["title"],
            "title_en": article["title"],
            "summary_cn": article["summary"] or "暂无摘要。",
            "summary_en": article["summary"] or "No summary available.",
            "why_important": "该信息来自全球科技媒体或研究机构，建议阅读原文了解详情。",
            "source": article["source"],
            "published": article["published"],
            "link": article["link"],
        }
        for index, article in enumerate(articles[:DIGEST_COUNT], 1)
    ]


def generate_digest(articles: list[dict]) -> list[dict]:
    if not OPENAI_API_KEY:
        logging.warning("OPENAI_API_KEY is not set; using fallback mode")
        return fallback_digest(articles)

    candidates = [
        {
            "id": index,
            "source": article["source"],
            "title": article["title"],
            "summary": article["summary"],
            "published": article["published"],
            "link": article["link"],
        }
        for index, article in enumerate(articles, 1)
    ]
    prompt = f'''从候选新闻中选出最重要的 {DIGEST_COUNT} 条全球科技新闻。优先选择重大影响力、技术突破、产业变化或政策意义的信息，去除重复、广告和低价值内容，尽量覆盖 AI、芯片、网络安全、机器人、航天、新能源、生物科技、互联网和科技政策。输出中文和英文标题、双���摘要、重要性说明，并保留 source、published 和 link。只输出合法 JSON 数组，不要 Markdown。格式：
[{{"rank":1,"category":"AI","title_cn":"中文标题","title_en":"English title","summary_cn":"中文摘要","summary_en":"English summary","why_important":"重要性说明","source":"来源","published":"发布时间","link":"原文链接"}}]
候选新闻：{json.dumps(candidates, ensure_ascii=False)}'''
    payload = {
        "model": OPENAI_MODEL,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "你只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
    }
    try:
        response = requests.post(
            OPENAI_BASE_URL,
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
        result = json.loads(content)
        if not isinstance(result, list):
            raise ValueError("AI response is not an array")
        return result[:DIGEST_COUNT]
    except Exception as exc:
        logging.exception("AI digest generation failed: %s", exc)
        return fallback_digest(articles)


def esc(value) -> str:
    return html.escape(str(value or ""))


def build_html(digest: list[dict]) -> str:
    sections = []
    for index, item in enumerate(digest, 1):
        sections.append(f'''<article style="margin-bottom:28px;padding-bottom:20px;border-bottom:1px solid #eee">
<h2>{esc(item.get("rank", index))}. {esc(item.get("title_cn"))}</h2>
<p><strong>{esc(item.get("title_en"))}</strong></p>
<p><strong>分类：</strong>{esc(item.get("category"))}<br><strong>来源：</strong>{esc(item.get("source"))}<br><strong>发布时间：</strong>{esc(item.get("published"))}</p>
<p><strong>中文摘要：</strong><br>{esc(item.get("summary_cn"))}</p>
<p><strong>English summary:</strong><br>{esc(item.get("summary_en"))}</p>
<p><strong>重要性：</strong>{esc(item.get("why_important"))}</p>
<p><a href="{esc(item.get("link"))}">阅读原文 / Read the original article</a></p>
</article>''')
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body style="font-family:Arial,'Microsoft YaHei',sans-serif;max-width:800px;margin:auto;line-height:1.7;color:#222"><h1>全球科技早报</h1><p>日期：{TODAY}<br>时间：北京时间 09:00<br>本期精选：{len(digest)} 条</p><hr>{"".join(sections)}<footer style="color:#777;font-size:13px">本邮件根据公开 RSS 信息自动生成；AI 摘要仅供参考，请点击原文核实。</footer></body></html>'''


def build_text(digest: list[dict]) -> str:
    lines = [f"全球科技早报｜{TODAY}", "北京时间 09:00", ""]
    for index, item in enumerate(digest, 1):
        lines += [
            f"{item.get('rank', index)}. {item.get('title_cn', '')}",
            f"   {item.get('title_en', '')}",
            f"   分类：{item.get('category', '')}",
            f"   来源：{item.get('source', '')}",
            f"   中文摘要：{item.get('summary_cn', '')}",
            f"   English summary: {item.get('summary_en', '')}",
            f"   重要性：{item.get('why_important', '')}",
            f"   原文：{item.get('link', '')}",
            "",
        ]
    return "\n".join(lines)


def send_email(digest: list[dict]) -> None:
    if not SMTP_PASSWORD:
        raise RuntimeError("请在 .env 中设置 SMTP_PASSWORD（163 邮箱 SMTP 授权码）")
    message = MIMEMultipart("alternative")
    message["Subject"] = Header(f"全球科技早报｜{TODAY}｜{len(digest)}条重要信息", "utf-8")
    message["From"] = formataddr((str(Header("全球科技早报", "utf-8")), EMAIL_FROM))
    message["To"] = EMAIL_TO
    message.attach(MIMEText(build_text(digest), "plain", "utf-8"))
    message.attach(MIMEText(build_html(digest), "html", "utf-8"))
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.login(EMAIL_FROM, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, [EMAIL_TO], message.as_string())


def main() -> None:
    articles = fetch_articles()
    if not articles:
        raise RuntimeError("没有获取到任何 RSS 新闻")
    digest = generate_digest(articles)
    if not digest:
        raise RuntimeError("没有生成摘要内容")
    send_email(digest)
    logging.info("Digest sent successfully")


if __name__ == "__main__":
    main()
