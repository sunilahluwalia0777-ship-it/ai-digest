#!/usr/bin/env python3
"""
AI Infra Daily — daily update script
Fetches fresh stories via NewsAPI, summarizes + categorizes with Claude,
then injects the new ARTICLES array into index.html.
Runs via GitHub Actions at 7am PT every day.
"""

import os
import re
import json
import time
import datetime
import requests

NEWSAPI_KEY  = os.environ["NEWSAPI_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_KEY"]
HTML_FILE    = "index.html"

# ---------------------------------------------------------------------------
# 1. FETCH NEWS
# ---------------------------------------------------------------------------

QUERIES = [
    "Broadcom AI networking switch",
    "NVIDIA GPU data center infrastructure",
    "Google TPU cloud infrastructure",
    "Arista networking AI",
    "Cisco AI infrastructure data center",
    "AWS Azure Google Cloud AI infrastructure",
    "Meta Oracle AI data center",
    "AI data center storage networking 2026",
    "InfiniBand Ethernet AI cluster",
    "HBM NAND memory AI server",
]

def fetch_articles(query, page_size=5):
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": query,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": page_size,
        "from": (datetime.date.today() - datetime.timedelta(days=3)).isoformat(),
        "apiKey": NEWSAPI_KEY,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json().get("articles", [])
    except Exception as e:
        print(f"  NewsAPI error for '{query}': {e}")
        return []

def gather_raw_articles():
    seen_urls = set()
    raw = []
    for q in QUERIES:
        articles = fetch_articles(q)
        for a in articles:
            url = a.get("url", "")
            if url and url not in seen_urls and a.get("title") and a.get("description"):
                seen_urls.add(url)
                raw.append({
                    "title": a["title"],
                    "description": a.get("description", ""),
                    "source": a.get("source", {}).get("name", "Unknown"),
                    "url": url,
                    "publishedAt": a.get("publishedAt", ""),
                })
        time.sleep(0.3)  # be polite to the API
    print(f"Fetched {len(raw)} unique raw articles")
    return raw

# ---------------------------------------------------------------------------
# 2. SUMMARIZE + CATEGORIZE WITH CLAUDE
# ---------------------------------------------------------------------------

CATEGORIES = ["networking", "silicon", "hyperscaler", "storage", "competition", "vendor"]

SYSTEM_PROMPT = """You are an AI infrastructure analyst for a Google Product Manager who owns Networking and Storage systems for GPU/TPU infrastructure.
Google's key vendors: Broadcom, NVIDIA, Cisco, Arista, Marvell, Juniper.
Competitors to watch: AWS, Azure, Meta, OCI, Oracle.
Focus areas: data center networking, storage systems, GPU/TPU infrastructure, silicon/chips, hyperscaler capex.

You will receive a list of raw news articles and must select the 8-10 most relevant and return them as a JSON array.
Each article must have exactly these fields:
  id         - integer starting at 1
  cat        - one of: networking, silicon, hyperscaler, storage, competition, vendor
  impact     - one of: high, medium, low
  title      - rewritten headline, max 90 chars, punchy and specific
  summary    - 2-sentence summary for the card, max 200 chars total, plain text
  detail     - 3-5 sentence detailed analysis for the brief sheet, plain text, no markdown
  source     - publication name (from the input)
  age        - human-readable age like "today", "1 day ago", "2 days ago"
  url        - original URL

Rules:
- Prefer stories published within the last 48 hours
- Prioritize HIGH impact stories about: networking fabric, switching, storage, GPU/TPU supply chain, hyperscaler capex, vendor announcements
- For 'competition' cat: stories about AWS/Azure/Meta/OCI infrastructure moves
- Skip paywalled, opinion-only, or clearly irrelevant articles
- Return ONLY valid JSON — no preamble, no markdown fences, no extra text
- The detail field must NOT contain single quotes — use double quotes or rephrase"""

