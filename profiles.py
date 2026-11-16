"""
Profili di ricerca del bot.

Ogni profilo dice: COSA cercare (keyword x localita'), COME filtrare (titolo,
descrizione, esclusioni) e QUALE testo usare per la valutazione AI (Secret su GitHub).
Per aggiungere una persona basta aggiungere un profilo a PROFILES in fondo al file.
"""
import re

# ---------------------------------------------------------------------------
# Localita' disponibili per le ricerche
# ---------------------------------------------------------------------------
ITALY = r"ital(ia|y|ien)"
EUROPE = (
    ITALY + r"|europ|\beu\b|emea|remot|"
    r"german|deutschland|france|francia|spain|spagna|espa[nñ]a|portugal|portogallo|"
    r"netherlands|olanda|paesi\s+bassi|nederland|belgi|ireland|irlanda|austria|"
    r"switzerland|svizzera|schweiz|sweden|svezia|denmark|danimarca|finland|norway|"
    r"poland|polonia|czech|greece|grecia|romania|hungary|united\s+kingdom|regno\s+unito|"
    r"\buk\b|england|london|luxembourg|lussemburgo|estonia|latvia|lithuania|croatia|"
    r"slovenia|slovakia|bulgaria|malta|cyprus"
)

# Localita' "pan-europee" (lavori da qualsiasi paese EU, o lista di sedi)
PAN_EUROPE = ITALY + r"|europ|\beu\b|emea|remot|multiple|various|several|anywhere"
# Frasi in descrizione che confermano che si puo' lavorare dall'Italia / da tutta Europa
EU_ANYWHERE = (
    r"\b(ital(ia|y)|milan[o]?)\b|"
    r"(work|working|based|live|located|remote(ly)?)\s+(from\s+)?anywhere\s+in\s+(the\s+)?(eu|europe|emea)|"
    r"remote(ly)?\s+(from|within|across|anywhere\s+in)\s+(any\s+)?(the\s+)?(eu|europe|emea|european)|"
    r"(eu|europe|emea)[- ]wide\s+remote|fully\s+remote\s*[(\-–,:]\s*(eu|europe|emea)|"
    r"(location|based)\s*:\s*(remote[,\s\-–/]*)?(eu|europe|emea)\b|"
    r"any\s+(eu|european)\s+(member\s+)?(country|countries|state)|"
    r"(one|any)\s+of\s+our\s+(european\s+)?(offices|hubs|locations)|"
    r"choose\s+(your|from|between)[^.]{0,30}(office|hub|location)"
)

LOCATIONS = {
    # nome: parametri LinkedIn + regex che la localita' dell'annuncio deve rispettare
    "milano":  {"params": {"location": "Milano, Lombardia, Italia", "distance": 25}, "allow": ITALY},
    "verona":  {"params": {"location": "Verona, Veneto, Italia", "distance": 25}, "allow": ITALY},
    "bologna": {"params": {"location": "Bologna, Emilia-Romagna, Italia", "distance": 25}, "allow": ITALY},
    "italia_remote": {
        "params": {"location": "Italia", "geoId": "103350119", "f_WT": 2}, "allow": ITALY, "remote": True,
    },
    # Remoto Europa: tiene gli annunci "pan-europei" o che includono l'Italia.
    # Se la localita' e' un singolo paese estero (es. "Berlin, Germany"), l'annuncio
    # passa solo se la DESCRIZIONE dice che si puo' lavorare da tutta Europa / dall'Italia.
    "europa_remote": {
        "params": {"location": "European Union", "geoId": "91000000", "f_WT": 2},
        "allow": PAN_EUROPE, "allow_if_desc": EU_ANYWHERE, "outer": EUROPE, "remote": True,
    },
}


def _rx(pats):
    return [re.compile(p, re.I) for p in pats]


def _hits(rxs, text):
    return [rx.pattern for rx in rxs if rx.search(text)]


# ===========================================================================
# ALICE — legal in-house (uffici legali di aziende o enti), Milano + remoto Italia
# ===========================================================================
ALICE_KEYWORDS = [
    "legal", "legal counsel", "legal specialist", "in-house counsel", "legal affairs",
    "legal manager", "head of legal", "giurista", "ufficio legale", "affari legali",
    "specialista legale", "contract manager", "contrattualistica", "paralegal",
    "compliance", "privacy specialist", "data protection", "modello 231", "AI Act",
    "corporate governance", "segreteria societaria", "antitrust", "trademark",
    "intellectual property", "regulatory", "affari regolatori",
]
ALICE_REMOTE_KEYWORDS = [
    "legal counsel", "legal specialist", "legal", "giurista", "contract manager",
    "compliance", "privacy specialist",
]

