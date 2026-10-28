"""Job alert bot -> Telegram. Runs on GitHub Actions every ~5 min."""
import json, os, random, re, time, urllib.parse as up
from pathlib import Path
import requests
from bs4 import BeautifulSoup

KEYWORDS = ["call center", "كول سنتر", "live chat", "لايف شات",
            "customer service", "خدمة عملاء", "customer support", "chat support",
            "chat agent", "خدمة العملاء شات", "دعم عملاء",
            "copywriter", "content writer", "كوبي رايتر", "كاتب محتوى"]
# A job title must contain at least one of these, otherwise it is ignored
RELEVANT = ["call cent", "contact cent", "customer", "live chat", "chat", "support",
            "help desk", "helpdesk", "telesales", "tele-sales",
            "كول", "خدمة", "عملاء", "شات", "اتصال", "دعم",
            "copywriter", "copy writer", "content writer", "كوبي", "كاتب محتوى", "كتابة محتوى"]
# Jooble only: keep jobs located in Cairo, OR remote jobs (from anywhere)
CAIRO = ["cairo", "القاهرة", "new cairo", "nasr city", "maadi", "heliopolis", "shorouk",
         "obour", "sheraton", "mokattam", "rehab", "madinaty", "badr", "5th settlement",
         "fifth settlement", "zamalek", "downtown", "abbassia", "shubra", "helwan",
         "new nozha", "nozha", "ain shams", "manial", "capital",
         "giza", "الجيزة", "mohandessin", "mohandiseen", "المهندسين", "dokki", "الدقي",
         "haram", "الهرم", "agouza", "العجوزة", "faisal", "فيصل", "imbaba", "6th of october",
         "6 october", "sixth of october", "sheikh zayed", "zayed", "smart village"]
OTHER_GOV = ["alexandria", "الإسكندرية", "الاسكندرية", "mansoura", "المنصورة", "tanta", "طنطا",
             "assiut", "أسيوط", "اسيوط", "sohag", "سوهاج", "luxor", "الأقصر", "aswan", "أسوان",
             "hurghada", "الغردقة", "sharm", "شرم", "ismailia", "الإسماعيلية", "suez", "السويس",
             "port said", "بورسعيد", "zagazig", "الزقازيق", "damietta", "دمياط", "minya", "المنيا",
             "fayoum", "الفيوم", "beni suef", "بني سويف", "kafr el", "كفر الشيخ", "damanhour",
             "دمنهور", "10th of ramadan", "العاشر من رمضان", "borg el arab", "برج العرب"]
REMOTE = ["remote", "work from home", "work-from-home", "wfh", "home based", "home-based",
          "anywhere", "worldwide", "عن بعد", "ريموت", "من المنزل", "من البيت"]
# Text that means "fully remote" (used on the description only)
REMOTE_STRONG = ["fully remote", "100% remote", "remote position", "remote role", "remote job",
                 "work from home", "work-from-home", "work from anywhere", "عن بعد", "من المنزل"]
# A remote job is kept only if the ad itself says people from Egypt / anywhere can apply
GLOBAL = ["worldwide", "world wide", "anywhere in the world", "work from anywhere", "work anywhere",
          "international candidates", "any country", "all countries", "emea", "mena", "middle east",
          "north africa", "egypt", "cairo", "arabic", "مصر", "القاهرة", "من أي مكان", "العالم"]
# Remote jobs limited to one country / region -> not useful from Egypt
RESTRICTED = ["united states", "u.s.", " usa", "us only", "us-based", "must reside", "must be located",
              "must live", "time zone", "timezone", "pacific time", "eastern time", "central time",
              "mountain time", "canada", "united kingdom", "uk only", "australia", "europe only",
              "eu only", "right to work", "work authorization", "authorized to work",
              "work authorisation", "citizen", "security clearance",
              "local presence", "must be local", "hybrid", "on-site", "onsite", "on site", "in-office"]
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


