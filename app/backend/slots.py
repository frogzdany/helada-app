"""Loss-report slot filling: {crop, area_ha, cause, date, notes}.

Three fillers with the same output schema:
  * HybridSlotFiller — default when HELADA_LLM is set: the LLM proposes, the regex rules
    validate (disagreement -> slot left empty -> follow-up question). See docs/small-ai-bench.md.
  * LLMSlotFiller — a local 1-2B instruct model behind an OpenAI-compatible URL
    (HELADA_LLM, e.g. Ollama http://localhost:11434/v1 with qwen3:1.7b), constrained with
    a JSON schema (response_format=json_schema -> grammar-constrained decoding in
    llama.cpp / Ollama). Output is re-validated here; anything out of schema is dropped
    and the bot asks a follow-up question.
  * RegexSlotFiller — deterministic Spanish rules so the demo always works offline.

The LLM never decides anything; it only fills slots from the farmer's words.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date as Date, timedelta

import httpx

log = logging.getLogger("helada.slots")

SLOTS = ("crop", "area_ha", "cause", "date", "notes")
CROPS = ["maiz_temporal", "papa", "avena", "haba", "frijol", "trigo", "cebada", "flores", "otro"]
CAUSES = ["helada", "granizo", "sequia", "inundacion", "nevada", "tornado", "viento", "otra"]
CAUSE_LABEL = {"helada": "helada", "granizo": "granizada", "sequia": "sequía", "inundacion": "inundación",
               "nevada": "nevada", "tornado": "tornado", "viento": "viento", "otra": "otra causa"}
CROP_LABEL = {"maiz_temporal": "maíz de temporal", "papa": "papa", "avena": "avena", "haba": "haba",
              "frijol": "frijol", "trigo": "trigo", "cebada": "cebada", "flores": "flores", "otro": "otro"}

JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "crop": {"type": ["string", "null"], "enum": CROPS + [None]},
        "area_ha": {"type": ["number", "null"]},
        "cause": {"type": ["string", "null"], "enum": CAUSES + [None]},
        "date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "notes": {"type": ["string", "null"]},
    },
    "required": list(SLOTS),
}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", strip_accents((s or "").lower())).strip()


# ------------------------------------------------------------------ validation
def validate(raw: dict, today: Date, parcel_area: float | None = None) -> dict:
    """Keep only in-schema values; everything else becomes None (-> follow-up question)."""
    out = {k: None for k in SLOTS}
    if not isinstance(raw, dict):
        return out
    c = raw.get("crop")
    out["crop"] = c if c in CROPS else None
    c = raw.get("cause")
    out["cause"] = c if c in CAUSES else None
    a = raw.get("area_ha")
    try:
        a = float(a) if a is not None else None
        out["area_ha"] = round(a, 3) if a is not None and 0 < a <= 100 else None
    except (TypeError, ValueError):
        out["area_ha"] = None
    d = raw.get("date")
    try:
        dd = Date.fromisoformat(d) if isinstance(d, str) else None
        out["date"] = dd.isoformat() if dd and today - timedelta(days=366) <= dd <= today else None
    except ValueError:
        out["date"] = None
    n = raw.get("notes")
    out["notes"] = n.strip()[:500] if isinstance(n, str) and n.strip() else None
    return out


# ------------------------------------------------------------------ regex filler
NUMW = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
        "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "quince": 15,
        "veinte": 20, "medio": 0.5, "media": 0.5}
NUM = r"(\d+(?:[.,]\d+)?|un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|quince|veinte)"
UNIT = r"(?:hectareas?|has?\b|hects?\b)"
FRAC = r"(?:\s+y\s+(media|medio|cuarto))?"

# Cause patterns: (cause, regex, strong). "Strong" = the cause is named (helada, granizo, no ha llovido...);
# weak = a damage verb that usually means that cause in the Toluca valley ("se quemó la milpa" = frost,
# "se secó" = drought). A strong cause beats a weak one; a negated cause ("no fue helada") is dropped.
CAUSE_PATTERNS = [
    ("helada", r"\bh?el(o|aron|ada|adas|ando|adota|adita|adon)\b|\bhielo\b|\bescarch\w*|congel\w*|\bfrio\b|\bamanecio (todo )?(blanco|escarchad\w*)", True),
    ("helada", r"\bse (me |nos |le |les )?quem\w*|\bquem(o|aron|ad[oa]s?)\b|\bse (me |nos )?pusieron negr\w*|\bnegrit[oa]s?\b", False),
    ("granizo", r"graniz\w*|\bpiedra\b|\bpedrisco\b", True),
    ("sequia", r"sequia|no (ha |habia )?llov\w*|falta de (lluvia|agua)|sin (lluvia|agua)|\bse (me |nos )?quem\w* (con|por) (el|la) (sol|calor|resolana)\b|\bresolana\b", True),
    ("sequia", r"\bseca\b|se (me |nos |le )?sec(o|aron|ando|a)\b|\bsecandose\b", False),
    ("inundacion", r"inund\w*|aneg\w*|encharc\w*|se (me |nos )?ahog\w*|mucha agua|se (lo |la |los |las )?llevo el agua|el agua se (lo |la |los |las )?llevo|"
                   r"desbord\w*|\bel rio se salio\b|\bse salio el rio\b|\bcreciente\b", True),
    ("nevada", r"\bneva(da|do|r)\b|\bnevo\b|\bnieve\b", True),
    ("tornado", r"tornado|remolino|torbellino", True),
    ("viento", r"\bvientos?\b|ventarron|vendaval|\bse (me )?acam\w*|\b(el|un|mucho|fuerte) aire\b", True),
    ("otra", r"plaga|gusano|cogollero|chapulin|gallina ciega|pulgon|\broya\b|\btuzas?\b|\bratas?\b|chahuixtle|"
             r"\blumbre\b|\bfuego\b|incendi\w*|quemazon", True),
]
# "bolitas de hielo" / "llovió hielo" is hail, not frost: matched first and masked so "hielo" is not read as frost.
HAIL_ICE = r"\b(bolit\w*|bolas|pelotit\w*|granit\w*|piedrit\w*|pedac\w*|trozos|cachos) de hielo\b|\bllovio hielo\b|\blluvia de hielo\b"
# "se quemó" next to fire words is fire, next to sun/heat is drought: the frost reading of "quem" is masked.
FIRE_OR_SUN = r"\b(lumbre|fuego|incendi\w*|quemazon|sol|calor|resolana)\b"
FROST_NAMED = r"\bh?elad|\bh?el(o|aron)\b|\bhielo\b|\bescarch|\bfrio\b|congel"
QUEM_WEAK = r"\bse (me |nos |le |les )?quem\w*|\bquem(o|aron|ad[oa]s?)\b"
CROP_PATTERNS = [
    ("maiz_temporal", r"\bmai[sz]\b|\bmilpa\b|\belotes?\b|\bmazorcas?\b|\bjilotes?\b"),
    ("papa", r"\bpapas?\b|\bpapal\b"),
    ("avena", r"\bavena\b"),
    ("haba", r"\bh?abas?\b"),
    ("frijol", r"\bfrijol(es)?\b"),
    ("trigo", r"\btrigo\b"),
    ("cebada", r"\bcebada\b"),
    ("flores", r"\bflores\b|cempasuchil|crisantemo|gladiola"),
    ("otro", r"\b(chicharo|calabaza|chile|jitomate|tomate|nopal|zanahoria|alfalfa|lechuga|cilantro|hortalizas?|"
             r"durazno|manzana|aguacate|ejote|garbanzo|lenteja|arvejon)\b"),
]

# ------------------------------------------------------------------ clause analysis (shared with router.py)
# Farmers' messages mix what happened to them, what happened to someone else, what did NOT happen and
# questions. Every rule below works per clause on norm()-ed text (lowercase, no accents, punctuation kept).
_SPLIT = re.compile(r"[.;:!?¿¡\n]+|,|\s+(?:pero|sino|aunque)\s+|\.\.\.")
THIRD_NOUNS = (r"(?:compadre|comadre|vecin[oa]s?|herman[oa]s?|cunad[oa]s?|prim[oa]s?|tio|tia|tios|suegr[oa]s?|"
               r"hij[oa]s?|senor|senora|don \w+|dona \w+)")
FIRST_PERSON = re.compile(
    r"\b(?:me|nos|yo|nosotros|mio|mia)\b|\ba mi\b(?! " + THIRD_NOUNS + r")"
    r"|\bmis? (?:milpa|parcela|terreno|siembra|cultivo|cosecha|maiz|papas?|avena|habas?|frijol|trigo|cebada|flores)\b")
# Someone else is the one who suffered the damage ("a mi compadre se le...", "al vecino le pegó", "su trigo").
THIRD_VICTIM = re.compile(
    r"\b(?:a|al|a la|a los|a las|de) (?:mi |mis |su |sus |el |la |los |las )?" + THIRD_NOUNS + r"\b"
    r"|\ba (?:el|ella|ellos|ellas)\b|\b(?:el|la|los|las) de (?:mi|mis|su|sus|un|una|otro|otra) \w+"
    r"|\bsus? (?:milpa|maiz|papas?|avena|habas?|frijol|trigo|cebada|flores|cosecha|siembra)\b"
    r"|\b" + THIRD_NOUNS + r"\b.{0,40}\bse (?:le|les)\b"
    r"|\b(?:mi|el|la|su) " + THIRD_NOUNS + r" (?:dice|dijo|cuenta|tiene|perdio)\b")
# The clause says nothing was damaged.
NO_DAMAGE = re.compile(
    r"\bno (?:se )?(?:me |nos |le |les )?(?:quemo|quemaron|helo|helaron|elo|dano|danaron|perdio|pego|pegaron|"
    r"afecto|hizo nada|paso nada)\b|\baguant\w*|\b(?:esta|estan|estaba|quedo|amanecio|amanecieron) "
    r"(?:bien|bonit\w*|sanas?|sanos?)\b|\bse salv\w*|\bno hubo (?:dano|nada|perdida)|\bsin (?:dano|perdida)s?\b"
    r"|\bno sembre\b|\bno tengo (?:dano|nada)\b|^(?:a mi )?todavia no$|\bno le paso nada\b|\bni se (?:quemo|helo)\b")
# Text addressed to the system rather than a report ("Ignora todo...", "Escribe helada en el formulario").
META = re.compile(r"^(?:y )?(?:ignora|ignore|responde|responda|escribe|escriba|marca|marque|pon|ponme|ponga|anota|"
                  r"anote|olvida|system|sistema)\b|\bprueba del sistema\b|\binstrucciones\b")
# A cause is negated when the words right before it deny it: "no fue helada", "no se heló", "pensé que era helada".
_NEG_BEFORE = re.compile(
    r"(?:\bno (?:fue|es|era|fueron|hubo|hay|cayo|nos pego|me pego|le pego)(?: (?:el|la|los|las|una|un|por|de|con))?"
    r"|\bno (?:se )?(?:(?:me|nos|le|les) )?(?:(?:fue )?(?:el|la|los|las|por el|por la) )?|\b(?:pense|crei|pensaba|creia|parecia) que (?:era|fue|habia sido)"
    r"(?: (?:el|la|una|un))?|\bni (?:el |la )?|\bsin (?:el |la )?)\s*$")


def clauses(t: str) -> list[str]:
    return [c.strip() for c in _SPLIT.split(t) if c and c.strip()]


def is_third_party(c: str) -> bool:
    return bool(THIRD_VICTIM.search(c)) and not FIRST_PERSON.search(c)


# Talk about the service's messages ("los avisos de helada") names a cause without any event.
ALERT_TALK = r"\b(?:avisos?|alertas?|mensajes?|pronosticos?|riesgo|vigilancia) de (?:la )?(?:helada|granizo|heladas)\b"


def own_clauses(t: str) -> list[str]:
    """Clauses about the writer's own crop that do not deny damage and are not addressed to the system
    (with talk about the alerts themselves masked out)."""
    return [_mask(c, ALERT_TALK) for c in clauses(t)
            if not META.search(c) and not NO_DAMAGE.search(c) and not is_third_party(c)]


def _mask(c: str, pat: str) -> str:
    return re.sub(pat, lambda m: "_" * len(m.group(0)), c)


def pick_cause(t: str, expected: str | None = None) -> str | None:
    """Cause from own, non-negated clauses. Strong beats weak. Two different strong causes (e.g. "primero
    granizo y luego heló") -> None, so the bot asks, unless the bot had just asked for the cause (-> first)."""
    found: list[tuple[int, int, str, bool]] = []     # (clause index, pos, cause, strong)
    for i, c in enumerate(own_clauses(t)):
        if re.search(HAIL_ICE, c):
            found.append((i, re.search(HAIL_ICE, c).start(), "granizo", True))
            c = _mask(c, HAIL_ICE)
        if re.search(FIRE_OR_SUN, c) and not re.search(FROST_NAMED, c):
            sun_or_fire_verb = re.search(r"quem|sec", c)
            c = _mask(c, QUEM_WEAK)                    # "se quemó con el sol" / "...prendió lumbre": not frost
            sun = re.search(r"\b(sol|calor|resolana)\b", c)
            if sun and sun_or_fire_verb:
                found.append((i, sun.start(), "sequia", True))
        for cause, pat, strong in CAUSE_PATTERNS:
            for m in re.finditer(pat, c):
                if _NEG_BEFORE.search(c[:m.start()]):
                    continue
                found.append((i, m.start(), cause, strong))
    if not found:
        return None
    pool = [f for f in found if f[3]] or found
    pool.sort(key=lambda f: (f[0], f[1]))
    kinds = {f[2] for f in pool}
    if len(kinds) == 1 or expected == "cause":
        return pool[0][2]
    return None


def pick_crop(t: str) -> str | None:
    """Earliest crop named in an own clause (a neighbour's field or a crop that "aguantó" does not count)."""
    best = None
    for i, c in enumerate(own_clauses(t)):
        for key, pat in CROP_PATTERNS:
            m = re.search(pat, c)
            if m and (best is None or (i, m.start()) < best[1]):
                best = (key, (i, m.start()))
    return best[0] if best else None


WEEKDAYS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
MONTHS = {m: i + 1 for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                                          "agosto", "septiembre", "octubre", "noviembre", "diciembre"])}
MONTHS["setiembre"] = 9


