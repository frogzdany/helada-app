"""Intent router, opt-out/opt-in, forecast/program replies, cause correction and pre-registro reading:
unit rules (intents.py, slots.py) and the WhatsApp flow (conversation.py). Offline and deterministic."""
from datetime import date, datetime

import pytest

from backend import pasacme, roster
from backend.conversation import Inbound, handle_inbound
from backend.intents import classify, read_preregistro, read_stage
from backend.photo import make_exif_jpeg
from backend.slots import RegexSlotFiller

R = {p["parcel_id"]: p for p in roster.load_roster_file()}
P01 = R["P01"]
TODAY = date(2026, 10, 4)


def phone(pid="P01"):
    return R[pid]["phone"]


# ------------------------------------------------------------------ router: primary route and precedence
@pytest.mark.parametrize("text,primary", [
    ("¿Va a caer helada esta noche?", "forecast"),
    ("¿cómo viene la noche?", "forecast"),
    ("Buenas tardes, ¿cuánto va a bajar la temperatura esta noche?", "forecast"),
    ("PRONÓSTICO", "forecast"),
    ("¿va a helar?", "forecast"),
    ("¿Cuándo siembro?", "planting"),
    ("¿Qué papeles necesito para el apoyo de siniestros?", "program"),
    ("Si se hiela la milpa, ¿me pagan algo?", "program"),
    ("APOYO", "program"),
    ("Sí, se quemó la milpa, como una hectárea. Fue anoche.", "damage"),
    ("DAÑO", "damage"),
    ("hola", "greeting"),
    ("Gracias por el aviso, tapé las papas y no les pasó nada", "greeting"),
    ("asdf qwerty", "other"),
    ("BAJA", "optout"), ("STOP", "optout"), ("ALTO", "optout"), ("baja.", "optout"),
    ("ya no me manden mensajes", "optout"), ("no quiero avisos", "optout"), ("bájenme de la lista", "optout"),
    ("Ya no quiero que me lleguen los avisos de helada, ya vendí el terreno", "optout"),
    ("ALTA", "optin"), ("quiero volver a recibir los avisos", "optin"), ("denme de alta otra vez", "optin"),
    # opt-out outranks everything else in the same message
    ("El martes nos cayó piedra y me tumbó toda la avena. ¿Dónde voy a reportar? Y dejen de mandarme los avisos",
     "optout"),
])
def test_primary_route(text, primary):
    assert classify(text).primary == primary


@pytest.mark.parametrize("text", [
    "Ya no me llegó la alerta de anoche", "no me mandaron el aviso", "la parte baja se heló", "baja la temperatura?",
    "ya no tengo más fotos", "no me manden la foto otra vez",
])
def test_not_an_optout(text):
    assert not classify(text).optout


def test_damage_with_question_keeps_both():
    r = classify("antier se me heló la milpa de abajo. ¿Todavía alcanzo a resembrar algo?")
    assert r.primary == "damage" and r.questions == ["planting"]


@pytest.mark.parametrize("text", [
    "¿Va a caer helada esta noche?", "Si se hiela la milpa, ¿me pagan?", "Anoche cayó escarcha pero la milpa aguantó",
    "Mi compadre dice que a él se le heló todo, a mí todavía no", "tapé las papas y no les pasó nada",
    "Dicen que va a caer helada en mi parcela", "como una hectárea",
])
def test_questions_hypotheticals_negations_and_others_fields_are_not_damage(text):
    assert not classify(text).damage


def test_pending_answer_counts_as_damage_info():
    assert classify("Como media hectárea", pending="area_ha", parcel_area=2.5).damage
    assert not classify("el de mi hermano, yo este año no sembré", pending="crop").damage


# ------------------------------------------------------------------ cause correction
F = RegexSlotFiller()


@pytest.mark.parametrize("text,cause", [
    ("No fue helada, fue granizo", "granizo"),
    ("No fue helada, fue granizo; me tumbó media hectárea de maíz el martes", "granizo"),
    ("Cayeron bolitas de hielo y rompieron las hojas", "granizo"),
    ("Nos pegó una granizada", "granizo"),
    ("no se heló, se secó por falta de agua", "sequia"),
    ("Se quemó la milpa", "helada"),                       # local idiom: burnt without fire = frost
    ("Se me quemó la milpa con la helada", "helada"),
    ("se quemó con el sol la avena", "sequia"),
    ("La sequía acabó con el frijol", "sequia"),
    ("Se quemó la milpa porque el vecino prendió lumbre", "otra"),
    ("pensé que era helada pero es gallina ciega", "otra"),
    ("Primero cayó granizo y luego heló", None),           # two causes: leave empty -> the bot asks
    ("no se heló", None),
])
def test_cause_with_negation_and_idioms(text, cause):
    assert F.fill(text, TODAY, 2.5)["cause"] == cause


