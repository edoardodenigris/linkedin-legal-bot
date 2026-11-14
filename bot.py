"""
LinkedIn -> Telegram job alert bot — multi-profilo.

Per ogni profilo definito in profiles.py, a ogni esecuzione:
  1. cerca su LinkedIn (endpoint pubblico, nessun login) le offerte delle ultime 2 ore;
  2. scarta localita' fuori zona e titoli esclusi;
  3. scarica descrizione e settore delle offerte nuove;
  4. le filtra con le regole del profilo;
  5. le fa valutare a Claude Haiku rispetto al profilo della persona (se configurato);
  6. le manda su Telegram e ricorda quelle gia' viste.
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

from profiles import KEEP_IN_BOILERPLATE, LOCATIONS, PROFILES

# =====================================================================
# CONFIG GENERALE
# =====================================================================
TIME_WINDOW_SEC = 7200          # offerte delle ultime 2h (con dedup copre i ritardi)
SEARCH_PAUSE_SEC = 2            # pausa tra una ricerca e l'altra
MAX_DETAILS_PER_PROFILE = 40    # tetto pagine dettaglio per profilo per esecuzione
AI_MODEL = "claude-haiku-5-5"
AI_MAX_CALLS_PER_PROFILE = 20   # tetto di sicurezza sui costi
AI_PRICE_IN, AI_PRICE_OUT = 0.10, 0.50   # USD per milione di token (Haiku 5.5, prompt < 100K)

# Frasi "di rito" a fondo annuncio: vengono tolte dalla descrizione prima dello scoring
BOILERPLATE = re.compile(
    r"2016/679|196/2003|101/2018|198/2006|903/77|215/2003|216/2003|"
    r"ai\s+sensi\s+(del|della|dell'|degli)|informativa|trattamento\s+dei\s+(tuoi\s+)?dati|"
    r"dati\s+personali|personal\s+data|privacy\s+(policy|notice|statement)|"
    r"(entrambi|ambo)\s+i\s+sessi|pari\s+opportunit|equal\s+opportunit|"
    r"candidat[iouae]\s+(sono\s+)?invitat|autorizz\w*\s+(il|al)\s+trattamento|"
    r"\bl\.?\s*68/99|categorie\s+protette|"
    r"(in\s+materia\s+di|normativa\s+(sulla|in\s+materia\s+di))\s+(protezione\s+dei\s+dati|privacy)|"
    r"normativa\s+vigente|regolamento\s+(ue|europeo|\(ue\))\s*(n\.?\s*)?2016|gdpr\)?\s*$",
    re.I,
)


# =====================================================================

STATE_FILE = Path(__file__).with_name("seen.json")
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}
SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
AI_STATE = {"spent": 0.0, "out_of_credit": False}
# Interruttore: dopo N ricerche fallite di fila considero LinkedIn "bloccato" e smetto
# di insistere per questo giro (evita esecuzioni lunghissime fatte solo di tentativi)
MAX_CONSECUTIVE_FAILS = 5
LI_STATE = {"consecutive_fails": 0, "tripped": False}


def env_list(name: str) -> list[str]:
    return [c.strip() for c in os.environ.get(name, "").split(",") if c.strip()]


DEFAULT_CHAT_IDS = env_list("TELEGRAM_CHAT_ID")


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
        print(f"[warn] HTTP {code} su {url[:100]}… (tentativo {attempt + 1})")
        if r is not None and r.status_code in (400, 404):
            return None
        time.sleep(10 * (attempt + 1))
    return None


# ---------------------------------------------------------------- SEARCH
def search(keyword: str, loc_key: str, extra: dict) -> tuple[list[dict], bool]:
    loc = LOCATIONS[loc_key]
    params = {"keywords": keyword, **loc["params"], **extra,
              "f_TPR": f"r{TIME_WINDOW_SEC}", "sortBy": "DD", "start": 0}
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
        job = {
            "id": m.group(1),
            "title": t("h3.base-search-card__title"),
            "company": t("h4.base-search-card__subtitle"),
            "location": t("span.job-search-card__location"),
            "posted": t("time"),
            "url": f"https://www.linkedin.com/jobs/view/{m.group(1)}/",
            "remote": bool(loc.get("remote")),
        }
        # paracadute: la localita' dell'annuncio deve essere coerente con la ricerca
        if job["location"] and not re.search(loc["allow"], job["location"], re.I):
            continue
        jobs.append(job)
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
        k, v = li.select_one("h3"), li.select_one("span")
        if k and v:
            criteria[k.get_text(strip=True).lower()] = v.get_text(" ", strip=True)
    job["industry"] = next((v for k, v in criteria.items() if "industr" in k or "settor" in k), "")
    job["seniority"] = next((v for k, v in criteria.items() if "seniority" in k or "livello" in k), "")
    return True


def strip_boilerplate(text: str) -> str:
    """Toglie le frasi standard su privacy/GDPR/pari opportunita'."""
    sentences = re.split(r"(?<=[.!?;])\s+|\s{2,}|\n", text)
    return " ".join(
        x for x in sentences
        if not BOILERPLATE.search(x) or any(k.search(x) for k in KEEP_IN_BOILERPLATE)
    )