def _num(tok: str) -> float:
    tok = tok.replace(",", ".")
    try:
        return float(tok)
    except ValueError:
        return float(NUMW[tok])


def _frac(f: str | None) -> float:
    return {"media": 0.5, "medio": 0.5, "cuarto": 0.25}.get(f or "", 0.0)


def parse_area(t: str, parcel_area: float | None = None, bare: bool = False) -> float | None:
    """t must be norm()-ed. bare=True also accepts a number without unit (answer to 'how much?')."""
    m = re.search(r"\btres cuartos de " + UNIT, t)
    if m:
        return 0.75
    m = re.search(r"\b(un|1) cuarto de " + UNIT, t)
    if m:
        return 0.25
    m = re.search(NUM + r"\s+y\s+(media|medio)\s+" + UNIT, t)            # "dos y media hectareas"
    if m:
        return _num(m.group(1)) + 0.5
    m = re.search(NUM + r"\s+" + UNIT + FRAC, t)                          # "una hectarea y media", "3 has"
    if m:
        return _num(m.group(1)) + _frac(m.group(2))
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*" + UNIT + FRAC, t)               # "1.5ha"
    if m:
        return _num(m.group(1)) + _frac(m.group(2))
    if re.search(r"\bmedia " + UNIT, t):
        return 0.5
    if re.search(r"(^|\s)" + UNIT + r" y media", t):                      # "hectarea y media"
        return 1.5
    m = re.search(r"(\d[\d.,]*)\s*(metros|m2|mts)", t)                     # "5000 metros"
    if m:
        v = float(m.group(1).replace(",", "")) / 10_000
        return v if v > 0 else None
    if parcel_area and re.search(r"\btod[oa]s?\b( la| el| mi| su)? ?(parcela|terreno|milpa|siembra|cultivo|todo)?", t) \
            and not re.search(r"\btodavia\b", t):
        if re.search(r"\btod[oa] (la parcela|el terreno|la milpa|mi parcela|mi terreno|la siembra|el cultivo)\b|"
                     r"\bse (me |nos )?\w+ tod[oa]\b|\btod[oa] se\b|\bfue tod[oa]\b|\btodito\b", t) or bare:
            return float(parcel_area)
    if parcel_area and re.search(r"\bla mitad\b", t):
        return round(parcel_area / 2, 2)
    if bare:
        m = re.search(r"(?:^|\s)" + NUM + FRAC + r"(?:\s|$)", t)
        if m:
            return _num(m.group(1)) + _frac(m.group(2))
        if re.search(r"\bmedia\b|\bmedio\b", t):
            return 0.5
    return None


