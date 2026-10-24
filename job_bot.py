"""Job alert bot -> Telegram. Runs on GitHub Actions every ~5 min."""
import json, os, random, re, time, urllib.parse as up
from pathlib import Path
import requests
from bs4 import BeautifulSoup

KEYWORDS = ["call center", "كول سنتر", "live chat", "لايف شات",
            "customer service", "خدمة عملاء", "customer support", "chat support",
            "chat agent", "خدمة العملاء شات", "دعم عملاء"]
# A job title must contain at least one of these, otherwise it is ignored
RELEVANT = ["call cent", "contact cent", "customer", "live chat", "chat", "support",
            "help desk", "helpdesk", "telesales", "tele-sales",
            "كول", "خدمة", "عملاء", "شات", "اتصال", "دعم"]
# Jooble only: keep jobs located in Cairo, OR remote jobs (from anywhere)
CAIRO = ["cairo", "القاهرة", "new cairo", "nasr city", "maadi", "heliopolis", "shorouk",
         "obour", "sheraton", "mokattam", "rehab", "madinaty", "badr", "5th settlement",
         "fifth settlement", "zamalek", "downtown", "abbassia", "shubra", "helwan",
         "new nozha", "nozha", "ain shams", "manial", "capital",
         "giza", "الجيزة", "mohandessin", "mohandiseen", "المهندسين", "dokki", "الدقي",
         "haram", "الهرم", "agouza", "العجوزة", "faisal", "فيصل", "imbaba", "6th of october",
         "6 october", "sixth of october", "sheikh zayed", "zayed", "smart village"]
REMOTE = ["remote", "work from home", "work-from-home", "wfh", "home based", "home-based",
          "anywhere", "worldwide", "عن بعد", "ريموت", "من المنزل", "من البيت"]
# Text that means "fully remote" (used on the description only)
REMOTE_STRONG = ["fully remote", "100% remote", "remote position", "remote role", "remote job",
                 "work from home", "work-from-home", "work from anywhere", "عن بعد", "من المنزل"]
# Remote jobs limited to one country / region -> not useful from Egypt
RESTRICTED = ["united states", "u.s.", " usa", "us only", "us-based", "must reside", "must be located",
              "must live", "time zone", "timezone", "pacific time", "eastern time", "central time",
              "mountain time", "canada", "united kingdom", "uk only", "australia", "europe only",
              "eu only", "right to work", "work authorization", "authorized to work",
              "work authorisation", "citizen", "security clearance"]
# Skip ads whose title asks for strong English (edit freely)
EXCLUDE = ["fluent english", "excellent english", "german", "french", "c1", "c2"]

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
SEEN_FILE = Path("seen.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept-Language": "ar,en;q=0.8"}


def get(url, **kw):
    time.sleep(1.5)  # be polite, avoid rate limits
    try:
        from curl_cffi import requests as cr  # looks like a real Chrome browser
        r = cr.get(url, headers=HEADERS, impersonate="chrome", timeout=25)
    except ImportError:
        r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def txt(el):
    return el.get_text(" ", strip=True) if el else ""


def wuzzuf(kw):
    # Public landing pages (/a/...) are meant for crawlers; use them for English keywords
    if kw.isascii():
        slug = "-".join(w.capitalize() for w in kw.split())
        u = f"https://wuzzuf.net/a/{slug}-Jobs-in-Egypt"
    else:
        u = "https://wuzzuf.net/search/jobs/?a=hpb&q=" + up.quote(kw)
    for h in get(u).select("a[href*='/jobs/p/']"):
        if len(txt(h)) > 5:
            card = h.find_parent("div", class_=True)
            comp = card.select_one("a[href*='/jobs/careers/']") if card else None
            yield dict(title=txt(h), company=txt(comp), url=h["href"].split("?")[0], source="Wuzzuf")


LINKEDIN_KW = ("call center", "live chat", "customer service", "customer support")


def linkedin_get(url):
    """LinkedIn often answers 429 (too many requests) to GitHub servers: wait and retry."""
    for wait in (0, 20, 45):
        time.sleep(wait)
        try:
            return get(url)
        except Exception as e:
            if "429" not in str(e):
                raise
    raise RuntimeError("429 even after 3 tries")


def linkedin(kw):  # public guest endpoint, no login
    if kw not in LINKEDIN_KW:
        return
    time.sleep(random.uniform(2, 5))
    # r86400 = last 24 hours. Already-seen jobs are skipped, so a blocked run loses nothing:
    # the job is simply picked up on the next run that gets through.
    u = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
         + up.urlencode({"keywords": kw, "location": "Egypt", "f_TPR": "r86400",
                         "sortBy": "DD", "start": 0}))
    for li in linkedin_get(u).select("li"):
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


