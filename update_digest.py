#!/usr/bin/env python3
"""
AI Infra Daily — daily update script (Phase 1+2)
- Fetches fresh AI infra news via NewsAPI
- Deduplicates against seen_urls.json (never shows same article twice)
- Summarizes/categorizes with Claude
- Injects articles into index.html
- Saves daily archive for weekly report
- Commits everything back to the repo
"""

import os, re, json, time, datetime, sys, base64, requests

NEWSAPI_KEY   = os.environ.get("NEWSAPI_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "")
HTML_FILE     = "index.html"
SEEN_FILE     = "data/seen_urls.json"
ARCHIVE_DIR   = "data"

QUERIES = [
    "Broadcom AI networking switch data center",
    "NVIDIA GPU TPU data center infrastructure",
    "Google TPU cloud AI chip",
    "Arista networking AI hyperscaler",
    "Cisco AI data center silicon",
    "AWS Azure Google Cloud AI infrastructure investment",
    "Meta Oracle OCI AI data center",
    "AI data center storage HBM NVMe",
    "Ethernet InfiniBand AI cluster networking",
    "AI infrastructure supply chain chips 2026",
]

def check_env():
    if not NEWSAPI_KEY: print("ERROR: NEWSAPI_KEY missing"); sys.exit(1)
    if not ANTHROPIC_KEY: print("ERROR: ANTHROPIC_KEY missing"); sys.exit(1)
    print(f"Keys OK: NEWS={NEWSAPI_KEY[:6]}... ANTHROPIC={ANTHROPIC_KEY[:6]}...")

def load_seen_urls():
    if not os.path.exists(SEEN_FILE): return set()
    with open(SEEN_FILE) as f:
        data = json.load(f)
    # Prune URLs older than 90 days
    cutoff = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    return {url for url, date in data.items() if date >= cutoff}

def save_seen_urls(existing_seen, new_urls):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()
    data = {}
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            data = json.load(f)
    for url in new_urls:
        data[url] = today
    # Prune old entries
    cutoff = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    data = {url: date for url, date in data.items() if date >= cutoff}
    with open(SEEN_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"seen_urls.json updated: {len(data)} total URLs tracked")

def fetch_news():
    seen = load_seen_urls()
    print(f"Loaded {len(seen)} previously seen URLs")
    from_date = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
    raw = []
    seen_in_run = set()
    for q in QUERIES:
        try:
            r = requests.get("https://newsapi.org/v2/everything", params={
                "q": q, "language": "en", "sortBy": "publishedAt",
                "pageSize": 8, "from": from_date, "apiKey": NEWSAPI_KEY
            }, timeout=15)
            print(f"  '{q}': HTTP {r.status_code}")
            if r.status_code != 200: continue
            for a in r.json().get("articles", []):
                url = a.get("url", "")
                title = a.get("title", "") or ""
                desc  = a.get("description", "") or ""
                if not url or not title or not desc: continue
                if "[Removed]" in title: continue
                if url in seen: continue          # already shown before
                if url in seen_in_run: continue   # duplicate within this run
                seen_in_run.add(url)
                raw.append({
                    "title": title, "description": desc[:400],
                    "source": a.get("source", {}).get("name", "Unknown"),
                    "url": url, "publishedAt": a.get("publishedAt", "")
                })
        except Exception as e:
            print(f"  Error: {e}")
        time.sleep(0.4)
    print(f"Found {len(raw)} new unseen articles")
    return raw, seen

def call_claude(system, user_msg, max_tokens=4000):
    headers = {"Content-Type":"application/json","x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01"}
    body = {"model":"claude-opus-4-5","max_tokens":max_tokens,"system":system,"messages":[{"role":"user","content":user_msg}]}
    print(f"  Calling Claude ({max_tokens} tokens)...")
    r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, timeout=90)
    print(f"  HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  Error: {r.text[:300]}")
        r.raise_for_status()
    return r.json()["content"][0]["text"].strip()

SYSTEM_PROMPT = """You are an AI infrastructure analyst for a Google PM owning Networking and Storage for GPU/TPU infrastructure.
Vendors: Broadcom, NVIDIA, Cisco, Arista, Marvell. Competitors: AWS, Azure, Meta, OCI, Oracle.

Select the 8-10 most relevant articles and return a JSON array. Each object:
  id        - integer from 1
  cat       - one of: networking, silicon, hyperscaler, storage, competition, vendor
  impact    - one of: high, medium, low
  title     - rewritten headline max 90 chars
  summary   - 2 sentences plain text no apostrophes
  detail    - 3-5 sentences analysis plain text no apostrophes no markdown
  source    - publication name from input
  age       - one of: today, 1 day ago, 2 days ago, 3 days ago
  url       - original URL unchanged

Return ONLY valid JSON array. No preamble. No markdown fences. No apostrophes anywhere."""

