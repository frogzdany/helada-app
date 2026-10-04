import json
from datetime import date

import httpx
import pytest

from backend.slots import LLMSlotFiller, RegexSlotFiller, fill_slots, intent

TODAY = date(2026, 10, 4)   # a Sunday
AREA = 2.5
F = RegexSlotFiller()

# (phrase, expected subset of slots)
PHRASES = [
    ("Sí se quemó la milpa, como una hectárea", {"cause": "helada", "crop": "maiz_temporal", "area_ha": 1.0}),
    ("Se heló todo el maíz anoche", {"cause": "helada", "crop": "maiz_temporal", "date": "2026-10-03"}),
    ("Nos cayó granizo ayer, media hectárea de milpa", {"cause": "granizo", "area_ha": 0.5, "date": "2026-10-03"}),
    ("Fue una granizada muy fuerte el viernes, se perdieron dos hectáreas", {"cause": "granizo", "area_ha": 2.0, "date": "2026-10-02"}),
    ("La helada del 3 de octubre me quemó hectárea y media de papa", {"cause": "helada", "area_ha": 1.5, "crop": "papa", "date": "2026-10-03"}),
    ("Se me secó la avena porque no ha llovido", {"cause": "sequia", "crop": "avena"}),
    ("Se inundó la parcela, se ahogaron como 3 has", {"cause": "inundacion", "area_ha": 3.0}),
    ("Una hectárea y media quemada por el frío, antier", {"cause": "helada", "area_ha": 1.5, "date": "2026-10-02"}),
    ("Se perdió toda la parcela con la helada", {"cause": "helada", "area_ha": 2.5}),
    ("un cuarto de hectárea de haba se heló", {"cause": "helada", "area_ha": 0.25, "crop": "haba"}),
    ("Hace 3 días cayó escarcha y dañó la milpa", {"cause": "helada", "date": "2026-10-01", "crop": "maiz_temporal"}),
    ("Se heló 1.5 ha de maíz", {"cause": "helada", "area_ha": 1.5}),
    ("Tres cuartos de hectárea con granizo", {"cause": "granizo", "area_ha": 0.75}),
    ("La mitad de la milpa se quemó con el hielo esta madrugada", {"cause": "helada", "area_ha": 1.25, "date": "2026-10-04"}),
    ("Dos y media hectáreas de maíz, se nos heló el lunes", {"cause": "helada", "area_ha": 2.5, "date": "2026-09-28"}),
]


@pytest.mark.parametrize("phrase,expected", PHRASES)
def test_regex_slot_filler(phrase, expected):
    out = F.fill(phrase, TODAY, AREA)
    for k, v in expected.items():
        assert out[k] == v, f"{phrase!r}: {k}={out[k]!r}, expected {v!r}"
    assert out["notes"] == phrase


def test_fifteen_phrases():
    assert len(PHRASES) == 15


def test_missing_slots_stay_none():
    out = F.fill("Buenas tardes, le quería contar algo", TODAY, AREA)
    assert out["cause"] is None and out["area_ha"] is None and out["date"] is None


def test_bare_number_when_area_was_asked():
    assert F.fill("como una", TODAY, AREA, expected="area_ha")["area_ha"] == 1.0
    assert F.fill("media", TODAY, AREA, expected="area_ha")["area_ha"] == 0.5
    assert F.fill("toda", TODAY, AREA, expected="area_ha")["area_ha"] == 2.5
    assert F.fill("como una", TODAY, AREA)["area_ha"] is None   # not asked -> not guessed


@pytest.mark.parametrize("text,it", [("BAJA", "baja"), ("alta", "alta"), ("sí", "si"), ("no", "no"),
                                     ("listo", "listo"), ("ya no tengo más fotos", "listo"), ("hola", "ayuda")])
def test_intents(text, it):
    assert intent(text) == it


def _fake_llm(monkeypatch, content: str):
    def post(url, json=None, timeout=None):  # noqa: A002
        assert url.endswith("/chat/completions")
        assert json["response_format"]["type"] == "json_schema"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr("backend.slots.httpx.post", post)


def test_llm_output_is_schema_validated(monkeypatch):
    _fake_llm(monkeypatch, json.dumps({"crop": "maiz_temporal", "area_ha": 1, "cause": "helada",
                                       "date": "2026-10-03", "notes": "milpa quemada"}))
    out, eng = fill_slots(LLMSlotFiller("http://llm/v1", "qwen3:1.7b"), "se quemó la milpa", TODAY, AREA)
    assert eng == "llm" and out["cause"] == "helada" and out["area_ha"] == 1.0


def test_llm_out_of_schema_becomes_none(monkeypatch):
    _fake_llm(monkeypatch, "<think>hmm</think>" + json.dumps(
        {"crop": "tomate", "area_ha": -4, "cause": "extraterrestres", "date": "2030-01-01", "notes": 5}))
    out, _ = fill_slots(LLMSlotFiller("http://llm/v1", "m"), "algo raro", TODAY, AREA)
    assert out["crop"] is None and out["area_ha"] is None and out["cause"] is None and out["date"] is None


def test_llm_unreachable_falls_back_to_regex(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr("backend.slots.httpx.post", boom)
    out, eng = fill_slots(LLMSlotFiller("http://llm/v1", "m"), "se heló media hectárea", TODAY, AREA)
    assert eng == "regex(fallback)" and out["area_ha"] == 0.5 and out["cause"] == "helada"


@pytest.mark.parametrize("phrase,today,expected", [
    # A day that has not come yet is not moved a year back in silence; the slot stays empty and is asked
    ("se me heló el maíz el 20 de noviembre, una hectárea", date(2025, 11, 15), None),
    ("se heló la milpa el 30 de diciembre", date(2025, 11, 15), None),
    # said in January about the end of December: last year, and valid
    ("se me heló el maíz el 31 de diciembre", date(2026, 1, 5), "2025-12-31"),
    ("heló el 20 de noviembre", date(2026, 1, 10), "2025-11-20"),
    # a past day of this year stays as said, even when it is outside the 10-day notice
    ("se heló el 2 de noviembre", date(2025, 11, 15), "2025-11-02"),
    ("se me heló hace 15 días", date(2025, 11, 15), "2025-10-31"),
])
def test_date_not_yet_come_is_never_guessed(phrase, today, expected):
    assert RegexSlotFiller().fill(phrase, today, 2.0)["date"] == expected