# ---------------------------------------------------------------- AI
AI_SYSTEM = """Valuti offerte di lavoro per una persona che cerca lavoro. Ecco il suo profilo:

<profilo>
{profilo}
</profilo>

Valuta quanto l'offerta che ricevi e' adatta a questa persona, seguendo il profilo
(in particolare vincoli, esclusioni e soglie economiche). Se la retribuzione e' indicata
in un'altra valuta o su base mensile/oraria, convertila in RAL annua lorda in euro.
Rispondi SOLO con un oggetto JSON valido, senza testo prima o dopo, con questi campi:
{{
  "voto": intero da 1 a 5 (5 = perfetta, 1 = da scartare),
  "in_target": true se il tipo di datore di lavoro e di ruolo rientra in cio' che cerca,
  "seniority": stringa breve, es. "mid (3-5 anni)", "senior (7+ anni)", "manager", "non indicata",
  "ral": RAL indicata nell'annuncio in forma breve (es. "55-65k EUR"), "" se non indicata,
  "sintesi": massimo 15 parole in italiano su cosa si fa concretamente nel ruolo,
  "attenzione": stringa breve con requisiti o condizioni che potrebbero escluderla
                (lingue, trasferimento, trasferte, contratto a termine, RAL sotto soglia,
                remoto non possibile dall'Italia), "" se nessuno
}}"""


def ai_review(job: dict, profile_text: str) -> dict | None:
    """Chiede a Claude Haiku una valutazione dell'offerta. None se qualcosa va storto."""
    desc = strip_boilerplate(job.get("description", ""))[:6000]
    ad = (
        f"Titolo: {job['title']}\nAzienda: {job['company']}\n"
        f"Settore: {job.get('industry') or 'n.d.'}\nLocalita': {job['location']}"
        f"{' (annuncio full remote)' if job.get('remote') else ''}\n"
        f"Seniority LinkedIn: {job.get('seniority') or 'n.d.'}\n\nDescrizione:\n{desc}"
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": AI_MODEL, "max_tokens": 400,
                  "system": AI_SYSTEM.format(profilo=profile_text),
                  "messages": [{"role": "user", "content": ad}]},
            timeout=60,
        )
        if not r.ok:
            print(f"[warn] AI HTTP {r.status_code}: {r.text[:200]}")
            if "credit balance" in r.text.lower():
                AI_STATE["out_of_credit"] = True
            return None
        body = r.json()
        u = body.get("usage", {})
        AI_STATE["spent"] += (u.get("input_tokens", 0) * AI_PRICE_IN
                              + u.get("output_tokens", 0) * AI_PRICE_OUT) / 1e6
        text = "".join(b.get("text", "") for b in body.get("content", []))
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0)) if m else None
        if not data or not isinstance(data.get("voto"), int):
            print(f"[warn] AI risposta non valida: {text[:200]}")
            return None
        data["voto"] = max(1, min(5, data["voto"]))
        return data
    except (requests.RequestException, ValueError) as e:
        print(f"[warn] AI errore: {e}")
        return None