def select_and_summarize(raw):
    today = datetime.date.today().isoformat()
    user_msg = f"Today is {today}. Select best 8-10:\n\n{json.dumps(raw[:40], indent=2)}"
    text = call_claude(SYSTEM_PROMPT, user_msg, 4000)
    text = re.sub(r"^```json\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```\s*", "", text, flags=re.MULTILINE)
    text = text.strip()
    try:
        articles = json.loads(text)
        print(f"Claude selected {len(articles)} articles")
        return articles
    except json.JSONDecodeError as e:
        print(f"JSON error: {e}\nRaw: {text[:400]}")
        raise

def generate_brief(articles):
    summaries = "\n".join(f"- [{a['cat'].upper()}] {a['title']}: {a['summary']}" for a in articles)
    system = "Concise AI infra analyst for Google PM owning Networking and Storage for GPU/TPU. No apostrophes in output."
    user = f"Stories:\n{summaries}\n\nWrite 3-sentence executive brief. Lead with most important for Google networking/storage PM, then competitive signal, then supply chain watch. No bullets. No apostrophes."
    return call_claude(system, user, 300)

def escape_js(s):
    s = str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", "")
    return s

def build_articles_js(articles):
    lines = ["// ARTICLES_START", "const ARTICLES = ["]
    for a in articles:
        lines.append("  {")
        lines.append(f'    id: {a["id"]}, cat: "{a["cat"]}", impact: "{a["impact"]}",')
        lines.append(f'    title: "{escape_js(a["title"])}",')
        lines.append(f'    summary: "{escape_js(a["summary"])}",')
        lines.append(f'    source: "{escape_js(a["source"])}", age: "{escape_js(a["age"])}",')
        lines.append(f'    detail: "{escape_js(a["detail"])}",')
        lines.append(f'    url: "{escape_js(a["url"])}"')
        lines.append("  },")
    lines.append("];")
    lines.append("// ARTICLES_END")
    return "\n".join(lines)

def inject_html(articles, brief):
    if not os.path.exists(HTML_FILE):
        print(f"ERROR: {HTML_FILE} not found. Files: {os.listdir('.')}"); sys.exit(1)
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()
    if "// ARTICLES_START" not in html:
        print("ERROR: ARTICLES_START sentinel missing"); sys.exit(1)
    html = re.sub(r"// ARTICLES_START\s*\nconst ARTICLES = \[.*?\];\s*\n// ARTICLES_END",
                  build_articles_js(articles), html, flags=re.DOTALL)
    escaped = escape_js(brief)
    html = re.sub(r"// BRIEF_START\s*\nconst STATIC_BRIEF = \".*?\";\s*\n// BRIEF_END",
                  f'// BRIEF_START\nconst STATIC_BRIEF = "{escaped}";\n// BRIEF_END',
                  html, flags=re.DOTALL)
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Injected {len(articles)} articles")

def save_daily_archive(articles):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()
    path = f"{ARCHIVE_DIR}/articles_{today}.json"
    with open(path, "w") as f:
        json.dump({"date": today, "articles": articles}, f, indent=2)
    print(f"Saved daily archive: {path}")

def main():
    print(f"\n=== AI Infra Daily — {datetime.date.today()} ===\n")
    check_env()

    print("\nStep 1: Fetching news...")
    raw, seen = fetch_news()
    if not raw:
        print("No new articles — keeping existing content"); sys.exit(0)

    print(f"\nStep 2: Summarizing {len(raw)} articles with Claude...")
    articles = select_and_summarize(raw)

    print("\nStep 3: Generating executive brief...")
    brief = generate_brief(articles)
    print(f"Brief: {brief[:80]}...")

    print("\nStep 4: Injecting into HTML...")
    inject_html(articles, brief)

    print("\nStep 5: Updating seen_urls.json...")
    new_urls = [a["url"] for a in articles]
    save_seen_urls(seen, new_urls)

    print("\nStep 6: Saving daily archive...")
    save_daily_archive(articles)

    print("\n=== Done! ===")

if __name__ == "__main__":
    main()