LAST_YEAR_MAX_DAYS = 90    # «el 20 de noviembre» said before that day only means last year this far back


def parse_date(t: str, today: Date) -> str | None:
    if re.search(r"\b(antier|anteayer|antes de ayer)\b", t):
        return (today - timedelta(days=2)).isoformat()
    if re.search(r"\b(anoche|ayer)\b", t):
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"\bhace\s+" + NUM + r"\s+dias?\b", t)
    if m:
        return (today - timedelta(days=int(_num(m.group(1))))).isoformat()
    if re.search(r"\b(la semana pasada|hace (una|1) semana)\b", t):
        return (today - timedelta(days=7)).isoformat()
    m = re.search(r"\b(\d{1,2})\s+de\s+(" + "|".join(MONTHS) + r")\b", t)
    if m:
        try:
            d = Date(today.year, MONTHS[m.group(2)], int(m.group(1)))
            if d > today:
                # A day that has not come yet. It is last year's only when that is recent («el 31 de diciembre»
                # said in January); otherwise it is a slip, and the bot asks instead of guessing a year.
                d = Date(today.year - 1, d.month, d.day)
                if (today - d).days > LAST_YEAR_MAX_DAYS:
                    return None
            return d.isoformat()
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t)
    if m:
        try:
            y = int(m.group(3)) if m.group(3) else today.year
            y = y + 2000 if y < 100 else y
            d = Date(y, int(m.group(2)), int(m.group(1)))
            return d.isoformat() if d <= today else None
        except ValueError:
            return None
    m = re.search(r"\b(el|del|este|el pasado|este pasado)\s+(" + "|".join(WEEKDAYS) + r")\b", t)
    if m:
        back = (today.weekday() - WEEKDAYS[m.group(2)]) % 7
        return (today - timedelta(days=back)).isoformat()
    if re.search(r"\b(hoy|esta manana|en la madrugada|esta madrugada|ahorita)\b", t):
        return today.isoformat()
    return None


