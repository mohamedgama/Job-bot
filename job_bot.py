"""Job alert bot -> Telegram. Runs on GitHub Actions every ~5 min."""
import json, os, re, urllib.parse as up
from pathlib import Path
import requests
from bs4 import BeautifulSoup

KEYWORDS = ["call center", "كول سنتر", "live chat", "لايف شات",
            "customer service", "خدمة عملاء", "customer support", "chat support",
            "chat agent", "خدمة العملاء شات", "دعم عملاء"]
# Skip ads whose title asks for strong English (edit freely)
EXCLUDE = ["fluent english", "excellent english", "german", "french", "c1", "c2"]

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
SEEN_FILE = Path("seen.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept-Language": "ar,en;q=0.8"}


def get(url, **kw):
    r = requests.get(url, headers=HEADERS, timeout=25, **kw)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def txt(el):
    return el.get_text(" ", strip=True) if el else ""


def wuzzuf(kw):
    s = get("https://wuzzuf.net/search/jobs/?a=hpb&q=" + up.quote(kw))
    for h in s.select("h2 a[href*='/jobs/p/']"):
        card = h.find_parent("div", class_=True)
        comp = card.select_one("a[href*='/jobs/careers/']") if card else None
        yield dict(title=txt(h), company=txt(comp), url=h["href"].split("?")[0], source="Wuzzuf")


def linkedin(kw):  # public guest endpoint, no login; r3600 = last hour
    u = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
         + up.urlencode({"keywords": kw, "location": "Egypt", "f_TPR": "r3600", "start": 0}))
    for li in get(u).select("li"):
        a = li.select_one("a.base-card__full-link")
        if a:
            yield dict(title=txt(li.select_one(".base-search-card__title")),
                       company=txt(li.select_one(".base-search-card__subtitle")),
                       url=a["href"].split("?")[0], source="LinkedIn")


def bayt(kw):
    slug = re.sub(r"\s+", "-", kw.strip().lower())
    s = get(f"https://www.bayt.com/en/egypt/jobs/{up.quote(slug)}-jobs/")
    for h in s.select("li[data-js-job] h2 a"):
        yield dict(title=txt(h), company="", url=up.urljoin("https://www.bayt.com", h["href"]), source="Bayt")


def jooble(kw):
    slug = re.sub(r"\s+", "-", kw.strip().lower())
    s = get(f"https://eg.jooble.org/jobs-{up.quote(slug)}")
    for a in s.find_all("a", href=re.compile(r"/desc/|/away/")):
        if len(txt(a)) > 8:
            yield dict(title=txt(a), company="", url=up.urljoin("https://eg.jooble.org", a["href"]), source="Jooble")


def shaghalni_all(keywords):
    """Shaghalni renders results with JavaScript, so we use a headless browser."""
    from playwright.sync_api import sync_playwright
    skip = ["الأقسام", "إتصل", "الشروط", "سياسة", "facebook", "twitter", "instagram", "javascript"]
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        for kw in keywords:
            q = up.quote(kw)
            pg.goto("https://www.shaghalni.com/%D9%88%D8%B8%D8%A7%D8%A6%D9%81"
                    f"?searchStr={q}&searchCity=1&category=&city=1&str={q}&page=1&get_filters=0",
                    wait_until="networkidle", timeout=45000)
            for t, h in pg.eval_on_selector_all("a", "e=>e.map(x=>[x.innerText.trim(), x.href])"):
                d = up.unquote(h)
                if (h.startswith("https://www.shaghalni.com/") and len(t) > 8
                        and not any(x in d for x in skip)):
                    yield dict(title=t.split("\n")[0], company="", url=h.split("?")[0], source="Shaghalni")
        b.close()


shaghalni_all.all_kw = True


def generic(name, url_tpl, link_re, base):
    """Fallback for simple sites: any link whose href matches link_re."""
    def run(kw):
        s = get(url_tpl.format(q=up.quote(kw)))
        for a in s.find_all("a", href=re.compile(link_re)):
            if len(txt(a)) > 8:
                yield dict(title=txt(a), company="", url=up.urljoin(base, a["href"]), source=name)
    return run


# ⚠️ generic ones are best-effort: verify the URL pattern on each site
SOURCES = [
    wuzzuf, linkedin, bayt, jooble,
    generic("Forasna", "https://forasna.com/jobs/search?q={q}", r"/job/|/jobs/\d", "https://forasna.com"),
    generic("Jobzella", "https://www.jobzella.com/jobs?q={q}", r"/jobs?/", "https://www.jobzella.com"),
    generic("Tanqeeb", "https://egypt.tanqeeb.com/jobs/search?keywords={q}", r"/jobs?/", "https://egypt.tanqeeb.com"),
    shaghalni_all,
]


def notify(j):
    msg = f"🆕 {j['title']}\n🏢 {j['company'] or '-'}\n🌐 {j['source']}\n🔗 {j['url']}"
    if not TOKEN:
        print(msg, "\n"); return
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  data={"chat_id": CHAT, "text": msg}, timeout=20)


def main():
    first_run = not SEEN_FILE.exists()
    seen = set(json.loads(SEEN_FILE.read_text())) if not first_run else set()
    new = []
    for src in SOURCES:
        runs = [KEYWORDS] if getattr(src, "all_kw", False) else KEYWORDS
        for kw in runs:
            try:
                for j in src(kw):
                    low = j["title"].lower()
                    if j["url"] in seen or any(x in low for x in EXCLUDE):
                        continue
                    seen.add(j["url"]); new.append(j)
            except Exception as e:
                print(f"[warn] {getattr(src, '__name__', 'src')} / {kw}: {e}")
    if not first_run:  # first run only builds the baseline, no spam
        for j in new:
            notify(j)
    print(f"{len(new)} new ({'baseline' if first_run else 'sent'})")
    SEEN_FILE.write_text(json.dumps(sorted(seen)[-5000:], ensure_ascii=False))


if __name__ == "__main__":
    main()