def test_two_causes_when_the_bot_asked_takes_the_first():
    assert F.fill("granizo y luego helada", TODAY, 2.5, expected="cause")["cause"] == "granizo"


def test_crop_of_someone_else_is_ignored():
    out = F.fill("A mi compadre se le inundó el trigo; a mí la sequía me secó la avena", TODAY, 2.5)
    assert out["cause"] == "sequia" and out["crop"] == "avena"


# ------------------------------------------------------------------ pre-registro answer
@pytest.mark.parametrize("text,ans", [
    ("sí", "si"), ("Sí, desde marzo", "si"), ("lo hizo mi esposa", "si"), ("ya lo llené", "si"),
    ("No... bueno sí, mi esposa lo hizo", "si"),
    ("no", "no"), ("todavía no", "no"), ("nel", "no"), ("mañana voy", "no"), ("sí fui pero estaba cerrado", "no"),
    ("Mi hermano sí ya lo hizo, yo no", "no"),
    ("no sé qué es eso", "unclear"), ("creo que sí", "unclear"), ("¿Qué es eso del pre-registro?", "unclear"),
    ("listo", "unclear"), ("La foto la mando al rato", "unclear"),
])
def test_read_preregistro(text, ans):
    assert read_preregistro(text) == ans


def test_stage_is_read_only_when_said():
    assert read_stage("Se me heló la milpa cuando ya estaba jiloteando") == "floracion"
    assert read_stage("se me heló la milpa") is None


# ------------------------------------------------------------------ checklist: "Confirmar: pre-registro"
def _report(pre):
    return {"slots": {"crop": None, "area_ha": 2.0, "cause": "helada", "date": "2026-10-03", "notes": "x"},
            "photos": [{"status": "ok"}] * 4, "preregistro": pre}


def test_checklist_preregistro_states():
    assert pasacme.build_checklist(P01, _report(True), TODAY)["status"] == "Documentos completos"
    assert pasacme.build_checklist(P01, _report(False), TODAY)["status"] == "Falta: pre-registro PASACME"
    cl = pasacme.build_checklist(P01, _report("unclear"), TODAY)
    assert cl["status"] == "Confirmar: pre-registro PASACME" and not cl["complete"] and cl["missing"] == []
    assert next(i for i in cl["items"] if i["key"] == "preregistro")["status"] == "revisar"
    both = pasacme.build_checklist(P01, dict(_report("unclear"), photos=[]), TODAY)
    assert both["status"] == "Falta: fotos (0 de 4); Confirmar: pre-registro PASACME"


def test_checklist_prints_the_stage_the_farmer_said():
    cl = pasacme.build_checklist(P01, dict(_report(True), stage_declared="floracion"), TODAY)
    it = next(i for i in cl["items"] if i["key"] == "cultivo")
    assert "etapa declarada por el productor: floración" in it["detail"] and "calendario" in it["detail"]


# ------------------------------------------------------------------ WhatsApp flow
def say(svc, text, when="2025-11-15T08:00", pid="P01"):
    return handle_inbound(svc, Inbound(phone(pid), "text", text, received_at=svc.now_local(when)))


def n_reports(svc):
    return svc.db.one("SELECT COUNT(*) n FROM reports")["n"]


@pytest.mark.parametrize("q", ["¿Va a caer helada esta noche?", "¿cómo viene la noche?", "¿va a helar hoy?"])
def test_forecast_question_never_opens_a_report(svc, q):
    res = say(svc, q, when="2025-11-14T12:00")
    assert res.route == "forecast" and res.report_id is None and n_reports(svc) == 0
    txt = res.replies[0]["text"]
    # tests run on the backup calculation (TEMP_STUB): the answer is «no estoy seguro», never a number
    assert "parcela P01" in txt and "No estoy seguro" in txt and "°C" not in txt
    assert any(e["action"] == "forecast.reply" for e in svc.audit.entries(20))


def test_forecast_reply_uses_tonights_alert(svc, real_model):
    svc.send_alerts("2025-11-14", scenario="demo", now="2025-11-14T18:30", parcel_ids=["P01"])
    res = say(svc, "¿Va a caer helada esta noche?", when="2025-11-14T19:00")
    txt = res.replies[0]["text"]
    assert "Ya le mandé el aviso de helada" in txt and "5 de cada 10" in txt and n_reports(svc) == 0


def test_forecast_question_during_a_report_keeps_the_report(svc):
    r1 = say(svc, "se heló la milpa anoche")
    assert r1.report_id and "cuánto terreno" in r1.replies[0]["text"]
    r2 = say(svc, "¿y va a volver a helar hoy?")
    assert r2.report_id is None and "Para seguir con su reporte" in r2.replies[-1]["text"]
    r3 = say(svc, "como una hectárea")
    assert r3.report_id == r1.report_id and r3.replies[0]["text"].startswith("Entendí: ") and "fotos" in r3.replies[1]["text"]


