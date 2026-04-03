#!/usr/bin/env python3
# AI Infra Daily - daily update script
# Sources: vendor/hyperscaler RSS blogs + NewsAPI
# Features: deduplication, daily archive, Claude summarization

import os, re, json, time, datetime, sys, requests
import xml.etree.ElementTree as ET
from html import unescape as html_unescape

NEWSAPI_KEY   = os.environ.get("NEWSAPI_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "")
HTML_FILE     = "index.html"
SEEN_FILE     = "data/seen_urls.json"
ARCHIVE_DIR   = "data"

# ---- NewsAPI keyword queries ----
QUERIES = [
    "Broadcom AI networking switch data center",
    "NVIDIA GPU TPU data center infrastructure",
    "Google TPU cloud AI chip",
    "Arista networking AI hyperscaler",
    "Cisco AI data center silicon",
    "AWS Azure Google Cloud AI infrastructure",
    "Meta Oracle OCI AI data center capex",
    "AI data center storage HBM NVMe",
    "Ethernet InfiniBand AI cluster networking",
    "AI infrastructure supply chain chips",
]

# ---- Vendor / hyperscaler RSS feeds ----
RSS_FEEDS = [
    ("Google Cloud Blog",      "https://cloudblog.withgoogle.com/rss"),
    ("Google Cloud Infra",     "https://cloud.google.com/blog/products/infrastructure/rss"),
    ("AWS News Blog",          "https://aws.amazon.com/blogs/aws/feed/"),
    ("AWS Networking",         "https://aws.amazon.com/blogs/networking-and-content-delivery/feed/"),
    ("AWS Storage",            "https://aws.amazon.com/blogs/storage/feed/"),
    ("Azure Blog",             "https://azure.microsoft.com/en-us/blog/feed/"),
    ("Azure Updates",          "https://www.microsoft.com/releasecommunications/api/v2/azure/rss"),
    ("Meta Engineering",       "https://engineering.fb.com/feed/"),
    ("Oracle Cloud Infra",     "https://blogs.oracle.com/cloud-infrastructure/rss"),
    ("NVIDIA Blog",            "https://feeds.feedburner.com/nvidiablog"),
    ("NVIDIA Newsroom",        "https://nvidianews.nvidia.com/releases.xml"),
    ("NVIDIA Developer Blog",  "https://developer.nvidia.com/blog/feed"),
    ("Broadcom Blog",          "https://www.broadcom.com/blog/rss"),
    ("Arista Blog",            "https://blogs.arista.com/blog/rss.xml"),
    ("Cisco Networking Blog",  "https://blogs.cisco.com/networking/feed"),
    ("Cisco Data Center Blog", "https://blogs.cisco.com/datacenter/feed"),
]

def check_env():
    if not NEWSAPI_KEY:
        print("ERROR: NEWSAPI_KEY missing"); sys.exit(1)
    if not ANTHROPIC_KEY:
        print("ERROR: ANTHROPIC_KEY missing"); sys.exit(1)
    print(f"Keys OK: NEWS={NEWSAPI_KEY[:6]}... ANTHROPIC={ANTHROPIC_KEY[:6]}...")

def load_seen_urls():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE) as f:
        data = json.load(f)
    cutoff = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    return {url for url, date in data.items() if date >= cutoff}

def save_seen_urls(seen, new_urls):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()
    data = {}
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            data = json.load(f)
    for url in new_urls:
        data[url] = today
    cutoff = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    data = {url: d for url, d in data.items() if d >= cutoff}
    with open(SEEN_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"seen_urls.json: {len(data)} total URLs tracked")