@dataclass
class RegexSlotFiller:
    name: str = "regex"

    def fill(self, text: str, today: Date, parcel_area: float | None = None,
             expected: str | None = None) -> dict:
        t = norm(text)
        raw = {
            "crop": pick_crop(t),
            "cause": pick_cause(t, expected),
            "area_ha": parse_area(t, parcel_area, bare=(expected == "area_ha")),
            "date": parse_date(t, today),
            "notes": text.strip() if text and text.strip() else None,
        }
        return validate(raw, today, parcel_area)


# ------------------------------------------------------------------ LLM filler
SYSTEM_PROMPT = """Eres un extractor de datos. Del mensaje de un productor del Estado de México extrae SOLO:
crop: maiz_temporal|papa|avena|haba|frijol|trigo|cebada|flores|otro ("milpa", "elote" = maiz_temporal)
cause: helada|granizo|sequia|inundacion|nevada|tornado|viento|otra ("se quemó" por frío, "escarcha" = helada)
area_ha: hectáreas DAÑADAS como número. "media hectárea" = 0.5; "hectárea y media" = 1.5;
  "dos y media" = 2.5; "un cuarto" = 0.25; "tres cuartos" = 0.75; "toda la parcela" = superficie total.
date: fecha del daño YYYY-MM-DD. Búscala en el CALENDARIO que se te da ("anoche"/"ayer", "antier", "el martes").
notes: null.
Si un dato NO se dice, pon null. No inventes: una pregunta sin daño lleva todo null."""