def jooble(kw):  # official free API: https://jooble.org/api/about
    key = os.environ.get("JOOBLE_API_KEY")
    if not key:
        return
    queries = [{"keywords": kw, "location": "Egypt"}]
    if kw in ("call center", "live chat", "customer service", "customer support"):
        queries.append({"keywords": kw + " remote"})  # no location: remote jobs from anywhere
    jobs = []
    for q in queries:
        r = requests.post(f"https://jooble.org/api/{key}", json=q, timeout=25)
        r.raise_for_status()
        jobs += r.json().get("jobs", [])
    for j in jobs:
        loc = (j.get("location") or "").lower()
        ttl = j.get("title", "").lower()
        snip = j.get("snippet", "").lower()
        in_cairo = any(x in loc for x in CAIRO)
        loc_is_remote = any(x in loc for x in REMOTE)
        # Remote: the LOCATION or the TITLE must say so. A description that only mentions
        # remote work is not enough, and a real on-site place (e.g. "Oregon State") never counts.
        remote_by_title = any(x in ttl for x in REMOTE) and (not loc or loc_is_remote)
        remote_by_text = (not loc or loc_is_remote) and any(x in snip for x in REMOTE_STRONG)
        is_remote = loc_is_remote or remote_by_title or remote_by_text
        restricted = any(x in " ".join([loc, ttl, snip]) for x in RESTRICTED)
        if in_cairo:
            pass
        elif not is_remote or restricted:
            continue  # on-site abroad / other governorate / remote but limited to one country
        yield dict(title=re.sub(r"<[^>]+>", "", j.get("title", "")).strip(),
                   company=j.get("company", ""), url=j["link"],
                   source="Jooble" + (f" ({j['source']})" if j.get("source") else ""))


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


BROWSER_KW = ["call center", "live chat", "customer service", "customer support",
              "كول سنتر", "خدمة عملاء"]


def browser_site(name, make_url, link_re=None):
    """Open the public page in a real headless Chrome (for sites that reject plain requests).
    If the site shows a block / challenge page we stop and report it, nothing more."""
    def run(keywords):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(user_agent=HEADERS["User-Agent"], locale="en-US",
                                viewport={"width": 1366, "height": 900})
            pg = ctx.new_page()
            for kw in keywords:
                if kw not in BROWSER_KW:
                    continue
                url = make_url(kw)
                resp = pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(3000)
                title = (pg.title() or "").lower()
                status = resp.status if resp else "?"
                if (resp and resp.status >= 400) or any(
                        x in title for x in ("just a moment", "access denied", "attention required", "forbidden")):
                    print(f"[warn] {name}: blocked (status {status}, title '{title[:40]}')")
                    break
                host = up.urlparse(url).netloc
                for t, h in pg.eval_on_selector_all("a", "e=>e.map(x=>[x.innerText.trim(), x.href])"):
                    if len(t) > 8 and up.urlparse(h).netloc == host and (link_re is None or re.search(link_re, h)):
                        parts = [x.strip() for x in t.split("\n") if x.strip()]
                        yield dict(title=parts[0], company=parts[1] if len(parts) > 1 else "",
                                   url=h.split("?")[0], source=name)
            b.close()
    run.__name__ = name
    run.all_kw = True
    return run


def _wuzzuf_url(kw):
    if kw.isascii():
        return "https://wuzzuf.net/a/" + "-".join(w.capitalize() for w in kw.split()) + "-Jobs-in-Egypt"
    return "https://wuzzuf.net/search/jobs/?a=hpb&q=" + up.quote(kw)


wuzzuf_b = browser_site("Wuzzuf", _wuzzuf_url, r"/jobs/p/")
tanqeeb_b = browser_site("Tanqeeb", lambda kw: "https://egypt.tanqeeb.com/jobs/search?keywords=" + up.quote(kw))


def _fa(slug):
    return "https://forasna.com/a/" + up.quote(slug)


# Public category pages on Forasna (found on the site itself): (url, trusted)
# trusted = the page is already call-center / support only, so skip the title filter
FORASNA_PAGES = [
    (_fa("وظائف-كول-سنتر-Call-Center-في-مصر"), True),
    (_fa("وظائف-خدمة-عملاء-في-مصر"), True),
    (_fa("وظائف-دعم-فنى-في-مصر"), True),
    (_fa("وظائف-عمل-من-المنزل"), False),  # remote: any place, but title must match
]


def forasna(_keywords):
    for url, trusted in FORASNA_PAGES:
        for a in get(url).select("a[href*='/job/p/']"):
            title = txt(a)
            if len(title) < 3:
                continue
            info = a.get("title", "")  # e.g. "وظيفة X فى شركة Y فى  الجيزة, مصر"
            loc = info.rsplit(" فى ", 1)[-1].strip()
            if trusted and not any(x in loc for x in ("القاهرة", "الجيزة")):
                continue  # on-site jobs only in Cairo / Giza (remote page is not filtered)
            m = re.search(r"فى شركة (.+?) فى ", info)
            yield dict(title=title, company=m.group(1) if m else "",
                       url=up.urljoin("https://forasna.com", a["href"]).split("?")[0],
                       source="Forasna", trusted=trusted)


forasna.all_kw = True


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
    linkedin, jooble, forasna, wuzzuf_b, tanqeeb_b,
    generic("Jobzella", "https://www.jobzella.com/jobs?q={q}", r"/jobs?/", "https://www.jobzella.com"),
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
                    if not j.get("trusted") and not any(x in low for x in RELEVANT):
                        continue  # title unrelated to call center / chat / support
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