TITLE_STRONG = [
    r"legal", r"lawyer", r"attorney", r"counsel", r"jurist", r"giurist", r"legale",
    r"avvocat", r"paralegal", r"contract\s*(manag|special|admin)", r"contrattualistic",
    r"\bdpo\b", r"data\s*protection", r"privacy", r"antitrust", r"litigation",
    r"contenzios", r"corporate\s*secretar", r"segreteria\s*societari",
    r"affari\s*(legali|societari)", r"trademark", r"\bmarchi\b", r"intellectual\s*property",
    r"propriet[aà]\s*intellettuale", r"\bip\s*(counsel|specialist|manager)", r"brand\s*protection",
]
# --- Titolo: segnali medi (servono conferme nella descrizione) ---
TITLE_MEDIUM = [
    r"compliance", r"regulatory", r"regolator", r"governance", r"\b231\b",
    r"ai\s*act", r"gdpr", r"risk", r"rischi", r"whistleblow", r"anti.?money|aml\b|antiriciclaggio",
    r"public\s*affairs", r"corporate\s*affairs",
]
# --- Descrizione: termini "core" giuridici (almeno uno serve per i titoli medi) ---
DESC_CORE = [
    r"giurisprudenza", r"laurea\s+in\s+legge", r"law\s+degree", r"\bdiritto\s+(commerciale|societario|civile|del\s+lavoro|amministrativo|bancario|industriale|dei\s+contratti)",
    r"(ufficio|area|funzione|direzione|consulenza|assistenza)\s+legal[ei]", r"affari\s+legali",
    r"legal\s+(department|team|function|affairs|counsel|advice|support|matters)",
    r"avvocat", r"abilitazione\s+(all'esercizio|forense)", r"bar\s+admission",
    r"contrattualistic", r"redazione\s+(di\s+)?contratti", r"(drafting|negotiat\w*)\s+(of\s+)?(commercial\s+)?contracts",
    r"\bgiuridic",
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
    "regulatory": r"regulatory|regolator",
    "autorità": r"\barera\b|\bconsob\b|\bmifid\b|\bemir\b|\bremit\b|\bivass\b|"
                r"\bagcm\b|banca\s+d'italia|garante\s+(per\s+la\s+)?(privacy|protezione)",
    "compliance": r"compliance",
    "segreteria societaria": r"segreteria\s+societaria|assemble[ae]\s+(dei\s+)?soci|board\s+meeting",
    "IP": r"propriet[aà]\s+intellettuale|intellectual\s+property",
    "marchi": r"\bmarchi\b|trademark|brand\s+protection|anticontraffazione|counterfeit",
    "licensing": r"licensing|contratti\s+di\s+licenza",
    "distribuzione": r"(contratti|accordi)\s+di\s+(distribuzione|agenzia|franchising|fornitura)|"
                     r"distribution\s+agreements?|franchis|dealer\s+agreements?|supply\s+agreements?",
    "consumatori": r"codice\s+del\s+consumo|consumer\s+(law|protection)|product\s+liability|"
                   r"responsabilit[aà]\s+(da\s+)?prodotto",
    "societario": r"diritto\s+societario|corporate\s+law|operazioni\s+straordinarie|\bm&a\b",
}

