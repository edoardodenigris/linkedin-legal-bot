"""
LinkedIn -> Telegram job alert bot (v2).

Flusso per ogni esecuzione:
  1. Ricerca: per ogni keyword interroga l'endpoint pubblico (guest) di LinkedIn,
     offerte delle ultime TIME_WINDOW_SEC secondi in zona Milano.
  2. Pre-filtro sul titolo: scarta stage/tirocini e titoli palesemente fuori ambito.
  3. Dettaglio: per ogni offerta nuova scarica la pagina con descrizione e settore.
  4. Scoring: decide se e' pertinente guardando titolo + descrizione.
  5. Esclusione studi legali tramite il SETTORE LinkedIn (non solo il nome).
  6. Notifica su Telegram con il motivo del match, e salva gli ID visti.
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

# =====================================================================
# CONFIG — tutto quello che serve modificare sta qui
# =====================================================================

# Ricerche su LinkedIn (LinkedIn cerca in titolo + descrizione)
SEARCH_KEYWORDS = [
    "legal",
    "legal counsel",
    "giurista",
    "ufficio legale",
    "affari legali",
    "contract manager",
    "contrattualistica",
    "paralegal",
    "compliance",
    "regulatory",
    "privacy",
    "data protection",
    "DPO",
    "GDPR",
    "modello 231",
    "AI Act",
    "corporate governance",
    "segreteria societaria",
    "antitrust",
]

LOCATION = "Milano, Lombardia, Italia"
DISTANCE_MILES = 25           # ~40 km
TIME_WINDOW_SEC = 7200        # ultime 2h (con dedup copre i ritardi del cron)
MAX_DETAILS_PER_RUN = 60      # tetto alle pagine dettaglio per esecuzione
MIN_SCORE = 3                 # soglia di pertinenza (vedi score())

# --- Titolo: segnali forti (bastano da soli) ---
TITLE_STRONG = [
    r"legal", r"lawyer", r"counsel", r"giurist", r"legale", r"avvocat",
    r"paralegal", r"contract\s*manag", r"contrattualistic", r"\bdpo\b",
    r"data\s*protection", r"privacy", r"antitrust", r"corporate\s*secretar",
    r"segreteria\s*societari", r"affari\s*(legali|societari)",
]
# --- Titolo: segnali medi (servono conferme nella descrizione) ---
TITLE_MEDIUM = [
    r"compliance", r"regulatory", r"regolator", r"governance", r"\b231\b",
    r"ai\s*act", r"gdpr", r"risk", r"rischi", r"whistleblow", r"anti.?money|aml\b|antiriciclaggio",
    r"public\s*affairs", r"corporate\s*affairs",
]
# --- Descrizione: termini "core" giuridici (almeno uno serve per i titoli medi) ---
DESC_CORE = [
    r"giurisprudenza", r"laurea\s+in\s+legge", r"law\s+degree", r"\bdiritto\b",
    r"\blegal\b", r"ufficio\s+legale", r"avvocat", r"abilitazione\s+(all'esercizio|forense)",
    r"bar\s+admission", r"contrattualistic", r"\bcontratti\b", r"\bcontracts?\b",
    r"\bgiurid", r"\blegali?\b",
]
# --- Descrizione: termini di dominio (ognuno distinto vale 1 punto) ---
DESC_DOMAIN = {
    "GDPR": r"gdpr",
    "data protection": r"data\s+protection",
    "privacy": r"privacy",
    "231": r"(modello|d\.?\s?lgs\.?)\s*(n\.?\s*)?231|\b231/2001",
    "OdV": r"organismo\s+di\s+vigilanza|\bodv\b",
    "AI Act": r"ai\s+act",
    "NIS2": r"\bnis\s?2\b",
    "DORA": r"\bdora\b",
    "antitrust": r"antitrust",
    "due diligence": r"due\s+diligence",
    "governance": r"corporate\s+governance",
    "contenzioso": r"contenzios|litigation",
    "whistleblowing": r"whistleblowing",
    "AML": r"antiriciclaggio|anti.?money\s+laundering",
    "normativa": r"normativ",
    "regulatory": r"regulatory",
    "compliance": r"compliance",
    "segreteria societaria": r"segreteria\s+societaria|assemble[ae]\s+(dei\s+)?soci|board\s+meeting",
    "IP": r"propriet[aà]\s+intellettuale|intellectual\s+property",
}

# Esclusioni
TITLE_EXCLUDE = re.compile(
    r"praticant|stage\b|stagista|tirocin|\bintern\b|internship|"
    r"sales|commerciale|venditor|engineer|developer|sviluppator|"
    r"\bit\s+(compliance|security)|cyber|infermier|medic[oa]\b",
    re.I,
)
# Studi legali: per settore LinkedIn (affidabile) + fallback sul nome
INDUSTRY_EXCLUDE = re.compile(
    r"law\s+practice|legal\s+services|studi\s+legali|servizi\s+legali|attivit[aà]\s+legali",
    re.I,
)
COMPANY_EXCLUDE = re.compile(
    r"\bstudio\b|avvocati|law\s*firm|\bllp\b|&\s*partners|associati|\bsta\b",
    re.I,
)

# =====================================================================

STATE_FILE = Path(__file__).with_name("seen.json")
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
TG_CHAT_IDS = [c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",") if c.strip()]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}
SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"

_rx = lambda pats: [re.compile(p, re.I) for p in pats]
TITLE_STRONG_RX, TITLE_MEDIUM_RX = _rx(TITLE_STRONG), _rx(TITLE_MEDIUM)
DESC_CORE_RX = _rx(DESC_CORE)
DESC_DOMAIN_RX = {k: re.compile(v, re.I) for k, v in DESC_DOMAIN.items()}


# ---------------------------------------------------------------- HTTP
def get(url: str) -> requests.Response | None:
    """GET con retry e backoff su 429/5xx."""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
        except requests.RequestException as e:
            print(f"[warn] {e}")
            r = None
        if r is not None and r.status_code == 200:
            return r
        code = r.status_code if r is not None else "ERR"
        print(f"[warn] HTTP {code} su {url[:90]}… (tentativo {attempt + 1})")
        if r is not None and r.status_code in (400, 404):
            return None
        time.sleep(10 * (attempt + 1))
    return None


# ---------------------------------------------------------------- SEARCH
def search(keyword: str) -> tuple[list[dict], bool]:
    params = {
        "keywords": keyword,
        "location": LOCATION,
        "distance": DISTANCE_MILES,
        "f_TPR": f"r{TIME_WINDOW_SEC}",
        "sortBy": "DD",
        "start": 0,
    }
    r = get(f"{SEARCH_URL}?{urlencode(params)}")
    if r is None:
        return [], False
    soup = BeautifulSoup(r.text, "html.parser")
    jobs = []
    for card in soup.select("div.base-card, div.job-search-card"):
        m = re.search(r"(\d+)$", card.get("data-entity-urn", ""))
        if not m:
            continue
        t = lambda sel: (el.get_text(strip=True) if (el := card.select_one(sel)) else "")
        jobs.append({
            "id": m.group(1),
            "title": t("h3.base-search-card__title"),
            "company": t("h4.base-search-card__subtitle"),
            "location": t("span.job-search-card__location"),
            "posted": t("time"),
            "url": f"https://www.linkedin.com/jobs/view/{m.group(1)}/",
        })
    return jobs, True


# ---------------------------------------------------------------- DETAIL
def fetch_detail(job: dict) -> bool:
    r = get(DETAIL_URL.format(job["id"]))
    if r is None:
        return False
    soup = BeautifulSoup(r.text, "html.parser")
    desc = soup.select_one("div.show-more-less-html__markup, div.description__text")
    job["description"] = desc.get_text(" ", strip=True) if desc else ""
    criteria = {}
    for li in soup.select("li.description__job-criteria-item"):
        k = li.select_one("h3")
        v = li.select_one("span")
        if k and v:
            criteria[k.get_text(strip=True).lower()] = v.get_text(" ", strip=True)
    job["industry"] = next(
        (v for k, v in criteria.items() if "industr" in k or "settor" in k), ""
    )
    job["seniority"] = next(
        (v for k, v in criteria.items() if "seniority" in k or "livello" in k), ""
    )
    return True


# ---------------------------------------------------------------- SCORING
def hits(rxs, text):
    return [rx.pattern for rx in rxs if rx.search(text)]


def score(job: dict) -> tuple[int, str]:
    """
    Regole:
      - titolo forte                         -> +3 (passa da solo)
      - titolo medio                         -> +1, e la descrizione deve avere
                                                almeno 1 termine 'core' giuridico
      - ogni termine di dominio distinto nella descrizione -> +1 (max 4)
      - descrizione senza titolo pertinente  -> passa solo con core + >=4 di dominio
    Ritorna (punteggio, motivo leggibile).
    """
    title, desc = job["title"], job.get("description", "")
    strong = hits(TITLE_STRONG_RX, title)
    medium = hits(TITLE_MEDIUM_RX, title)
    core = hits(DESC_CORE_RX, desc)
    domain = [k for k, rx in DESC_DOMAIN_RX.items() if rx.search(desc)]

    s = 0
    if strong:
        s += 3
    elif medium:
        s += 1 if core else -10
    elif not (core and len(domain) >= 4):
        s -= 10
    s += min(len(domain), 4)

    why = []
    if strong or medium:
        why.append("titolo")
    if domain:
        why.append("descr.: " + ", ".join(domain[:6]))
    return s, " | ".join(why)


def excluded(job: dict) -> str | None:
    if TITLE_EXCLUDE.search(job["title"]):
        return "titolo escluso"
    if job.get("industry") and INDUSTRY_EXCLUDE.search(job["industry"]):
        return "studio legale (settore)"
    if COMPANY_EXCLUDE.search(job["company"]):
        return "studio legale (nome)"
    return None


# ---------------------------------------------------------------- TELEGRAM
def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tg_send(text: str) -> bool:
    """Invia a tutte le chat. Riprova su errori temporanei (5xx, 429, rete).
    Ritorna False se dopo i tentativi l'invio non e' riuscito."""
    ok_all = True
    for chat_id in TG_CHAT_IDS:
        for attempt in range(4):
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                          "disable_web_page_preview": True},
                    timeout=20,
                )
            except requests.RequestException as e:
                print(f"[warn] Telegram: {e}")
                r = None
            if r is not None and r.ok:
                break
            wait = 5 * (attempt + 1)
            if r is not None:
                print(f"[warn] Telegram HTTP {r.status_code}: {r.text[:150]}")
                if r.status_code == 429:
                    wait = r.json().get("parameters", {}).get("retry_after", wait) + 1
                elif r.status_code < 500:
                    break  # errore non temporaneo (es. chat id sbagliato): inutile riprovare
            time.sleep(wait)
        else:
            ok_all = False
            continue
        if r is None or not r.ok:
            ok_all = False
    return ok_all


def notify(job: dict, why: str) -> bool:
    lines = [
        f"🆕 <b>{esc(job['title'])}</b>",
        f"🏢 {esc(job['company'])}" + (f"  ·  {esc(job['industry'])}" if job.get("industry") else ""),
        f"📍 {esc(job['location'])}  ·  {esc(job['posted'])}"
        + (f"  ·  {esc(job['seniority'])}"
           if job.get("seniority") and "applicab" not in job["seniority"].lower() else ""),
        f"🔎 <i>{esc(why)}</i>",
        job["url"],
    ]
    return tg_send("\n".join(lines))


# ---------------------------------------------------------------- STATE
def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"seen": [], "last_warn": 0}
    data = json.loads(STATE_FILE.read_text() or "[]")
    if isinstance(data, list):  # compatibilita' con la v1
        return {"seen": data, "last_warn": 0}
    return data


def save_state(state: dict) -> None:
    state["seen"] = sorted(set(state["seen"]))[-8000:]
    STATE_FILE.write_text(json.dumps(state))


# ---------------------------------------------------------------- MAIN
def main() -> None:
    state = load_state()
    seen = set(state["seen"])

    # 1. ricerca
    found: dict[str, dict] = {}
    ok_searches = 0
    for kw in SEARCH_KEYWORDS:
        jobs, ok = search(kw)
        ok_searches += ok
        for j in jobs:
            found.setdefault(j["id"], j)
        time.sleep(3)

    # Se LinkedIn ci blocca del tutto: avviso su Telegram (max 1 ogni 12h) e fallisco
    if ok_searches == 0:
        if time.time() - state.get("last_warn", 0) > 12 * 3600:
            tg_send("⚠️ <b>Job bot</b>: LinkedIn non risponde a nessuna ricerca. "
                    "Controlla i log su GitHub Actions.")
            state["last_warn"] = time.time()
            save_state(state)
        sys.exit(1)

    new = [j for jid, j in found.items() if jid not in seen]
    print(f"Ricerche OK {ok_searches}/{len(SEARCH_KEYWORDS)} · "
          f"offerte trovate {len(found)} · nuove {len(new)}")

    # 2-5. valutazione (lo stato viene salvato anche se qualcosa va storto)
    stats: dict[str, int] = {}
    try:
        _evaluate(new, seen, stats)
    finally:
        state["seen"] = list(seen)
        save_state(state)
    print(f"Esito: {stats}")


def _evaluate(new: list[dict], seen: set, stats: dict) -> None:
    sent, details = 0, 0
    for job in new:
        reason = excluded(job)  # pre-filtro su titolo/nome, senza scaricare il dettaglio
        if reason is None:
            if details >= MAX_DETAILS_PER_RUN:
                continue  # resta "non vista": la valutiamo al prossimo giro
            details += 1
            if not fetch_detail(job):
                continue  # errore: riprovo al prossimo giro
            time.sleep(1.5)
            reason = excluded(job)  # ora con il settore
        if reason is None:
            s, why = score(job)
            if s >= MIN_SCORE:
                if not notify(job, why):
                    print(f"  [{s:>3}] invio fallito    {job['title']} — riprovo al prossimo giro")
                    continue  # resta non vista
                sent += 1
                time.sleep(3)  # Telegram: max ~20 messaggi/minuto per gruppo
                reason = "inviata"
            else:
                reason = "poco pertinente"
            print(f"  [{s:>3}] {reason:<16} {job['title']} — {job['company']}")
        stats[reason] = stats.get(reason, 0) + 1
        seen.add(job["id"])

    print(f"Dettagli scaricati {details} · inviate {sent}")


if __name__ == "__main__":
    main()