def _ascii_kw():
    """English keywords (max 12) for sites that are strict about request volume."""
    return [k for k in KEYWORDS if k.isascii()][:12]



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
    if kw not in _ascii_kw():
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
    if not key or kw not in KEYWORDS[:24]:
        return
    queries = [{"keywords": kw, "location": "Egypt"}]
    if kw in _ascii_kw():
        queries.append({"keywords": kw + " remote"})  # no location: remote jobs from anywhere
    jobs = []
    for q in queries:
        r = requests.post(f"https://jooble.org/api/{key}", json=q, timeout=25)
        r.raise_for_status()
        jobs += r.json().get("jobs", [])
    kept = 0
    places = []
    for j in jobs:
        loc = (j.get("location") or "").lower()
        if loc not in places and len(places) < 6:
            places.append(loc)
        ttl = j.get("title", "").lower()
        snip = j.get("snippet", "").lower()
        in_cairo = any(x in loc for x in CAIRO)
        if not in_cairo and loc.strip() in ("egypt", "مصر", "egypt, egypt"):
            # Jooble gives only the country. Keep it unless the ad names another governorate.
            text = " ".join([ttl, snip])
            in_cairo = not any(x in text for x in OTHER_GOV)
        loc_is_remote = any(x in loc for x in REMOTE)
        # Remote: the LOCATION or the TITLE must say so. A description that only mentions
        # remote work is not enough, and a real on-site place (e.g. "Oregon State") never counts.
        remote_by_title = any(x in ttl for x in REMOTE) and (not loc or loc_is_remote)
        remote_by_text = (not loc or loc_is_remote) and any(x in snip for x in REMOTE_STRONG)
        is_remote = loc_is_remote or remote_by_title or remote_by_text
        restricted = any(x in " ".join([loc, ttl, snip]) for x in RESTRICTED)
        global_ok = any(x in " ".join([loc, ttl, snip]) for x in GLOBAL)
        note = ""
        if in_cairo:
            pass
        elif restricted or not is_remote:
            continue  # on-site abroad / other governorate / remote limited to one country
        elif not global_ok:
            continue  # "Remote" without any sign that Egypt/worldwide is allowed: Jooble shows these
                      # as "requires local presence", so they are not real remote jobs for you
        kept += 1
        yield dict(note=note, title=re.sub(r"<[^>]+>", "", j.get("title", "")).strip(),
                   company=j.get("company", ""), url=j["link"],
                   source="Jooble" + (f" ({j['source']})" if j.get("source") else ""))
    print(f"[info] jooble '{kw}': api gave {len(jobs)}, kept {kept}, places e.g. {places}")


def _diag(name, pg, anchors, hide=()):
    """Print what the browser really sees, so the parser can be fixed from real data."""
    print(f"[info] {name}: title='{(pg.title() or '')[:50]}' anchors={len(anchors)} url={up.unquote(pg.url)[:90]}")
    shown, seen_h = 0, set()
    for t, h in anchors:
        d = up.unquote(h)
        if any(x in d for x in hide) or d in seen_h:
            continue
        seen_h.add(d)
        print(f"[info]   {t[:40]!r} -> {d[:110]}")
        shown += 1
        if shown >= 25:
            break


def _anchors(pg):
    pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")  # trigger lazy loading
    pg.wait_for_timeout(1500)
    return pg.eval_on_selector_all("a", "e=>e.map(x=>[x.innerText.trim(), x.href])")


