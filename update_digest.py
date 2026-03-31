#!/usr/bin/env python3
"""
AI Infra Daily — daily update script
Fetches fresh stories via NewsAPI, summarizes with Claude,
injects into index.html, and commits back to the repo.
"""

import os
import re
import json
import time
import datetime
import sys
import requests

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

NEWSAPI_KEY   = os.environ.get("NEWSAPI_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "")
HTML_FILE     = "index.html"

def check_secrets():
    if not NEWSAPI_KEY:
        print("ERROR: NEWSAPI_KEY secret is missing or empty")
        sys.exit(1)
    if not ANTHROPIC_KEY:
        print("ERROR: ANTHROPIC_KEY secret is missing or empty")
        sys.exit(1)
    print(f"Secrets present: NEWSAPI_KEY={NEWSAPI_KEY[:6]}... ANTHROPIC_KEY={ANTHROPIC_KEY[:6]}...")

# ---------------------------------------------------------------------------
# 1. FETCH NEWS
# ---------------------------------------------------------------------------

QUERIES = [
    "Broadcom AI networking switch",
    "NVIDIA GPU data center infrastructure",
    "Google TPU cloud infrastructure",
    "Arista networking AI data center",
    "Cisco AI infrastructure",
    "AWS Azure Google Cloud AI infrastructure",
    "Meta Oracle AI data center capex",
    "AI data center storage networking",
    "InfiniBand Ethernet AI cluster",
    "HBM memory AI server supply",
]

def fetch_articles(query, page_size=5):
    url = "https://newsapi.org/v2/everything"
    from_date = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
    params = {
        "q": query,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": page_size,
        "from": from_date,
        "apiKey": NEWSAPI_KEY,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        print(f"  NewsAPI '{query}': HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"  Response: {r.text[:200]}")
            return []
        data = r.json()
        articles = data.get("articles", [])
        print(f"  Got {len(articles)} articles")
        return articles
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
            title = a.get("title", "") or ""
            description = a.get("description", "") or ""
            if url and url not in seen_urls and title and description:
                if "[Removed]" in title:
                    continue
                seen_urls.add(url)
                raw.append({
                    "title": title,
                    "description": description,
                    "source": a.get("source", {}).get("name", "Unknown"),
                    "url": url,
                    "publishedAt": a.get("publishedAt", ""),
                })
        time.sleep(0.5)
    print(f"\nTotal unique raw articles: {len(raw)}")
    return raw

# ---------------------------------------------------------------------------
# 2. SUMMARIZE + CATEGORIZE WITH CLAUDE
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an AI infrastructure analyst briefing a Google Product Manager who owns Networking and Storage systems for GPU/TPU infrastructure.
Google vendors: Broadcom, NVIDIA, Cisco, Arista, Marvell.
Competitors: AWS, Azure, Meta, OCI, Oracle.

Select the 8-10 most relevant articles and return them as a JSON array.
Each object must have exactly these fields:
  id         - integer starting at 1
  cat        - one of: networking, silicon, hyperscaler, storage, competition, vendor
  impact     - one of: high, medium, low
  title      - rewritten headline, max 90 chars, specific and factual
  summary    - 2 sentences max, plain text
  detail     - 3-5 sentences of analysis, plain text, no markdown
  source     - publication name from input
  age        - one of: today, 1 day ago, 2 days ago, 3 days ago
  url        - original URL unchanged

Rules:
- Return ONLY a valid JSON array. No preamble, no explanation, no markdown fences.
- Do NOT use apostrophes inside any string value - rephrase to avoid them.
- Do NOT use backslashes inside any string value.
- If fewer than 8 relevant articles exist, return what you have."""

def call_claude(system, user_msg, max_tokens=4000):
    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": "claude-opus-4-5",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_msg}],
    }
    print(f"  Calling Claude (model={body['model']}, max_tokens={max_tokens})...")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers,
        json=body,
        timeout=90,
    )
    print(f"  Claude HTTP status: {r.status_code}")
    if r.status_code != 200:
        print(f"  Claude error: {r.text[:500]}")
        r.raise_for_status()
    data = r.json()
    text = data["content"][0]["text"].strip()
    print(f"  Claude returned {len(text)} chars")
    return text

def claude_select_and_summarize(raw_articles):
    trimmed = raw_articles[:40]
    articles_text = json.dumps([
        {
            "title": a["title"],
            "description": a["description"][:300],
            "source": a["source"],
            "url": a["url"],
            "publishedAt": a["publishedAt"],
        }
        for a in trimmed
    ], indent=2)

    today = datetime.date.today().isoformat()
    user_msg = f"Today is {today}. Select and process the best 8-10 from these articles:\n\n{articles_text}"

    text = call_claude(SYSTEM_PROMPT, user_msg, max_tokens=4000)

    # Strip any accidental markdown fences
    text = re.sub(r"^```json\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```\s*", "", text, flags=re.MULTILINE)
    text = text.strip()

    print(f"  Parsing JSON...")
    try:
        articles = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}")
        print(f"  Raw response (first 500 chars):\n{text[:500]}")
        raise

    print(f"  Claude selected {len(articles)} articles")
    return articles

# ---------------------------------------------------------------------------
# 3. GENERATE EXECUTIVE BRIEF
# ---------------------------------------------------------------------------

def generate_exec_brief(articles):
    summaries = "\n".join(
        f"- [{a['cat'].upper()}] {a['title']}: {a['summary']}"
        for a in articles
    )
    brief_system = (
        "You are a concise AI infrastructure analyst briefing a Google PM "
        "who owns Networking and Storage for GPU/TPU infrastructure. "
        "Do not use apostrophes or single quotes in your response."
    )
    user_msg = (
        f"Today stories:\n{summaries}\n\n"
        "Write a 3-sentence executive brief. "
        "Lead with the most important development for Google networking/storage, "
        "then the competitive signal, then the supply chain watch item. "
        "No bullets, flowing prose only. No apostrophes."
    )
    brief = call_claude(brief_system, user_msg, max_tokens=300)
    return brief

# ---------------------------------------------------------------------------
# 4. INJECT INTO HTML
# ---------------------------------------------------------------------------

def escape_for_js(s):
    s = str(s)
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\n", " ")
    s = s.replace("\r", "")
    return s

def build_articles_js(articles):
    lines = ["// ARTICLES_START", "const ARTICLES = ["]
    for a in articles:
        lines.append("  {")
        lines.append(f"    id: {a['id']}, cat: \"{a['cat']}\", impact: \"{a['impact']}\",")
        lines.append(f"    title: \"{escape_for_js(a['title'])}\",")
        lines.append(f"    summary: \"{escape_for_js(a['summary'])}\",")
        lines.append(f"    source: \"{escape_for_js(a['source'])}\", age: \"{escape_for_js(a['age'])}\",")
        lines.append(f"    detail: \"{escape_for_js(a['detail'])}\",")
        lines.append(f"    url: \"{escape_for_js(a['url'])}\"")
        lines.append("  },")
    lines.append("];")
    lines.append("// ARTICLES_END")
    return "\n".join(lines)

def inject_into_html(articles, brief):
    if not os.path.exists(HTML_FILE):
        print(f"ERROR: {HTML_FILE} not found. CWD: {os.getcwd()}")
        print(f"Files: {os.listdir('.')}")
        sys.exit(1)

    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    if "// ARTICLES_START" not in html:
        print("ERROR: // ARTICLES_START sentinel not found in index.html")
        sys.exit(1)

    new_js = build_articles_js(articles)
    html = re.sub(
        r"// ARTICLES_START\s*\nconst ARTICLES = \[.*?\];\s*\n// ARTICLES_END",
        new_js,
        html,
        flags=re.DOTALL,
    )

    escaped_brief = escape_for_js(brief)
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
    print(f"\n=== AI Infra Daily update — {datetime.date.today()} ===\n")

    print("Step 0: Checking secrets...")
    check_secrets()

    print("\nStep 1: Fetching news...")
    raw = gather_raw_articles()

    if not raw:
        print("WARNING: No articles fetched — keeping existing content")
        sys.exit(0)

    print(f"\nStep 2: Sending {len(raw)} articles to Claude...")
    articles = claude_select_and_summarize(raw)

    print("\nStep 3: Generating executive brief...")
    brief = generate_exec_brief(articles)

    print("\nStep 4: Injecting into HTML...")
    inject_into_html(articles, brief)

    print("\n=== Done! ===")

if __name__ == "__main__":
    main()