def test_program_question_and_menu(svc):
    res = say(svc, "¿Hasta cuándo tengo para avisar a la delegación?")
    assert res.report_id is None and "10 días naturales" in res.replies[0]["text"]
    assert "Lo decide la Secretaría del Campo" in res.replies[0]["text"]
    other = say(svc, "asdf")
    assert other.route == "other" and "PRONÓSTICO" in other.replies[0]["text"] and "BAJA" in other.replies[0]["text"]
    thanks = say(svc, "Gracias por el aviso, tapé las papas y no les pasó nada")
    assert thanks.replies[0]["text"].startswith("Con gusto.") and n_reports(svc) == 0


def test_damage_report_opens_with_corrected_cause(svc):
    res = say(svc, "No fue helada, fue granizo; me tumbó media hectárea de maíz el martes")
    assert res.report_id and res.slots["cause"] == "granizo" and res.slots["area_ha"] == 0.5


def _opted_out(svc, pid="P01"):
    r = svc.db.one("SELECT opted_out FROM contacts WHERE phone=?", (phone(pid),))
    return bool(r and r["opted_out"])


@pytest.mark.parametrize("text", ["ya no me manden mensajes", "no quiero avisos", "bájenme de la lista",
                                  "STOP", "ALTO", "BAJA"])
def test_optout_phrases_stop_alerts_and_are_audited(svc, text, real_model):
    res = say(svc, text)
    assert _opted_out(svc) and "ya no le mandaremos avisos" in res.replies[0]["text"]
    e = next(e for e in svc.audit.entries(20) if e["action"] == "contact.optout")
    assert e["payload"]["rule"] in ("keyword", "phrase") and svc.audit.verify()["ok"]
    r = svc.send_alerts("2025-11-14", scenario="demo", now="2025-11-14T18:30", parcel_ids=["P01"])
    assert r["results"][0]["reason"] == "baja"


def test_optout_cancels_queued_alert_and_optin_reenables(svc, real_model):
    q = svc.send_alerts("2025-11-14", scenario="demo", now="2025-11-14T12:00", parcel_ids=["P01"])
    assert q["results"][0]["action"] == "queue"
    say(svc, "dejen de mandarme los avisos", when="2025-11-14T13:00")
    a = svc.db.one("SELECT status FROM alerts WHERE parcel_id='P01'")
    assert a["status"] == "cancelled"
    assert svc.flush_queue("2025-11-14T18:30") == []
    out = [m for m in svc.messages(phone()) if m["direction"] == "out"]
    assert not any("Aviso de Helada" in (m["text"] or "") for m in out)
    res = say(svc, "quiero volver a recibir los avisos", when="2025-11-14T13:05")
    assert not _opted_out(svc) and "volverá a recibir" in res.replies[0]["text"]
    assert any(e["action"] == "contact.optin" for e in svc.audit.entries(20))
    r = svc.send_alerts("2025-11-14", scenario="demo", now="2025-11-14T18:30", parcel_ids=["P01"])
    assert r["results"][0]["action"] == "send"


def test_queued_alert_is_not_delivered_after_optout_by_any_path(svc, real_model):
    svc.send_alerts("2025-11-14", scenario="demo", now="2025-11-14T12:00", parcel_ids=["P01"])
    svc.db.execute("INSERT INTO contacts(phone, opted_out) VALUES (?,1)", (phone(),))   # e.g. set by an officer
    got = svc.flush_queue("2025-11-14T18:30")
    assert got[0]["status"] == "cancelled" and got[0]["reason"] == "baja"


def test_not_an_optout_complaint_opens_the_report(svc):
    res = say(svc, "Ya no me llegó la alerta de anoche y se me quemó el haba de la orilla, ¿qué pasó con el mensaje?")
    assert not _opted_out(svc) and res.report_id


def test_damage_and_optout_in_one_message_do_both(svc):
    res = say(svc, "El martes nos cayó piedra y me tumbó toda la avena. Y dejen de mandarme los avisos")
    assert _opted_out(svc) and res.report_id and res.slots["cause"] == "granizo"


# ------------------------------------------------------------------ pre-registro in the flow
def _to_prereg(svc, tmp_path, when="2025-11-15T08:00"):
    say(svc, "Se me heló la milpa anoche, como una hectárea", when=when)
    for i in range(4):
        p = make_exif_jpeg(tmp_path / f"f{i}.jpg", when=datetime.fromisoformat("2025-11-15T07:40"),
                           lat=P01["lat"] + 0.0003 * i, lon=P01["lon"])
        res = handle_inbound(svc, Inbound(phone(), "image", media_path=p, received_at=svc.now_local(when)))
    assert "pre-registro" in res.replies[0]["text"]