# Esclusioni
TITLE_EXCLUDE = re.compile(
    r"praticant|stage\b|stagista|tirocin|\bintern\b|internship|"
    r"sales|commerciale|venditor|engineer|developer|sviluppator|"
    r"\bit\s+(compliance|security)|cyber|infermier|medic[oa]\b|medico\s+legale|"
    r"legal\s+entit|contabil|accountant|"
    r"quality|qualit[aà]|\bhse\b|\behs\b|ambiente\s+e\s+sicurezza",
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


TITLE_STRONG_RX, TITLE_MEDIUM_RX, DESC_CORE_RX = _rx(TITLE_STRONG), _rx(TITLE_MEDIUM), _rx(DESC_CORE)
DESC_DOMAIN_RX = {k: re.compile(v, re.I) for k, v in DESC_DOMAIN.items()}
# termini troppo generici per far passare da soli un titolo non legal
WEAK_DOMAIN = {"normativa", "compliance", "regulatory", "privacy", "GDPR"}
# frasi da NON togliere come "di rito" anche se contengono "ai sensi del..."
KEEP_IN_BOILERPLATE = [DESC_DOMAIN_RX["231"], DESC_DOMAIN_RX["AI Act"], DESC_DOMAIN_RX["OdV"]]


def alice_excluded(job: dict) -> str | None:
    if TITLE_EXCLUDE.search(job["title"]):
        return "titolo escluso"
    if job.get("industry") and INDUSTRY_EXCLUDE.search(job["industry"]):
        return "studio legale (settore)"
    if COMPANY_EXCLUDE.search(job["company"]):
        return "studio legale (nome)"
    return None


def alice_relevance(job: dict, desc: str) -> tuple[int, str]:
    """
    - titolo forte                 -> +3 (passa da solo)
    - titolo medio                 -> +1, serve almeno 1 termine giuridico 'core' nella descrizione
    - titolo generico              -> passa solo con core + >=3 termini di dominio specifici
    - ogni termine di dominio      -> +1 (max 4)
    """
    title = job["title"]
    strong, medium = _hits(TITLE_STRONG_RX, title), _hits(TITLE_MEDIUM_RX, title)
    core = _hits(DESC_CORE_RX, desc)
    domain = [k for k, rx in DESC_DOMAIN_RX.items() if rx.search(desc)]
    s = 0
    if strong:
        s += 3
    elif medium:
        s += 1 if core else -10
    elif not (core and len([d for d in domain if d not in WEAK_DOMAIN]) >= 3):
        s -= 10
    s += min(len(domain), 4)
    why = (["titolo"] if strong or medium else []) + (["descr.: " + ", ".join(domain[:6])] if domain else [])
    return s, " | ".join(why)


ALICE = {
    "key": "alice",
    "name": "Alice",
    "emoji": "⚖️",
    "searches": [(kw, "milano") for kw in ALICE_KEYWORDS]
              + [(kw, "italia_remote") for kw in ALICE_REMOTE_KEYWORDS],
    "extra_params": {},
    "excluded": alice_excluded,
    "relevance": alice_relevance,
    "min_score": 3,
    "profile_env": "PROFILO",                  # Secret con il profilo per l'AI
    "chat_env": "TELEGRAM_CHAT_ID_ALICE",      # opzionale: chat dedicata
}


# ===========================================================================
# EDOARDO — data / analytics / AI, senior o manager
# Milano, Verona, Bologna, remoto Italia, remoto Europa
# ===========================================================================
EDOARDO_KEYWORDS = [
    "data scientist",
    "data analyst",
    "analytics manager",
    "data science manager",
    "head of data",
    "AI manager",
    "data lead",             # pesca "Data & AI Lead", "Data Team Lead"...
    "AI lead",
    "business intelligence",
    "analytics lead",
]
EDOARDO_LOCATIONS = ["milano", "verona", "bologna", "italia_remote", "europa_remote"]

EDO_TITLE_STRONG = re.compile(
    r"data\s*(scien|analy|strateg|product|lead|manager|director|governance|insight)|"
    r"analytic|machine\s*learning|\bml\b|\bai\b|artificial\s*intelligence|"
    r"intelligenza\s*artificiale|business\s*intelligence|\bbi\b|decision\s*scien|"
    r"insight|head\s*of\s*data|chief\s*data|\bcdo\b|quantitative|statistic|"
    r"\bdata\b.*\b(manager|lead|head|director)",
    re.I,
)
EDO_TITLE_EXCLUDE = re.compile(
    r"junior|\bjr\b|\bintern\b|internship|stage\b|stagista|tirocin|graduate|neolaureat|"
    r"data\s*entry|data\s*engineer|software|developer|sviluppator|devops|"
    r"sales|account\s*(manager|executive)|commerciale|"
    r"trainer|annotat|tutor|writer|teacher|docente|contabil|"
    r"\bsap\b|s/4\s*hana|\berp\b|migration|migrazione",
    re.I,
)


def edoardo_excluded(job: dict) -> str | None:
    if EDO_TITLE_EXCLUDE.search(job["title"]):
        return "titolo escluso"
    return None


def edoardo_relevance(job: dict, desc: str) -> tuple[int, str]:
    if EDO_TITLE_STRONG.search(job["title"]):
        return 3, "titolo"
    return -10, ""


EDOARDO = {
    "key": "edoardo",
    "name": "Edoardo",
    "emoji": "📊",
    "searches": [(kw, loc) for kw in EDOARDO_KEYWORDS for loc in EDOARDO_LOCATIONS],
    "extra_params": {},
    "excluded": edoardo_excluded,
    "relevance": edoardo_relevance,
    "min_score": 3,
    "profile_env": "PROFILO_EDOARDO",
    "chat_env": "TELEGRAM_CHAT_ID_EDOARDO",
}


PROFILES = [ALICE, EDOARDO]
