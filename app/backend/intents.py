"""Deterministic reading of inbound farmer text: intent router, pre-registro answer, farmer-stated stage.

No model and no network: regex + keyword rules over norm()-ed Spanish text, per clause (slots.clauses).

Intent labels (multi-label, a message can carry several):
  damage    the writer says their OWN crop was already damaged (not a question, hypothetical,
            future, negation, someone else's field or text addressed to the system)
  optout    asks to stop receiving messages ("BAJA", "ya no me manden mensajes", "bájenme de la lista")
  optin     asks to receive them again ("ALTA", "quiero volver a recibir los avisos")
  forecast  asks whether it will freeze / how cold tonight ("¿va a caer helada esta noche?")
  planting  asks when / what to sow (planting.is_planting_question + "¿alcanzo a resembrar?")
  program   asks about the PASACME support program (papers, deadline, where, payment)
  greeting  hello / thanks / help
Primary route (explicit precedence, used by conversation.py when no follow-up is pending):
  optout > optin > damage > forecast > planting > program > greeting > other
A message with damage AND a question gets the answer to the question plus the loss-report follow-up.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as Date

from . import planting
from .slots import (ALERT_TALK, CAUSE_PATTERNS, CROP_PATTERNS, FIRST_PERSON, META, NO_DAMAGE, RegexSlotFiller,
                    _mask, clauses, is_third_party, norm, parse_area)

LABELS = ("damage", "optout", "optin", "forecast", "planting", "program", "greeting")
PRECEDENCE = ("optout", "optin", "damage", "forecast", "planting", "program", "greeting")

# ------------------------------------------------------------------ opt-out / opt-in
OPTOUT_FULL = re.compile(r"(?:baja|alto|stop|ya no|cancelar|salir|basta|unsubscribe|no mas avisos|no mas mensajes"
                         r"|darme de baja|dar de baja|de baja)[.!¡ ]*")
OPTOUT = re.compile(
    r"^(?:baja|stop|alto)\b(?! (?:la|el|mucho|temperatura))"
    r"|\bno (?:me|nos) (?:manden|mande|envien|envie|escriban|escriba|sigan (?:mandando|enviando|escribiendo)|"
    r"lleguen|llegue|hablen)\b(?! (?:\w+ )?(?:la foto|las fotos|el paquete|los papeles))"
    r"|\b(?:dejen|deje|dejar|paren|pare|parar) de (?:mandar|enviar|escribir)(?:me|nos|le)?\b"
    r"|\bno quiero (?:recibir |que me (?:lleguen|manden|envien) )?(?:mas |ya )?(?:los |las |sus |esos |esas )?"
    r"(?:avisos|mensajes|alertas|notificaciones|msjs?)\b"
    r"|\b(?:bajen|bajenme|bajeme|denme de baja|deme de baja|darme de baja|me doy de baja|quitenme|quiteme|"
    r"saquenme|saqueme|borrenme|borreme|eliminenme|eliminen mi numero|quiten mi numero)\b"
    r"|\b(?:sacar|quitar|borrar|bajar|eliminar)(?:me)? de (?:la|su|esta) lista\b|\bde la lista de (?:avisos|alertas|mensajes)\b"
    r"|\bme (?:pueden|puede|podrian) (?:sacar|quitar|borrar|bajar|dar de baja)\b")
OPTIN_FULL = re.compile(r"(?:alta|volver|reanudar|activar|reactivar|start|suscribir(?:me)?|si quiero (?:los )?avisos)[.!¡ ]*")
OPTIN = re.compile(
    r"(?<!no )\b(?:quiero|quisiera|queremos) (?:volver a )?(?:recibir|que me (?:lleguen|manden|envien)) (?:otra vez |de nuevo )?"
    r"(?:los |las )?(?:avisos|alertas|mensajes)\b"
    r"|\b(?:denme|deme|darme|me doy) de alta\b|\b(?:vuelvan|vuelva) a (?:mandar|enviar)(?:me|nos)?\b"
    r"|\b(?:mandenme|envienme) (?:otra vez|de nuevo) (?:los |las )?(?:avisos|alertas|mensajes)\b"
    r"|\breactiv\w+ (?:los |las )?(?:avisos|alertas)\b")

# ------------------------------------------------------------------ questions
_LEAD = r"(?:(?:oiga|oye|oigan|disculpe|perdon|buenas(?: tardes| noches| dias)?|buenos dias|hola|una pregunta|pregunta|y|nomas|oiga joven)\s*,?\s*)*"
INTERROG = re.compile(
    r"^" + _LEAD + r"(?:que|q|como(?! (?:un|una|uno|media|medio|dos|tres|cuatro|cinco|diez|\d|la mitad|el|de|a las|a la))|cuanto|cuanta|cuantos|cuantas|donde|adonde|cual|cuales|quien|hasta cuando|"
    r"pa cuando|para cuando|a que hora|a donde|se puede|puedo|podemos|hay que|es cierto|a poco"
    r"|cuando(?! (?:\w+ )?(?:cayo|cayeron|paso|llego|fue|estaba|empezo|vino|pego|helo|elo|quemo|granizo|nevo|"
    r"andaba|iba|tenia|estabamos)))\b")
QUERY_LEAD = re.compile(r"\b(?:queria|quiero|quisiera|queremos|quisieramos) saber\b|\bme (?:puede|podria|pueden) decir\b"
                        r"|\bdigame\b|\bme dice si\b|\bpregunt\w+ si\b|\bme avisa si\b")
HYPOTHETICAL = re.compile(
    r"^(?:y )?(?:si|por si|en caso de que|ojala|y si)\s+(?:(?:se|me|nos|le|les|no|llega a|vuelve a)\s+)*"
    r"(?:hiela|hela|yela|hielan|cae|caiga|cayera|llega|llegue|pega|pegue|quema|queme|pierde|pierda|seca|seque|"
    r"inunda|inunde|graniza|granice|hay|haya|viene|venga|da|de|va|dana|dane|helara|hiele)\b")
FUTURE = re.compile(r"\b(?:va|van|vaya|vayan) a (?:caer|haber|helar|elar|hacer|bajar|pegar|granizar|nevar|llover|"
                    r"quemar|venir)\b|\b(?:helara|caera|habra|granizara|nevara|podria|pueda)\b")


@dataclass
class Seg:
    text: str
    question: bool


def segments(t: str) -> list[Seg]:
    """Split norm()-ed text into clauses and flag the ones that ask something."""
    out: list[Seg] = []

    def plain(s: str) -> None:
        for sent in re.split(r"[.!;\n]+", s):
            q_end = sent.rstrip().endswith("?")
            parts = clauses(sent)
            lead = False
            for i, c in enumerate(parts):
                m = QUERY_LEAD.search(c)
                if m:
                    if c[:m.start()].strip():
                        out.append(Seg(c[:m.start()].strip(), False))
                    if c[m.end():].strip():
                        out.append(Seg(c[m.end():].strip(), True))
                    lead = True
                    continue
                out.append(Seg(c, lead or bool(INTERROG.match(c)) or (q_end and i == len(parts) - 1)))

    pos = 0
    for m in re.finditer(r"¿[^?¿]*\??", t):
        plain(t[pos:m.start()])
        out.extend(Seg(c, True) for c in clauses(m.group(0)))
        pos = m.end()
    plain(t[pos:])
    return out


# ------------------------------------------------------------------ damage
DMG_VERB = re.compile(
    r"\bse (?:me |nos |le |les )?(?:quem|hel|el[oa]|sec|perd|inund|ahog|encharc|acam|tumb|pudr|congel|chamusc|muri|"
    r"maltrat|pus\w* negr|dan|arru|tir|ech\w* a perder|chup|marchit|aplast|quebr|romp|llev|acab)\w*"
    r"|\bse (?:me|nos) \w+(?:o|aron|ieron)\b"                      # "se me <verb>ó": first-person past, any verb
    r"|\b(?:me|nos|le|les) (?:quem|hel|tumb|tir|acost|acab|sec|dan|peg|dej|ech\w* a perder|romp|arras|llev|quebr|"
    r"aplast|com)\w*"
    r"|\b(?:dano|danaron|danado|danada|danados|danadas|quemad[oa]s?|perdid[oa]s?|perdi|perdimos|perdio|perdieron|"
    r"tumbad[oa]s?|inundad[oa]s?|arrasad[oa]s?|tumbo|tiro|acosto|comio|comieron|quemo|quemaron|helo|helaron|elo|"
    r"rompieron|rompio|maltrato)\b")
CONTEXT = re.compile(r"\b(?:tod[oa]s?|nada|cosecha|parcela|terreno|siembra|sembrado|plantas?|matas?|hojas?|"
                     r"cultivo|surcos?)\b")
FULL_DAMAGE = re.compile(r"(?:dano|danos|reportar|reporte|reportar (?:un )?dano|tengo (?:un )?dano|siniestro)[.!? ]*")


def _has(pats, c: str) -> bool:
    return any(re.search(p[1], c) for p in pats)


def _damage(segs: list[Seg], t: str) -> bool:
    if FULL_DAMAGE.fullmatch(t):
        return True
    own = [_mask(s.text, ALERT_TALK) for s in segs if not s.question and not META.search(s.text)
           and not NO_DAMAGE.search(s.text) and not is_third_party(s.text) and not HYPOTHETICAL.search(s.text)
           and not FUTURE.search(s.text)]
    if not own:
        return False
    denied = any(NO_DAMAGE.search(s.text) for s in segs)
    crop = any(_has(CROP_PATTERNS, c) for c in own)
    cause = any(_has(CAUSE_PATTERNS, c) for c in own)
    area = any(parse_area(c) is not None for c in own)
    first = any(FIRST_PERSON.search(c) for c in own)
    ctx = any(CONTEXT.search(c) for c in own)
    verb = any(DMG_VERB.search(c) for c in own)
    if verb and (crop or cause or first or ctx or area):
        return True
    # an event without a damage verb ("nos cayó granizo", "tres cuartos de hectárea con granizo") counts
    # only when nothing in the message says the crop was fine
    return cause and (crop or area or first) and not denied


# ------------------------------------------------------------------ questions by topic
FORECAST = re.compile(
    r"\b(?:va|van) a (?:helar|elar|granizar|nevar)\b"
    r"|\b(?:va|van) a (?:caer|haber|hacer|pegar|venir) (?:una |la |el |mucho |mucha |otra )?(?:helada|elada|escarcha|"
    r"granizo|granizada|piedra|nevada|hielo|frio|frialdad)\b"
    r"|\b(?:va|van) a bajar mucho\b|\b(?:cuanto|que tanto|que tan) va a bajar\b"
    r"|\b(?:va|van) a bajar\b.*\b(?:temperatura|grados|frio|termometro)\b"
    r"|\b(?:helara|elara|caera helada|habra helada|hara frio|granizara|nevara)\b|\bpronostico\b"
    r"|\bcomo (?:viene|va a estar|esta|estara|se ve|pinta|amanece|amanecera) (?:la noche|el tiempo|el clima|hoy|"
    r"manana|la madrugada|la helada|el dia)\b"
    r"|\b(?:cuanto|que tanto|que tan) (?:frio|helara)\b|\bque temperatura\b|\ba cuantos grados\b"
    r"|\b(?:hay|habra|tienen|viene) (?:riesgo|peligro|aviso|alerta|pronostico) de helada\b|\bse espera (?:helada|frio)\b")
WEATHER_WORD = re.compile(r"\b(?:helada|elada|helar|hiela|hela|frio|temperatura|clima|tiempo|escarcha|granizo|aviso|"
                          r"alerta|proteger|tapar|cubrir)\b")
SOON = re.compile(r"\b(?:hoy|esta noche|en la noche|la noche|manana|madrugada|ahorita|al rato)\b")
FULL_FORECAST = re.compile(r"(?:pronostico|el pronostico|clima|el clima|el tiempo|helada hoy|va a helar)[.!?¿ ]*")
RESOW = re.compile(r"\b(?:re)?sembrar\b|\bresiembr\w*|\bsiembro\b|\bvariedad\b|\bsemilla\b")
PROGRAM = re.compile(
    r"\b(?:papeles|requisitos|documentos|apoyos?|pagan|pagar|pago|paga|seguro|delegacion|pasacme|programa|"
    r"siniestros?|reportar|reporte|reporto|reportarlo|reportamos|avisar|registr\w*|pre ?registro|prerregistro|inscrit\w*|inscribir\w*|"
    r"ventanilla|plazo|folio|dinero|cobrar|cobro|indemniz\w*|cubre|compens\w*|tramite|solicitud)\b")
FULL_PROGRAM = re.compile(r"(?:apoyo|el apoyo|requisitos|programa|pasacme|papeles)[.!?¿ ]*")
GREETING = re.compile(r"^(?:hola|buenas(?: tardes| noches| dias)?|buenos dias|buen dia|que tal|gracias|muchas gracias|"
                      r"mil gracias|ayuda|menu|info|informacion)\b|\bgracias\b"
                      r"|^(?:ok|oki|okey|va|sale|de acuerdo|esta bien|bueno)[.!¡ ]*$")


@dataclass
class Route:
    labels: dict[str, bool]
    primary: str
    questions: list[str] = field(default_factory=list)   # question labels also present (answered before a report)

    def __getattr__(self, k):
        if k in LABELS:
            return self.labels[k]
        raise AttributeError(k)


def classify(text: str | None, pending: str | None = None, today: Date | None = None,
             parcel_area: float | None = None) -> Route:
    """pending = the slot the bot just asked for in an open loss report (crop/area_ha/cause/date), if any.
    Then a plain answer ("como media hectárea") counts as damage information."""
    t = norm(text or "")
    segs = segments(t)
    qtext = " / ".join(s.text for s in segs if s.question)
    optout = bool(OPTOUT_FULL.fullmatch(t) or OPTOUT.search(t))
    optin = not optout and bool(OPTIN_FULL.fullmatch(t) or OPTIN.search(t))
    damage = _damage(segs, t)
    if pending and not damage and not optout and segs:
        own = [s.text for s in segs if not s.question and not META.search(s.text)]
        denied = any(NO_DAMAGE.search(s.text) or is_third_party(s.text) for s in segs)
        if own and not denied:
            sl = RegexSlotFiller().fill(" , ".join(own), today or Date.today(), parcel_area, expected=pending)
            damage = any(sl[k] is not None for k in ("crop", "area_ha", "cause", "date"))
    forecast = bool(FORECAST.search(t) or FULL_FORECAST.fullmatch(t)
                    or any(s.question and WEATHER_WORD.search(s.text) and SOON.search(s.text) for s in segs))
    plant = planting.is_planting_question(text) or bool(qtext and RESOW.search(qtext))
    program = bool(FULL_PROGRAM.fullmatch(t) or (qtext and PROGRAM.search(qtext)))
    greeting = bool(GREETING.search(t))
    labels = dict(damage=damage and not optin, optout=optout, optin=optin, forecast=forecast, planting=plant,
                  program=program, greeting=greeting)
    primary = next((k for k in PRECEDENCE if labels[k]), "other")
    return Route(labels, primary, [k for k in ("forecast", "planting", "program") if labels[k]])


# ------------------------------------------------------------------ pre-registro answer
_PRE_META = re.compile(r"\bsystem\b|\bsistema\b|^(?:marca|marque|responde|responda|ignora|escribe|pon)\b|\bmarca \w+_")
_PRE_SPLIT = re.compile(r"\s*(?:\bpero\b|\bbueno\b|\bmejor dicho\b|\bah no\b|\bya me acorde\b|\.\.\.|\bsino\b)\s*")
_PRE_NO_STRONG = re.compile(r"\byo (?:todavia |aun )?no\b")
_PRE_UNSURE = re.compile(
    r"\bno se\b(?! como\b)|\bcreo que\b|\bcreo\b$|\ba lo mejor\b|\btal vez\b|\bquizas?\b|\bno estoy segur\w*|"
    r"\bno me acuerdo\b|\bno recuerdo\b|\bsepa\b|\bquien sabe\b|\bque es (?:eso|el|un|lo)\b|\bcual (?:pre ?registro|registro)\b|"
    r"\bsi cuenta\b|\bdijo que (?:el|ella|ellos) lo\b|\bsupongo\b|\bparece que\b|\bigual y\b")
_PRE_NO = re.compile(
    r"^(?:no|nel|nop|nope|nada|nunca|todavia|aun|ni)\b|\b(?:todavia|aun) no\b|\bno (?:lo )?(?:he|hemos|ha)\b|"
    r"\bno (?:lo )?(?:hice|hicimos)\b|\bno (?:he|hemos) podido\b|\bno pude\b|\bno alcance\b|\bya mero\b|"
    r"\bmanana (?:voy|lo hago|lo hacemos|vamos|voy a)\b|\b(?:voy|vamos|iba|ibamos) a (?:ir|hacerlo|registrar\w*|llenar\w*)\b|"
    r"\b(?:el|este|la otra|el otro) (?:lunes|martes|miercoles|jueves|viernes|sabado|domingo|semana|mes) (?:voy|vamos|lo hago|me doy)\b|"
    r"\ba ver si\b|\bno me (?:lo )?(?:quisieron|quiso|dejaron|recibieron|aceptaron|atendieron)\b|\bestaba cerrado\b|"
    r"\bme falt(?:a|aba|o)\b|\bno se como\b|\bnel\b|\bno me han\b|\bni idea de como\b|\bpendiente\b")
_PRE_PAPER = re.compile(r"\b(?:folio|comprobante|constancia|acuse|numero de registro|respuesta|papelito)\b")
_PRE_YES = re.compile(
    r"^(?:si|sip|simon|claro|ya|afirmativo|asi es|correcto|por supuesto|efectivamente)\b|\b(?:que|pues) si\b|"
    r"\b(?:lo|la) (?:hice|llene|hicimos|llenamos|mande|meti)\b|\bme (?:registre|inscribi)\b|"
    r"\bya (?:lo |me |la )?(?:hice|hizo|hicimos|hicieron|ise|ize|izo|llene|lleno|llenamos|llenaron|registre|"
    r"registro|registraron|inscribi|inscribio|fui|fuimos|estoy|esta|tengo|tiene|quedo|quede)\b|"
    r"\b(?:lo|la|me) (?:hizo|hicieron|lleno|llenaron|registro|registraron|inscribio) (?:mi|el|la)\b|"
    r"\bmi (?:esposa|esposo|hijo|hija|senora|vieja|hermano|hermana|yerno|nuera|nieto|nieta|mujer)\b.{0,20}"
    r"\b(?:lo |me )?(?:hizo|lleno|registro|inscribio)\b|"
    r"\bdesde (?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre|"
    r"el ano pasado|la semana pasada|el mes pasado|hace)\b|\bestoy (?:registrad|inscrit)\w*|\bfui a \w+(?: \w+)? a registrarme\b")


def _prereg_polarity(seg: str) -> str | None:
    if not seg:
        return None
    if _PRE_NO_STRONG.search(seg):
        return "no"
    if _PRE_PAPER.search(seg) and not _PRE_YES.search(seg):
        return None               # "no me han dado el folio" is about the receipt, not about doing it
    if _PRE_UNSURE.search(seg):
        return "unclear"
    if _PRE_NO.search(seg):
        return "no"
    if _PRE_YES.search(seg):
        return "si"
    return None


def read_preregistro(text: str | None) -> str:
    """Answer to "¿Ya hizo su pre-registro?": "si" | "no" | "unclear". The last part of the answer with a
    polarity wins ("No... bueno sí, mi esposa lo hizo" -> si; "sí fui pero estaba cerrado" -> no). Doubt
    ("creo que sí", "no sé si ya lo hizo"), a question ("¿qué es eso?") or text addressed to the system -> unclear."""
    t = norm(text or "")
    if not t or _PRE_META.search(t):
        return "unclear"
    for seg in reversed([s.strip(" ,") for s in _PRE_SPLIT.split(t)]):
        p = _prereg_polarity(seg)
        if p:
            return p
    return "unclear"


# ------------------------------------------------------------------ farmer-stated stage (maize)
STAGES = ("emergencia", "crecimiento_vegetativo", "floracion", "llenado_de_grano", "madurez")
_STAGE_PATS = [
    ("crecimiento_vegetativo", r"\b(?:todavia|aun) (?:no|sin) (?:\w+ )?(?:espig|echaba espiga|jilot|salia la espiga)\w*|"
                               r"\bsin espiga\b|\b(?:a|hasta|como a|por) la rodilla\b|\bgrandecit\w*|\bde un metro\b|"
                               r"\bunas? \d+ hojas\b|\b(?:ocho|seis|siete|diez|cinco) hojas\b|\bcreciendo\b"),
    ("floracion", r"\bjilot\w*|\bespig(?:ando|ado|o|aba|ada)\b|\becha(?:ndo|ba) (?:la )?espiga\b|\bpelo\b|\bflor(?:eando|eaba|acion)\b"),
    ("llenado_de_grano", r"\ben elote\b|\belotes?\b|\bllenando (?:el )?grano\b|\bgrano tierno\b|\blechos[oa]s?\b|\btiernas?\b"),
    ("madurez", r"\bmaciz\w*|\bpizc\w*|\bmazorcas? (?:ya )?(?:bien )?secas?\b|\bsecandose en la mata\b|\bya (?:estaba )?seca\b"),
    ("emergencia", r"\brecien (?:nacid|brotad|salid|sembrad)\w*|\b(?:apenas|iba) (?:estaba |iba )?(?:saliendo|naciendo|brotando)\b|"
                   r"\b(?:dos|tres|2|3) hojitas\b|\bhojitas\b|\bestaba naciendo\b"),
]
_STAGE_UNKNOWN = re.compile(r"\bno (?:le )?se (?:decir|en que)\b|\bno me acuerdo en que\b|\bni idea\b")
_EVENT_NOW = re.compile(r"\b(?:ahorita|ahora|hoy en dia|el ano pasado|ese si fue|aquella vez)\b")


def read_stage(text: str | None) -> str | None:
    """Maize stage the farmer SAYS the crop was in when the damage happened, or None if not said. Printed next to
    the calendar estimate as the farmer's declaration; the inspection confirms it."""
    t = norm(text or "")
    if not t or _STAGE_UNKNOWN.search(t):
        return None
    for seg in segments(t):
        c = seg.text
        if seg.question or is_third_party(c) or re.search(r"\bde mi (?:vecino|compadre|hermano)\b", c) \
                or _EVENT_NOW.search(c):
            continue
        for key, pat in _STAGE_PATS:
            if re.search(pat, c):
                return key
    return None