_WD = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def llm_user_prompt(text: str, today: Date, parcel_area: float | None, expected: str | None) -> str:
    """Small models are bad at calendar arithmetic, so we hand them a lookup table instead."""
    cal = [f"{(today - timedelta(days=i)).isoformat()} {_WD[(today - timedelta(days=i)).weekday()]}"
           + {0: " = hoy, esta madrugada", 1: " = ayer, anoche", 2: " = antier, hace 2 días"}.get(i, f" = hace {i} días")
           for i in range(8)]
    return ("CALENDARIO:\n" + "\n".join(cal)
            + f"\nSuperficie total de la parcela: {parcel_area or 'desconocida'} ha."
            + (f"\nSe le acaba de preguntar por: {expected}." if expected else "")
            + f"\nMensaje: \"{text}\"")


@dataclass
class LLMSlotFiller:
    """1-2B local model, grammar-constrained to JSON_SCHEMA, re-validated by validate()."""
    base_url: str
    model: str
    timeout: float = 20.0
    name: str = field(init=False, default="llm")

    def raw(self, text: str, today: Date, parcel_area: float | None = None,
            expected: str | None = None) -> dict:
        body = {
            "model": self.model, "temperature": 0, "max_tokens": 160,
            # qwen3 is a thinking model: without this it reasons for ~300 tokens (3-4x slower).
            # Ollama's /v1 honours it; servers that don't know it ignore it (retried without on 400).
            "reasoning_effort": "none",
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": llm_user_prompt(text, today, parcel_area, expected)}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "loss_report", "strict": True, "schema": JSON_SCHEMA}},
        }
        url = self.base_url.rstrip("/") + "/chat/completions"
        r = httpx.post(url, json=body, timeout=self.timeout)
        if r.status_code == 400:
            body.pop("reasoning_effort")
            r = httpx.post(url, json=body, timeout=self.timeout)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"] or ""
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", content, re.S)
            return json.loads(m.group(0)) if m else {}

    def fill(self, text: str, today: Date, parcel_area: float | None = None,
             expected: str | None = None) -> dict:
        out = validate(self.raw(text, today, parcel_area, expected), today, parcel_area)
        out["notes"] = text.strip() or None     # keep the farmer's own words, never a paraphrase
        return out