def claude_select_and_summarize(raw_articles):
    # Build a compact input for Claude
    articles_text = json.dumps([
        {
            "title": a["title"],
            "description": a["description"],
            "source": a["source"],
            "url": a["url"],
            "publishedAt": a["publishedAt"],
        }
        for a in raw_articles
    ], indent=2)

    today = datetime.date.today().isoformat()
    user_msg = f"Today is {today}. Here are the raw articles. Select and process the best 8-10:\n\n{articles_text}"

    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 4000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_msg}],
    }

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers,
        json=body,
        timeout=60,
    )
    r.raise_for_status()
    text = r.json()["content"][0]["text"].strip()

    # Strip any accidental markdown fences
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    articles = json.loads(text)
    print(f"Claude selected {len(articles)} articles")
    return articles

# ---------------------------------------------------------------------------
# 3. GENERATE EXECUTIVE BRIEF WITH CLAUDE
# ---------------------------------------------------------------------------

def generate_exec_brief(articles):
    summaries = "\n".join(f"- [{a['cat'].upper()}] {a['title']}: {a['summary']}" for a in articles)
    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 400,
        "system": "You are a concise AI infrastructure analyst briefing a Google PM who owns Networking and Storage for GPU/TPU infrastructure. Be specific and actionable.",
        "messages": [{
            "role": "user",
            "content": f"Today's stories:\n{summaries}\n\nWrite a 3-sentence executive brief. Lead with the single most important development for Google networking/storage, then the competitive signal, then the supply chain watch item. No bullets, flowing prose only. No single quotes in output."
        }],
    }
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers,
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    brief = r.json()["content"][0]["text"].strip()
    print(f"Generated exec brief ({len(brief)} chars)")
    return brief

# ---------------------------------------------------------------------------
# 4. INJECT INTO HTML
# ---------------------------------------------------------------------------

def escape_for_js(s):
    """Escape a string so it's safe inside a JS single-quoted template literal."""
    return s.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")

def build_articles_js(articles):
    lines = ["// ARTICLES_START", "const ARTICLES = ["]
    for a in articles:
        lines.append("  {")
        lines.append(f"    id: {a['id']}, cat: '{a['cat']}', impact: '{a['impact']}',")
        lines.append(f"    title: \"{escape_for_js(a['title'])}\",")
        lines.append(f"    summary: \"{escape_for_js(a['summary'])}\",")
        lines.append(f"    source: \"{escape_for_js(a['source'])}\", age: \"{a['age']}\",")
        lines.append(f"    detail: \"{escape_for_js(a['detail'])}\",")
        lines.append(f"    url: \"{a['url']}\"")
        lines.append("  },")
    lines.append("];")
    lines.append("// ARTICLES_END")
    return "\n".join(lines)

def inject_articles(articles, brief):
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    # Replace ARTICLES block
    new_articles_js = build_articles_js(articles)
    html = re.sub(
        r"// ARTICLES_START\s*\nconst ARTICLES = \[.*?\];\s*\n// ARTICLES_END",
        new_articles_js,
        html,
        flags=re.DOTALL,
    )

    # Replace the static fallback brief text in generateBrief()
    escaped_brief = brief.replace("\\", "\\\\").replace("'", "\\'")
    html = re.sub(
        r"(briefEl\.textContent = ')[^']*(';\s*\n\s*\})",
        rf"\g<1>{escaped_brief}\g<2>",
        html,
    )

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Injected {len(articles)} articles into {HTML_FILE}")

# ---------------------------------------------------------------------------
# 5. MAIN
# ---------------------------------------------------------------------------

def main():
    print(f"=== AI Infra Daily update — {datetime.date.today()} ===")

    print("Fetching news...")
    raw = gather_raw_articles()

    if not raw:
        print("No articles fetched — aborting to preserve existing content")
        return

    print("Sending to Claude for selection + summarization...")
    articles = claude_select_and_summarize(raw)

    print("Generating executive brief...")
    brief = generate_exec_brief(articles)

    print("Injecting into HTML...")
    inject_articles(articles, brief)

    print("Done!")

if __name__ == "__main__":
    main()