# ---------------------------------------------------------------- TELEGRAM
def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tg_send(text: str, chat_ids: list[str], silent: bool = False) -> bool:
    """Invia a tutte le chat. Riprova su errori temporanei (5xx, 429, rete)."""
    ok_all = True
    for chat_id in chat_ids:
        r = None
        for attempt in range(4):
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                          "disable_web_page_preview": True, "disable_notification": silent},
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
                    break  # errore non temporaneo: inutile riprovare
            time.sleep(wait)
        if r is None or not r.ok:
            ok_all = False
    return ok_all


def notify(profile: dict, job: dict, why: str, ai: dict | None, chat_ids: list[str]) -> bool:
    head = f"{profile['emoji']} <b>{esc(profile['name'])}</b> · "
    if ai:
        v = ai["voto"]
        head += ("🔥" if v >= 4 else "⬇️" if v <= 2 else "🆗") + f" <b>{v}/5</b> · "
    if job.get("remote"):
        head += "🏠 <b>REMOTO</b> · "
    lines = [
        head + f"<b>{esc(job['title'])}</b>",
        f"🏢 {esc(job['company'])}" + (f"  ·  {esc(job['industry'])}" if job.get("industry") else ""),
        f"📍 {esc(job['location'])}  ·  {esc(job['posted'])}",
    ]
    if ai:
        if ai.get("sintesi"):
            lines.append(f"📝 {esc(ai['sintesi'])}")
        sen = str(ai.get("seniority") or "")
        if sen and "non indicat" not in sen.lower():
            lines.append(f"👤 {esc(sen)}")
        if ai.get("ral"):
            lines.append(f"💰 {esc(ai['ral'])}")
        if ai.get("in_target") is False:
            lines.append("🚫 <i>Fuori target (tipo di azienda o ruolo)</i>")
        if ai.get("attenzione"):
            lines.append(f"⚠️ {esc(ai['attenzione'])}")
    else:
        lines.append(f"🔎 <i>{esc(why)}</i>")
    lines.append(job["url"])
    return tg_send("\n".join(lines), chat_ids, silent=bool(ai and ai["voto"] <= 2))


# ---------------------------------------------------------------- STATE
def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"seen": []}
    data = json.loads(STATE_FILE.read_text() or "[]")
    if isinstance(data, list):
        data = {"seen": data}
    # compatibilita': gli ID senza prefisso erano tutti del profilo di Alice
    data["seen"] = [x if ":" in x else f"alice:{x}" for x in data.get("seen", [])]
    return data


def save_state(state: dict) -> None:
    state["seen"] = sorted(set(state["seen"]))[-15000:]
    STATE_FILE.write_text(json.dumps(state))