@dataclass
class HybridSlotFiller:
    """LLM proposes, deterministic rules check. Per slot:
      crop / cause (closed enums; where a small LLM can add recall for phrasings the rules miss):
        * LLM and rules agree                     -> value
        * both set but DISAGREE                   -> None (the bot asks a follow-up question)
        * only rules set                          -> rules value
        * only LLM set                            -> LLM value, but only if the rules saw a damage
          report in this message (some crop/cause/area) or the bot had just asked for that slot;
          otherwise None (1-2B models answer "helada / maíz" to "¿cuándo siembro?")
      area_ha / date (arithmetic and calendar: 1-2B models get these wrong 40-80% of the time in
        docs/small-ai-bench.md): rules only; an LLM disagreement is logged, never used.
      LLM down / timeout / bad JSON               -> rules alone ("regex(fallback)")
    Rules are both the guaranteed fallback and the validator."""
    llm: LLMSlotFiller
    regex: RegexSlotFiller = field(default_factory=RegexSlotFiller)
    name: str = field(init=False, default="llm+regex")

    def fill_ex(self, text: str, today: Date, parcel_area: float | None = None,
                expected: str | None = None) -> tuple[dict, str, list[str]]:
        r = self.regex.fill(text, today, parcel_area, expected)
        try:
            l = self.llm.fill(text, today, parcel_area, expected)
        except Exception as e:  # noqa: BLE001 — any LLM failure degrades to the rules
            log.warning("LLM slot filler failed (%s); using regex", e)
            return r, "regex(fallback)", []
        is_report = any(r[k] is not None for k in ("crop", "cause", "area_ha"))
        out, conflicts = dict(r), []
        for k in ("crop", "cause"):
            a, b = l[k], r[k]
            if a is not None and b is not None and a != b:
                out[k] = None
                conflicts.append(k)
            elif b is None and a is not None and (is_report or expected == k):
                out[k] = a
        ignored = [k for k in ("area_ha", "date") if l[k] is not None and r[k] is not None and l[k] != r[k]]
        if conflicts or ignored:
            log.info("slots llm=%s rules=%s -> ask %s, ignored llm %s", l, r, conflicts, ignored)
        return out, self.name + (f" conflict={','.join(conflicts)}" if conflicts else ""), conflicts

    def fill(self, text: str, today: Date, parcel_area: float | None = None,
             expected: str | None = None) -> dict:
        return self.fill_ex(text, today, parcel_area, expected)[0]