@pytest.mark.parametrize("answers,status", [
    (["Sí, desde marzo"], "Documentos completos"),
    (["lo hizo mi esposa"], "Documentos completos"),
    (["todavía no"], "Falta: pre-registro PASACME"),
    (["no sé qué es eso", "sí, ya lo llené"], "Documentos completos"),
    (["no sé qué es eso", "creo que sí"], "Confirmar: pre-registro PASACME"),
])
def test_preregistro_answers_drive_the_packet(svc, tmp_path, answers, status):
    _to_prereg(svc, tmp_path)
    for i, a in enumerate(answers):
        res = say(svc, a)
        if i < len(answers) - 1:
            assert res.packet is None and "Responda SÍ o NO" in res.replies[0]["text"]
    assert res.packet["summary"]["status"] == status


def test_packet_carries_stage_said_by_farmer(svc, tmp_path):
    say(svc, "Se me heló la milpa anoche cuando ya estaba jiloteando, como una hectárea")
    for i in range(4):
        p = make_exif_jpeg(tmp_path / f"g{i}.jpg", when=datetime.fromisoformat("2025-11-15T07:40"),
                           lat=P01["lat"] + 0.0003 * i, lon=P01["lon"])
        handle_inbound(svc, Inbound(phone(), "image", media_path=p, received_at=svc.now_local("2025-11-15T08:00")))
    res = say(svc, "sí")
    it = next(i for i in res.packet["checklist"]["items"] if i["key"] == "cultivo")
    assert "etapa declarada por el productor: floración" in it["detail"]


def test_area_is_said_back_the_way_farmers_say_it():
    from backend.conversation import _area_dicha
    assert [_area_dicha(a) for a in (0.5, 1, 1.5, 2, 2.5, 0.75)] == [
        "media hectárea", "1 hectárea", "hectárea y media", "2 hectáreas", "2 hectáreas y media", "0.75 hectáreas"]


def test_bot_says_back_what_it_understood_and_takes_a_correction(svc):
    """Before asking for photos the bot repeats the four facts in one line. It says them once; if the farmer
    corrects one, it repeats the line with the correction and does not ask for anything twice."""
    r1 = say(svc, "se me heló el maíz anoche, como una hectárea")
    assert [m["text"][:8] for m in r1.replies] == ["Entendí:", "Gracias."]
    assert "maíz de temporal, helada, 1 hectárea, el " in r1.replies[0]["text"]
    assert "dígamelo y lo corrijo" in r1.replies[0]["text"]
    r2 = say(svc, "no, fue media hectárea")
    assert "maíz de temporal, helada, media hectárea, el " in r2.replies[0]["text"]
    r3 = say(svc, "sí, así es")                      # nothing changed: the line is not repeated
    assert not any(m["text"].startswith("Entendí:") for m in r3.replies)


def test_future_damage_date_is_asked_not_moved_a_year_back(svc):
    """«el 20 de noviembre» said on the 15th. The bot asks for the day instead of filing it under 2024."""
    r = say(svc, "se me heló el maíz el 20 de noviembre, una hectárea", when="2025-11-15T08:00", pid="P04")
    assert r.slots["date"] is None and "¿Qué día pasó?" in r.replies[-1]["text"]
    assert not any("Entendí" in x["text"] for x in r.replies)
    r2 = say(svc, "anoche", when="2025-11-15T08:05", pid="P04")
    assert r2.replies[0]["text"].startswith("Entendí: ") and "viernes 14 de noviembre" in r2.replies[0]["text"]
    assert "2024" not in r2.replies[0]["text"] and "2025" not in r2.replies[0]["text"]


def test_understood_line_says_the_year_when_it_is_not_this_one(svc):
    r = say(svc, "se me heló el maíz el 31 de diciembre, media hectárea", when="2026-01-05T08:00")
    assert "el miércoles 31 de diciembre de 2025" in r.replies[0]["text"]


def test_damaged_area_larger_than_the_parcel_is_asked_again(svc):
    """P03 has 3 ha registered. «Perdí 20 hectáreas» is not written down; the bot says why and asks."""
    r = say(svc, "se me heló el maíz anoche, perdí 20 hectáreas", pid="P03")
    assert r.slots["area_ha"] is None
    txt = r.replies[-1]["text"]
    assert txt.startswith("Su parcela registrada tiene 3 hectáreas; no puedo anotar más que eso.") and "cuánto terreno" in txt
    r2 = say(svc, "dos hectáreas", when="2025-11-15T08:05", pid="P03")
    assert r2.replies[0]["text"].startswith("Entendí: ") and "2 hectáreas" in r2.replies[0]["text"]
