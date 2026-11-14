"""
LinkedIn -> Telegram job alert bot.
Cerca nuove posizioni legal in-house a Milano e dintorni e le manda su Telegram.
Usa solo l'endpoint pubblico (guest) di LinkedIn: nessun login.
"""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

# ---------------- CONFIG ----------------
KEYWORDS = [
    "legal specialist",
    "legal counsel",
    "in-house lawyer",
    "giurista d'impresa",
    "legal affairs",
    "contract manager",
    "paralegal",
    "legal & compliance",
]
LOCATION = "Milano, Lombardia, Italia"
DISTANCE_MILES = 25          # ~40 km: Milano + hinterland
TIME_WINDOW_SEC = 7200       # offerte delle ultime 2h (con dedup, copre i ritardi del cron)
PAGES = 2                    # 25 risultati per pagina

# Il titolo deve contenere almeno una di queste parole
TITLE_INCLUDE = re.compile(
    r"legal|lawyer|counsel|giurist|legale|avvocat|paralegal|contract|compliance|corporate affairs",
    re.I,
)
# Scarta titoli non pertinenti
TITLE_EXCLUDE = re.compile(r"praticant|stage|tirocin|intern\b|internship", re.I)
# Scarta studi legali (cerchiamo ruoli in azienda)
COMPANY_EXCLUDE = re.compile(
    r"studio|avvocati|law firm|\bllp\b|& partners|associati|legal\s*(?:s\.?t\.?a|firm)",
    re.I,
)

SEEN_FILE = Path(__file__).with_name("seen.json")
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
TG_CHAT_IDS = [c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",") if c.strip()]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}
BASE = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
# ----------------------------------------


def fetch(keyword: str, start: int) -> list[dict]:
    params = {
        "keywords": keyword,
        "location": LOCATION,
        "distance": DISTANCE_MILES,
        "f_TPR": f"r{TIME_WINDOW_SEC}",
        "sortBy": "DD",
        "start": start,
    }
    r = requests.get(f"{BASE}?{urlencode(params)}", headers=HEADERS, timeout=20)
    if r.status_code != 200:
        print(f"[warn] {keyword} start={start}: HTTP {r.status_code}")
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    jobs = []
    for card in soup.select("div.base-card, div.job-search-card"):
        urn = card.get("data-entity-urn", "")
        m = re.search(r"(\d+)$", urn)
        if not m:
            continue
        def txt(sel):
            el = card.select_one(sel)
            return el.get_text(strip=True) if el else ""
        link = card.select_one("a.base-card__full-link")
        jobs.append({
            "id": m.group(1),
            "title": txt("h3.base-search-card__title"),
            "company": txt("h4.base-search-card__subtitle"),
            "location": txt("span.job-search-card__location"),
            "url": f"https://www.linkedin.com/jobs/view/{m.group(1)}/",
            "posted": txt("time"),
        })
    return jobs


def relevant(job: dict) -> bool:
    return (
        TITLE_INCLUDE.search(job["title"])
        and not TITLE_EXCLUDE.search(job["title"])
        and not COMPANY_EXCLUDE.search(job["company"])
    )


def send(job: dict) -> None:
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = (
        f"🆕 <b>{esc(job['title'])}</b>\n"
        f"🏢 {esc(job['company'])}\n"
        f"📍 {esc(job['location'])}  ·  {esc(job['posted'])}\n"
        f"{job['url']}"
    )
    for chat_id in TG_CHAT_IDS:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=20,
        ).raise_for_status()


def main() -> None:
    seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()
    found = {}
    for kw in KEYWORDS:
        for page in range(PAGES):
            batch = fetch(kw, page * 25)
            for j in batch:
                found[j["id"]] = j
            time.sleep(3)  # gentile con LinkedIn
            if len(batch) < 25:
                break

    new = [j for jid, j in found.items() if jid not in seen and relevant(j)]
    print(f"Trovate {len(found)} offerte, {len(new)} nuove e pertinenti")
    for j in new:
        send(j)
        time.sleep(1)

    seen |= set(found)  # segna come viste anche quelle scartate
    SEEN_FILE.write_text(json.dumps(sorted(seen)[-5000:]))


if __name__ == "__main__":
    main()