def make_filler(llm_url: str, llm_model: str, mode: str | None = None):
    """mode (env HELADA_SLOT_MODE): hybrid (default when an LLM URL is set) | llm (LLM alone,
    for benchmarking) | regex. No URL -> regex."""
    mode = (mode or os.environ.get("HELADA_SLOT_MODE", "hybrid")).lower()
    if not llm_url or mode == "regex":
        return RegexSlotFiller()
    llm = LLMSlotFiller(llm_url, llm_model)
    return llm if mode == "llm" else HybridSlotFiller(llm)


def fill_slots(filler, text: str, today: Date, parcel_area: float | None = None,
               expected: str | None = None) -> tuple[dict, str]:
    """Returns (slots, engine). If the LLM is unreachable, falls back to regex (logged)."""
    if isinstance(filler, HybridSlotFiller):
        out, engine, _ = filler.fill_ex(text, today, parcel_area, expected)
        return out, engine
    try:
        return filler.fill(text, today, parcel_area, expected), filler.name
    except Exception as e:
        if isinstance(filler, RegexSlotFiller):
            raise
        log.warning("LLM slot filler failed (%s); falling back to regex", e)
        return RegexSlotFiller().fill(text, today, parcel_area, expected), "regex(fallback)"


# ------------------------------------------------------------------ intents
def intent(text: str) -> str | None:
    t = norm(text)
    if re.fullmatch(r"(baja|alto|stop|ya no|ya no quiero( avisos)?|cancelar)[.! ]*", t):
        return "baja"
    if re.fullmatch(r"(alta|volver|reanudar)[.! ]*", t):
        return "alta"
    if re.fullmatch(r"(ayuda|menu|hola|buenas( tardes| noches| dias)?|info)[.!? ]*", t):
        return "ayuda"
    if re.search(r"\b(listo|es todo|ya es todo|ya no tengo( mas)?( fotos)?|no tengo (mas )?fotos?|sin fotos?)\b", t):
        return "listo"
    if re.fullmatch(r"(si|sip|claro|ya|ya lo hice|si ya|si, ya|ya hice el pre ?registro)[.! ]*", t) \
            or re.search(r"\bya (lo )?(hice|tengo) (el|mi) pre ?registro\b", t):
        return "si"
    if re.fullmatch(r"(no|todavia no|aun no|no lo he hecho|nel)[.! ]*", t):
        return "no"
    return None