# ---------------------------------------------------------------- RUN
def run_profile(profile: dict, seen: set) -> tuple[int, int, dict]:
    """Ritorna (ricerche ok, ricerche totali, statistiche)."""
    name = profile["name"]
    chat_ids = env_list(profile["chat_env"]) or DEFAULT_CHAT_IDS
    profile_text = os.environ.get(profile["profile_env"], "").strip()
    ai_on = bool(ANTHROPIC_API_KEY and profile_text)

    found: dict[str, dict] = {}
    ok = 0
    for kw, loc_key in profile["searches"]:
        if LI_STATE["tripped"]:
            break
        jobs, success = search(kw, loc_key, profile["extra_params"])
        ok += success
        LI_STATE["consecutive_fails"] = 0 if success else LI_STATE["consecutive_fails"] + 1
        if LI_STATE["consecutive_fails"] >= MAX_CONSECUTIVE_FAILS:
            LI_STATE["tripped"] = True
            print(f"[warn] {MAX_CONSECUTIVE_FAILS} ricerche fallite di fila: LinkedIn sta "
                  "bloccando, interrompo le ricerche per questo giro")
        for j in jobs:
            prev = found.setdefault(j["id"], j)
            prev["remote"] = prev["remote"] or j["remote"]
        time.sleep(SEARCH_PAUSE_SEC)

    key = lambda jid: f"{profile['key']}:{jid}"
    new = [j for jid, j in found.items() if key(jid) not in seen]
    print(f"\n=== {name}: ricerche OK {ok}/{len(profile['searches'])} · "
          f"trovate {len(found)} · nuove {len(new)} · AI {'on' if ai_on else 'off'}")

    stats: dict[str, int] = {}
    details = ai_calls = 0
    for job in new:
        reason = profile["excluded"](job)
        if reason is None:
            if details >= MAX_DETAILS_PER_PROFILE or LI_STATE["tripped"]:
                continue  # resta non vista: la valuto al prossimo giro
            details += 1
            if not fetch_detail(job):
                continue
            time.sleep(1.5)
            reason = profile["excluded"](job)
        if reason is None:
            s, why = profile["relevance"](job, strip_boilerplate(job.get("description", "")))
            if s >= profile["min_score"]:
                ai = None
                if ai_on and ai_calls < AI_MAX_CALLS_PER_PROFILE and not AI_STATE["out_of_credit"]:
                    ai_calls += 1
                    ai = ai_review(job, profile_text)
                if not notify(profile, job, why, ai, chat_ids):
                    print(f"  invio fallito: {job['title']} — riprovo al prossimo giro")
                    continue
                time.sleep(3)  # Telegram: max ~20 messaggi/minuto per gruppo
                reason = f"inviata (AI {ai['voto']}/5)" if ai else "inviata"
            else:
                reason = "poco pertinente"
            print(f"  [{s:>3}] {reason:<18} {job['title']} — {job['company']}")
        stats[reason.split(" (")[0]] = stats.get(reason.split(" (")[0], 0) + 1
        seen.add(key(job["id"]))
    print(f"  dettagli {details} · chiamate AI {ai_calls} · esito {stats}")
    return ok, len(profile["searches"]), stats


def main() -> None:
    state = load_state()
    seen = set(state["seen"])
    tot_ok = tot = 0
    try:
        for profile in PROFILES:
            ok, n, _ = run_profile(profile, seen)
            tot_ok += ok
            tot += n
    finally:
        state["seen"] = list(seen)
        state["ai_spent_usd"] = state.get("ai_spent_usd", 0.0) + AI_STATE["spent"]
        if AI_STATE["out_of_credit"] and time.time() - state.get("ai_credit_warn", 0) > 24 * 3600:
            tg_send("💳 <b>Job bot</b>: credito Anthropic esaurito. Le offerte continuano "
                    "ad arrivare, ma senza voto AI. Ricarica su console.anthropic.com.",
                    DEFAULT_CHAT_IDS)
            state["ai_credit_warn"] = time.time()
        # avviso solo se il blocco persiste: 4 giri bloccati di fila (~1 ora)
        state["blocked_runs"] = state.get("blocked_runs", 0) + 1 if LI_STATE["tripped"] else 0
        if state["blocked_runs"] >= 4 and time.time() - state.get("last_warn", 0) > 12 * 3600:
            tg_send("⚠️ <b>Job bot</b>: LinkedIn blocca le ricerche da circa un'ora. "
                    "Controlla i log su GitHub Actions.", DEFAULT_CHAT_IDS)
            state["last_warn"] = time.time()
        save_state(state)
    print(f"\nRicerche OK {tot_ok}/{tot} · spesa AI stimata: questo giro "
          f"${AI_STATE['spent']:.4f} · totale ${state['ai_spent_usd']:.3f}")
    if LI_STATE["tripped"]:
        sys.exit(1)   # esecuzione segnata in rosso su GitHub: si vede subito che c'era un blocco


if __name__ == "__main__":
    main()