def shaghalni_all(keywords):
    """Shaghalni loads jobs from its own JSON API; we open the page in a browser and read that answer."""
    from playwright.sync_api import sync_playwright
    # Arabic keywords (max 8) + the newest English ones (max 6)
    kws = [k for k in keywords if not k.isascii()][:8] + [k for k in keywords if k.isascii()][:6]
    got = []

    def on_resp(r):
        if "/api/job/jobs?" in r.url and "json" in r.headers.get("content-type", ""):
            try:
                got.append(r.json())
            except Exception:
                pass

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_context(user_agent=HEADERS["User-Agent"], locale="ar-EG").new_page()
        pg.on("response", on_resp)
        job_base = "وظائف"  # confirmed by testing: job pages are shaghalni.com/وظائف/<slug>
        for i, kw in enumerate(kws):
            got.clear()
            q = up.quote(kw)
            page_url = ("https://www.shaghalni.com/%D9%88%D8%B8%D8%A7%D8%A6%D9%81"
                        f"?searchStr={q}&searchCity=1&category=&city=1&str={q}&page=1&get_filters=0")
            pg.goto(page_url, wait_until="networkidle", timeout=45000)
            pg.wait_for_timeout(2500)
            items = [it for d in list(got) for it in (d.get("data") or []) if isinstance(it, dict)]
            print(f"[info] shaghalni '{kw}': {len(items)} jobs from API")
            if job_base is None and items:
                # Find the real job-page address: try the usual patterns, keep the one that
                # opens a page showing this job's title
                slug, t0 = str(items[0].get("slug")), str(items[0].get("title"))
                print(f"[info]   sample: {t0!r} | {items[0].get('address_string')!r} | slug={slug!r}")
                for pref in ("وظيفة", "وظائف", "job", "jobs", ""):
                    u = "https://www.shaghalni.com/" + (up.quote(pref) + "/" if pref else "") + up.quote(slug)
                    try:
                        r = pg.goto(u, wait_until="networkidle", timeout=30000)
                        pg.wait_for_timeout(1500)
                        ok = bool(r and r.status < 400 and t0 in pg.inner_text("body")
                                  and "searchStr" not in pg.url)
                        print(f"[info]   try /{pref}/slug -> status {r.status if r else '?'} ok={ok}")
                        if ok:
                            job_base = pref
                            break
                    except Exception as e:
                        print("[info]   try failed:", str(e)[:60])
                if job_base is None:
                    job_base = False  # not found, use the search-page link
            for it in items:
                addr = str(it.get("address_string") or "")
                if any(x in addr for x in OTHER_GOV):
                    continue  # another governorate
                title = str(it.get("title") or it.get("short_title") or "").strip()
                if not title:
                    continue
                if job_base is not False and job_base is not None and it.get("slug"):
                    link = ("https://www.shaghalni.com/" + (up.quote(job_base) + "/" if job_base else "")
                            + up.quote(str(it["slug"])))
                    note = ""
                else:
                    link = f"{page_url}#job-{it.get('id')}"
                    note = "🔎 اللينك بيفتح نتايج البحث في شغلني (دور على اسم الوظيفة)"
                yield dict(title=title, company=str(it.get("employer_name") or ""),
                           url=link, note=note, source="Shaghalni")
        b.close()


shaghalni_all.all_kw = True