def clean(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", str(s))
    s = html_unescape(s)
    return " ".join(s.split())[:500]

def fetch_rss(seen, seen_in_run):
    raw = []
    for name, url in RSS_FEEDS:
        try:
            r = requests.get(url, timeout=12, headers={"User-Agent": "AIInfraDigest/1.0"})
            print(f"  RSS [{name}]: HTTP {r.status_code}")
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            items = root.findall(".//item") or root.findall(".//atom:entry", ns)
            count = 0
            for item in items[:15]:
                # URL
                link_el = item.find("link")
                item_url = ""
                if link_el is not None:
                    item_url = (link_el.text or link_el.get("href") or "").strip()
                if not item_url:
                    guid_el = item.find("guid")
                    if guid_el is not None:
                        item_url = (guid_el.text or "").strip()
                if not item_url or item_url in seen or item_url in seen_in_run:
                    continue
                # Title
                title_el = item.find("title")
                title = clean(title_el.text if title_el is not None else "")
                if not title or "[Removed]" in title:
                    continue
                # Description
                desc = ""
                for tag in ["description", "summary",
                            "{http://www.w3.org/2005/Atom}summary",
                            "{http://purl.org/rss/1.0/modules/content/}encoded"]:
                    el = item.find(tag)
                    if el is not None and el.text:
                        desc = clean(el.text)
                        break
                if not desc:
                    desc = title
                # Pub date
                pub_el = item.find("pubDate") or item.find("published") or \
                         item.find("{http://www.w3.org/2005/Atom}published")
                pub = (pub_el.text or "").strip() if pub_el is not None else ""

                seen_in_run.add(item_url)
                raw.append({
                    "title": title, "description": desc[:400],
                    "source": name, "url": item_url, "publishedAt": pub
                })
                count += 1
            print(f"    {count} new items")
        except Exception as e:
            print(f"  RSS error [{name}]: {e}")
        time.sleep(0.3)
    return raw

def fetch_newsapi(seen, seen_in_run):
    raw = []
    from_date = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
    for q in QUERIES:
        try:
            r = requests.get("https://newsapi.org/v2/everything", params={
                "q": q, "language": "en", "sortBy": "publishedAt",
                "pageSize": 8, "from": from_date, "apiKey": NEWSAPI_KEY
            }, timeout=15)
            print(f"  NewsAPI '{q[:40]}': HTTP {r.status_code}")
            if r.status_code != 200:
                continue
            for a in r.json().get("articles", []):
                url = a.get("url", "")
                title = (a.get("title") or "")
                desc  = (a.get("description") or "")
                if not url or not title or not desc:
                    continue
                if "[Removed]" in title:
                    continue
                if url in seen or url in seen_in_run:
                    continue
                seen_in_run.add(url)
                raw.append({
                    "title": title, "description": desc[:400],
                    "source": a.get("source", {}).get("name", "Unknown"),
                    "url": url, "publishedAt": a.get("publishedAt", "")
                })
        except Exception as e:
            print(f"  NewsAPI error: {e}")
        time.sleep(0.4)
    return raw

def fetch_news():
    seen = load_seen_urls()
    print(f"Previously seen URLs: {len(seen)}")
    seen_in_run = set()

    print("\n-- RSS blogs --")
    rss = fetch_rss(seen, seen_in_run)
    print(f"RSS subtotal: {len(rss)}")

    print("\n-- NewsAPI --")
    napi = fetch_newsapi(seen, seen_in_run)
    print(f"NewsAPI subtotal: {len(napi)}")

    combined = rss + napi
    print(f"\nTotal new articles: {len(combined)}")
    return combined, seen

def call_claude(system, user_msg, max_tokens=4000):
    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01"
    }
    body = {
        "model": "claude-opus-4-5",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_msg}]
    }
    print(f"  Claude API ({max_tokens} tokens)...")
    r = requests.post("https://api.anthropic.com/v1/messages",
                      headers=headers, json=body, timeout=90)
    print(f"  HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  Error: {r.text[:300]}")
        r.raise_for_status()
    return r.json()["content"][0]["text"].strip()

SYSTEM_PROMPT = (
    "You are an AI infrastructure analyst for a Google PM owning Networking and Storage "
    "for GPU/TPU infrastructure. "
    "Vendors: Broadcom, NVIDIA, Cisco, Arista, Marvell. "
    "Competitors: AWS, Azure, Meta, OCI, Oracle. "
    "Select the 8-10 most relevant articles and return a JSON array. Each object: "
    "id (integer from 1), "
    "cat (one of: networking silicon hyperscaler storage competition vendor), "
    "impact (one of: high medium low), "
    "title (rewritten headline max 90 chars no apostrophes), "
    "summary (2 sentences plain text no apostrophes), "
    "detail (3-5 sentences analysis plain text no apostrophes no markdown), "
    "source (publication name from input), "
    "age (one of: today / 1 day ago / 2 days ago / 3 days ago), "
    "url (original URL unchanged). "
    "Return ONLY valid JSON array. No preamble. No markdown fences. No apostrophes anywhere."
)