def jobzella_all(keywords):
    """Jobzella is a JavaScript app; first we only look at what it shows (see [info] lines)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_context(user_agent=HEADERS["User-Agent"], locale="en-US").new_page()
        for url in ("https://www.jobzella.com/search/jobs", "https://www.jobzella.com/search/jobs?q=call%20center"):
            pg.goto(url, wait_until="networkidle", timeout=45000)
            pg.wait_for_timeout(2000)
            anchors = _anchors(pg)
            _diag("jobzella", pg, anchors, hide=["facebook", "instagram", "twitter", "youtube", "linkedin",
                                                 "/auth/", "about-us", "contact-us", "/search/companies",
                                                 "/search/people", "blog.", "jobfair.", "job-posting"])
            for t, h in anchors:
                if "/job" in up.unquote(h).lower() and len(t) > 8 and "jobzella.com" in h:
                    parts = [x.strip() for x in t.split("\n") if x.strip()]
                    yield dict(title=parts[0], company=parts[1] if len(parts) > 1 else "",
                               url=h.split("?")[0], source="Jobzella")
        b.close()


jobzella_all.all_kw = True


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


# Public category pages on Forasna: (url, trusted)
# trusted = the page is already call-center / support only, so skip the title filter
FORASNA_PAGES = [
    (_fa("وظائف-كول-سنتر-في-القاهرة"), True),
    (_fa("وظائف-كول-سنتر-Call-Center-في-مصر"), True),
    (_fa("وظائف-خدمة-عملاء-في-مصر"), True),
    (_fa("وظائف-دعم-فنى-في-مصر"), True),
    (_fa("وظائف-عمل-من-المنزل"), False),  # remote: any place, but the title must match
    # copywriting pages (address guessed from the site's pattern; skipped with a warning if missing)
    (_fa("وظائف-كوبي-رايتر-في-القاهرة"), True),
    (_fa("وظائف-كوبي-رايتر-في-مصر"), True),
]


def forasna(keywords):
    pages = list(FORASNA_PAGES)
    have = {u for u, _ in pages}
    for kw in [k for k in keywords if not k.isascii()][:10]:
        # Forasna has no search box we can use, but it has a page per job title: /a/وظائف-<كلمة>-في-مصر
        url = _fa("وظائف-" + re.sub(r"\s+", "-", kw.strip()) + "-في-مصر")
        if url not in have:
            have.add(url)
            pages.append((url, True))
    for url, trusted in pages:
        try:
            found = get(url).select("a[href*='/job/p/']")
        except Exception as e:
            msg = str(e)
            if "404" in msg:
                print(f"[info] forasna no page for {up.unquote(url)[-34:]}")
            else:
                print(f"[warn] forasna page {up.unquote(url)[-30:]}: {msg[:60]}")
            continue
        kept = 0
        for a in found:
            title = txt(a)
            if len(title) < 3:
                continue  # image links
            full = up.unquote(a["href"])  # the slug ends with the place, e.g. ...-الجيزة-444128
            if trusted and not any(x in full for x in ("القاهرة", "الجيزة")):
                continue  # on-site jobs only in Cairo / Giza (the remote page is not filtered)
            kept += 1
            yield dict(title=title, company="", note="" if trusted else "🏠 من صفحة العمل من المنزل",
                       url=up.urljoin("https://forasna.com", a["href"]).split("?")[0],
                       source="Forasna", trusted=trusted)
        print(f"[info] forasna {up.unquote(url)[-38:]}: {len(found)} links, kept {kept}")


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
    linkedin, jooble, forasna,
    shaghalni_all,
]


# Keywords added from this file. Each batch is applied ONCE (the saved settings win after that),
# so /remove from Telegram keeps working. To add more later, add a new batch with a new name.
PRESET_BATCHES = {
    "medical-1": ["medical claims", "medical approval", "medical scribe",
                  "medical call center", "home care", "medical case management"],
    # Arabic versions (my guesses of how Egyptian ads write them). Remove any with /remove
    "medical-ar": ["مطالبات طبية", "موافقات طبية", "رعاية منزلية", "تمريض منزلي",
                   "ادارة حالات طبية", "كول سنتر طبي"],
}

START = time.time()
DEFAULTS_VERSION = 2
NEW_DEFAULTS = {  # added in version 2
    "keywords": ["medical claims specialist", "medical approval specialist", "medical scribe",
                 "medical account call center", "home care specialist", "medical case management"],
    "relevant": ["medical claims", "medical approval", "medical scribe", "medical account",
                 "home care", "medical case", "case management"],
}
TIME_BUDGET = 420      # seconds: stop starting new searches after 7 minutes
MAX_SEND = 30          # max messages per run (Telegram rate limits); the rest go next run
MAX_SEEN = 8000


def tg_send(text):
    """True only if Telegram really accepted the message. Waits when Telegram says 'slow down'."""
    if not (TOKEN and CHAT):
        print(text, "\n")
        return True
    for _ in range(4):
        try:
            r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                              data={"chat_id": CHAT, "text": text[:4000]}, timeout=20)
        except Exception as e:
            print("[warn] telegram send:", str(e)[:60])
            time.sleep(3)
            continue
        if r.status_code == 200:
            return True
        if r.status_code == 429:
            try:
                wait = r.json().get("parameters", {}).get("retry_after", 5)
            except Exception:
                wait = 5
            time.sleep(min(wait + 1, 60))
            continue
        print("[warn] telegram", r.status_code, r.text[:80])
        return False
    return False


def notify(j):
    msg = f"🆕 {j['title']}\n🏢 {j['company'] or '-'}\n🌐 {j['source']}"
    if j.get("note"):
        msg += f"\n{j['note']}"
    msg += f"\n🔗 {j['url']}"
    return tg_send(msg)


def send(text):
    tg_send(text)


def is_relevant(low):
    for e in RELEVANT:
        words = e.split()
        if words and all(w in low for w in words):  # "data entry" also matches "Entry Data Clerk"
            return True
    return False


def is_excluded(low):
    for x in EXCLUDE:
        if not x:
            continue
        if x.isascii():  # whole word only, so "c1" does not hit "ABC123"
            if re.search(r"(?<![a-z0-9])" + re.escape(x) + r"(?![a-z0-9])", low):
                return True
        elif x in low:
            return True
    return False


def same_job_key(j):
    if j.get("company"):  # same job posted on two sites
        return "k|" + re.sub(r"\s+", " ", j["title"].lower()).strip() + "|" + j["company"].lower().strip()
    return None


HELP = ("أوامر التحكم (ابعتها للبوت هنا):\n"
        "/add كلمة ← يدور على كلمة جديدة (مثال: /add محاسب)\n"
        "/remove كلمة ← يشيل كلمة\n"
        "/exclude كلمة ← يستبعد أي وظيفة عنوانها فيه الكلمة دي\n"
        "/unexclude كلمة ← يرجع الكلمة المستبعدة\n"
        "/list ← يعرض الإعدادات الحالية\n"
        "التغيير بيتطبق في الدورة الجاية (خلال دقايق).")


def apply_command(st, text):
    cmd, _, arg = text.partition(" ")
    cmd, arg = cmd.lower().split("@")[0], arg.strip()
    low = arg.lower()
    if cmd in ("/start", "/help"):
        return HELP
    if cmd == "/list":
        return ("🔎 كلمات البحث:\n" + "\n".join("• " + k for k in st["keywords"])
                + "\n\n🚫 المستبعد من العناوين:\n"
                + ("\n".join("• " + k for k in st["exclude"]) or "(مفيش)"))
    if cmd in ("/add", "/remove", "/exclude", "/unexclude") and not arg:
        return "اكتب الكلمة بعد الأمر، مثال: " + cmd + " محاسب"
    items = [x.strip() for x in re.split(r"[,\n\u060c]", arg) if x.strip()]
    if cmd == "/add":
        added, bad = [], []
        for it in items:
            if len(it) < 3 or len(it) > 40:
                bad.append(it)
                continue
            if it.lower() not in [k.lower() for k in st["keywords"]]:
                st["keywords"].insert(0, it)  # newest first, so it is always searched
            if it.lower() not in st["relevant"]:
                st["relevant"].append(it.lower())
            added.append(it)
        msg = ("✅ ضفت: " + "، ".join(added)
               + "\nهتشوف نتايجها في الدورة الجاية (ممكن وظايف كتير مرة واحدة).") if added else ""
        if bad:
            msg += "\n⚠️ اتجاهلت (لازم من 3 لـ 40 حرف): " + "، ".join(bad)
        return msg.strip()
    if cmd == "/remove":
        lows = [x.lower() for x in items]
        st["keywords"] = [k for k in st["keywords"] if k.lower() not in lows]
        st["relevant"] = [k for k in st["relevant"] if k not in lows]
        return "🗑 شيلت: " + "، ".join(items)
    if cmd == "/exclude":
        ok = [x.lower() for x in items if len(x) >= 2]
        for x in ok:
            if x not in st["exclude"]:
                st["exclude"].append(x)
        return ("🚫 هستبعد أي وظيفة عنوانها فيه: " + "، ".join(ok)) if ok else "الكلمة قصيرة جداً."
    if cmd == "/unexclude":
        lows = [x.lower() for x in items]
        st["exclude"] = [k for k in st["exclude"] if k not in lows]
        return "↩️ رجّعت: " + "، ".join(items)
    return None  # not a command we know: ignore


def handle_commands(state):
    """Read messages you sent to the bot and apply them (only from YOUR chat)."""
    if not (TOKEN and CHAT):
        return
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                         params={"offset": state.get("tg_offset", 0)}, timeout=20).json()
    except Exception as e:
        print("[warn] telegram commands:", e)
        return
    for u in r.get("result", []):
        state["tg_offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != str(CHAT):
            continue  # ignore anyone else
        text = (m.get("text") or "").strip()
        if text.startswith("/"):
            reply = apply_command(state["settings"], text)
            if reply:
                print("[info] command:", text[:40])
                send(reply)


def main():
    global KEYWORDS, RELEVANT, EXCLUDE
    first_run = not SEEN_FILE.exists()
    state = {}
    if not first_run:
        try:
            raw = json.loads(SEEN_FILE.read_text())
        except Exception:
            raw = []
        state = raw if isinstance(raw, dict) else {"seen": raw}  # old file = plain list
    seen_list = list(state.get("seen", []))  # keeps order, oldest first
    seen = set(seen_list)
    st = state.setdefault("settings", {})
    st.setdefault("keywords", list(KEYWORDS))
    st.setdefault("relevant", list(RELEVANT))
    st.setdefault("exclude", list(EXCLUDE))
    if st.get("defaults_version", 1) < DEFAULTS_VERSION:  # merge new built-in keywords once
        for k in reversed(NEW_DEFAULTS["keywords"]):
            if k.lower() not in [x.lower() for x in st["keywords"]]:
                st["keywords"].insert(0, k)
        for k in NEW_DEFAULTS["relevant"]:
            if k not in st["relevant"]:
                st["relevant"].append(k)
        st["defaults_version"] = DEFAULTS_VERSION
    done_batches = state.setdefault("batches", [])
    for name, kws in PRESET_BATCHES.items():
        if name in done_batches:
            continue
        for kw in reversed(kws):  # newest first, keeps the order written above
            if kw.lower() not in [k.lower() for k in st["keywords"]]:
                st["keywords"].insert(0, kw)
            if kw.lower() not in st["relevant"]:
                st["relevant"].append(kw.lower())
        done_batches.append(name)
    handle_commands(state)
    KEYWORDS, RELEVANT, EXCLUDE = st["keywords"], st["relevant"], st["exclude"]

    cands, batch = [], set()
    for src in SOURCES:
        if time.time() - START > TIME_BUDGET:
            print("[warn] time budget reached, skipping", getattr(src, "__name__", "src"))
            continue
        runs = [KEYWORDS] if getattr(src, "all_kw", False) else KEYWORDS
        for kw in runs:
            if time.time() - START > TIME_BUDGET:
                break
            try:
                for j in src(kw):
                    low = j["title"].lower()
                    key2 = same_job_key(j)
                    if (j["url"] in seen or j["url"] in batch
                            or (key2 and (key2 in seen or key2 in batch))):
                        continue
                    if is_excluded(low):
                        continue
                    if not j.get("trusted") and not is_relevant(low):
                        continue  # title unrelated to your keywords
                    batch.add(j["url"])
                    if key2:
                        batch.add(key2)
                    cands.append(j)
            except Exception as e:
                print(f"[warn] {getattr(src, '__name__', 'src')} / {str(kw)[:30]}: {e}")
                if "403" in str(e):
                    break  # the site blocks us: do not waste time on its other keywords

    done = []
    if first_run:  # first run only builds the baseline, no spam
        done = cands
    else:
        for j in cands[:MAX_SEND]:
            if notify(j):
                done.append(j)
                time.sleep(1.1)
            else:
                break  # Telegram problem: keep the rest for the next run (nothing is lost)
    for j in done:  # only jobs that were really delivered count as "seen"
        seen_list.append(j["url"])
        k = same_job_key(j)
        if k:
            seen_list.append(k)
    from collections import Counter
    per = Counter(j["source"].split(" ")[0] for j in done)
    left = len(cands) - len(done)
    print(f"{len(done)} {'baseline' if first_run else 'sent'}, {left} waiting for next run, by source: {dict(per)}")
    state["seen"] = seen_list[-MAX_SEEN:]
    SEEN_FILE.write_text(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