def select_and_summarize(raw):
    today = datetime.date.today().isoformat()
    # Send RSS articles first (higher quality), cap at 60 total
    user_msg = f"Today is {today}. Select best 8-10:\n\n{json.dumps(raw[:60], indent=2)}"
    text = call_claude(SYSTEM_PROMPT, user_msg, 4000)
    text = re.sub(r"^```json\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```\s*",     "", text, flags=re.MULTILINE)
    text = text.strip()
    try:
        articles = json.loads(text)
        print(f"Claude selected {len(articles)} articles")
        return articles
    except json.JSONDecodeError as e:
        print(f"JSON error: {e}\nRaw: {text[:400]}")
        raise

def generate_brief(articles):
    summaries = "\n".join(
        f"- [{a['cat'].upper()}] {a['title']}: {a['summary']}"
        for a in articles
    )
    system = ("Concise AI infra analyst for Google PM owning Networking and Storage "
              "for GPU/TPU. No apostrophes in output.")
    user = (f"Stories:\n{summaries}\n\n"
            "Write 3-sentence executive brief. Lead with most important for "
            "Google networking/storage PM, then competitive signal, then supply chain watch. "
            "No bullets. No apostrophes.")
    return call_claude(system, user, 300)

def escape_js(s):
    s = str(s).replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", " ").replace("\r", "")
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

    # Diagnose sentinel presence
    has_start = "// ARTICLES_START" in html
    has_end   = "// ARTICLES_END" in html
    print(f"Sentinel check: ARTICLES_START={has_start}, ARTICLES_END={has_end}")
    if not has_start or not has_end:
        print("ERROR: sentinels missing — cannot inject"); sys.exit(1)

    # Show what we're about to replace
    start_idx = html.index("// ARTICLES_START")
    end_idx   = html.index("// ARTICLES_END") + len("// ARTICLES_END")
    old_block = html[start_idx:end_idx]
    print(f"Old block length: {len(old_block)} chars")
    print(f"Old block first 120: {repr(old_block[:120])}")

    new_js = build_articles_js(articles)
    html_new = html[:start_idx] + new_js + html[end_idx:]

    changed = html_new != html
    print(f"HTML changed: {changed} (old={len(html)} chars, new={len(html_new)} chars)")
    if not changed:
        print("WARNING: HTML unchanged after injection — check sentinels match exactly")

    # Inject brief
    has_brief_start = "// BRIEF_START" in html_new
    has_brief_end   = "// BRIEF_END" in html_new
    print(f"Brief sentinel check: BRIEF_START={has_brief_start}, BRIEF_END={has_brief_end}")
    if has_brief_start and has_brief_end:
        escaped = escape_js(brief)
        bs = html_new.index("// BRIEF_START")
        be = html_new.index("// BRIEF_END") + len("// BRIEF_END")
        html_new = html_new[:bs] + f'// BRIEF_START\nconst STATIC_BRIEF = "{escaped}";\n// BRIEF_END' + html_new[be:]
        print("Brief injected")
    else:
        print("WARNING: Brief sentinels missing — skipping brief injection")

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html_new)
    print(f"Wrote {len(html_new)} chars to {HTML_FILE}")
    print(f"First article title: {articles[0]['title'] if articles else 'none'}")

def save_daily_archive(articles):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()
    path = f"{ARCHIVE_DIR}/articles_{today}.json"
    with open(path, "w") as f:
        json.dump({"date": today, "articles": articles}, f, indent=2)
    print(f"Saved archive: {path}")

def main():
    print(f"\n=== AI Infra Daily — {datetime.date.today()} ===\n")
    check_env()

    print("\nStep 1: Fetching from RSS blogs + NewsAPI...")
    raw, seen = fetch_news()
    if not raw:
        print("No new articles — keeping existing content")
        sys.exit(0)

    print(f"\nStep 2: Claude selecting + summarizing {len(raw)} candidates...")
    articles = select_and_summarize(raw)

    print("\nStep 3: Generating executive brief...")
    brief = generate_brief(articles)
    print(f"Brief: {brief[:80]}...")

    print("\nStep 4: Injecting into HTML...")
    inject_html(articles, brief)

    print("\nStep 5: Updating seen_urls.json...")
    save_seen_urls(seen, [a["url"] for a in articles])

    print("\nStep 6: Saving daily archive...")
    save_daily_archive(articles)

    print("\n=== Done! ===")

if __name__ == "__main__":
    main()
