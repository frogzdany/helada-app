"""Hybrid slot filler (LLM proposes, rules validate), LLM request shape, ASR engine choice,
and the benchmark helpers. All offline: the LLM is mocked."""
import json
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

from backend import asr
from backend.slots import (HybridSlotFiller, LLMSlotFiller, RegexSlotFiller, fill_slots, llm_user_prompt,
                           make_filler)

TODAY = date(2026, 10, 4)   # Sunday
AREA = 2.5
APP = Path(__file__).resolve().parents[1]


def _fake_llm(monkeypatch, payload: dict | str, seen: list | None = None, status: int = 200):
    def post(url, json=None, timeout=None):  # noqa: A002
        if seen is not None:
            seen.append(json)
        content = payload if isinstance(payload, str) else __import__("json").dumps(payload)
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr("backend.slots.httpx.post", post)


def _llm_says(**kw):
    base = {"crop": None, "area_ha": None, "cause": None, "date": None, "notes": None}
    base.update(kw)
    return base


H = HybridSlotFiller(LLMSlotFiller("http://llm/v1", "m"))


def test_make_filler_modes():
    assert isinstance(make_filler("", "m"), RegexSlotFiller)
    assert isinstance(make_filler("http://x/v1", "m"), HybridSlotFiller)
    assert isinstance(make_filler("http://x/v1", "m", mode="llm"), LLMSlotFiller)
    assert isinstance(make_filler("http://x/v1", "m", mode="regex"), RegexSlotFiller)


def test_hybrid_agreement_passes(monkeypatch):
    _fake_llm(monkeypatch, _llm_says(crop="maiz_temporal", cause="helada", area_ha=1.5, date="2026-10-03"))
    out, eng = fill_slots(H, "Se me heló la milpa anoche, como hectárea y media", TODAY, AREA)
    assert eng == "llm+regex"
    assert out == {"crop": "maiz_temporal", "cause": "helada", "area_ha": 1.5, "date": "2026-10-03",
                   "notes": "Se me heló la milpa anoche, como hectárea y media"}


def test_hybrid_crop_or_cause_disagreement_empties_slot_for_follow_up(monkeypatch):
    _fake_llm(monkeypatch, _llm_says(crop="maiz_temporal", cause="granizo", area_ha=1.5, date="2026-10-03"))
    out, eng = fill_slots(H, "Se me heló la milpa anoche, como hectárea y media", TODAY, AREA)
    assert out["cause"] is None and out["crop"] == "maiz_temporal"
    assert eng == "llm+regex conflict=cause"


def test_hybrid_rules_own_area_and_date(monkeypatch):
    # classic 1-2B mistakes: "hectárea y media" -> 0.5, "anoche" -> today. Rules win, no re-ask.
    _fake_llm(monkeypatch, _llm_says(crop="maiz_temporal", cause="helada", area_ha=0.5, date="2026-10-04"))
    out, eng = fill_slots(H, "Se me heló la milpa anoche, como hectárea y media", TODAY, AREA)
    assert out["area_ha"] == 1.5 and out["date"] == "2026-10-03" and eng == "llm+regex"


def test_hybrid_llm_adds_recall_for_unseen_phrasing(monkeypatch):
    # rules know "maíz" but not "se chamuscó": the LLM's closed-enum cause is accepted
    _fake_llm(monkeypatch, _llm_says(crop="maiz_temporal", cause="helada"))
    out, _ = fill_slots(H, "se me chamuscó el maíz", TODAY, AREA)
    assert out["cause"] == "helada" and out["crop"] == "maiz_temporal"


def test_hybrid_llm_cannot_turn_a_question_into_a_report(monkeypatch):
    _fake_llm(monkeypatch, _llm_says(crop="maiz_temporal", cause="helada", area_ha=2.5, date="2026-10-03"))
    out, eng = fill_slots(H, "¿Cuándo siembro este año?", TODAY, AREA)
    assert all(out[k] is None for k in ("crop", "cause", "area_ha", "date"))


def test_hybrid_llm_answers_the_slot_just_asked(monkeypatch):
    _fake_llm(monkeypatch, _llm_says(crop="otro"))
    out, _ = fill_slots(H, "chícharo", TODAY, AREA, expected="crop")
    assert out["crop"] == "otro"


def test_hybrid_llm_never_invents_area_or_date(monkeypatch):
    _fake_llm(monkeypatch, _llm_says(cause="sequia", area_ha=0.5, date="2026-10-03"))
    out, _ = fill_slots(H, "Se me secó la avena porque no ha llovido", TODAY, AREA)
    assert out["cause"] == "sequia" and out["area_ha"] is None and out["date"] is None


def test_hybrid_out_of_schema_llm_is_ignored(monkeypatch):
    _fake_llm(monkeypatch, _llm_says(crop="tomate", cause="extraterrestres", area_ha=-3, date="2031-01-01"))
    out, _ = fill_slots(H, "se heló media hectárea de papa ayer", TODAY, AREA)
    assert out == {"crop": "papa", "cause": "helada", "area_ha": 0.5, "date": "2026-10-03",
                   "notes": "se heló media hectárea de papa ayer"}


def test_hybrid_llm_down_falls_back_to_regex(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr("backend.slots.httpx.post", boom)
    out, eng = fill_slots(H, "cayó granizo el martes en mi parcela de maíz", TODAY, AREA)
    assert eng == "regex(fallback)" and out["cause"] == "granizo" and out["date"] == "2026-09-29"


def test_hybrid_garbage_json_falls_back(monkeypatch):
    _fake_llm(monkeypatch, "lo siento, no puedo")
    out, eng = fill_slots(H, "se heló la milpa anoche", TODAY, AREA)
    assert out["cause"] == "helada" and out["date"] == "2026-10-03"


def test_llm_request_is_schema_constrained_without_thinking(monkeypatch):
    seen = []
    _fake_llm(monkeypatch, _llm_says(cause="granizo"), seen)
    LLMSlotFiller("http://llm/v1", "helada-slots").fill("granizo", TODAY, AREA)
    body = seen[0]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    assert body["reasoning_effort"] == "none" and body["temperature"] == 0


def test_llm_retries_without_reasoning_effort_on_400(monkeypatch):
    seen = []

    def post(url, json=None, timeout=None):  # noqa: A002
        seen.append(dict(json))
        if "reasoning_effort" in json:
            return httpx.Response(400, json={"error": "unknown field"}, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"cause": "helada"}'}}]},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr("backend.slots.httpx.post", post)
    out = LLMSlotFiller("http://llm/v1", "m").fill("se heló", TODAY, AREA)
    assert out["cause"] == "helada" and len(seen) == 2


def test_llm_notes_keep_farmer_words(monkeypatch):
    _fake_llm(monkeypatch, _llm_says(cause="helada", notes="resumen inventado"))
    assert LLMSlotFiller("http://llm/v1", "m").fill(" se heló ", TODAY, AREA)["notes"] == "se heló"


def test_calendar_prompt_gives_lookup_table():
    p = llm_user_prompt("x", TODAY, AREA, "area_ha")
    assert "2026-10-03 sábado = ayer, anoche" in p and "2026-09-29 martes = hace 5 días" in p
    assert "area_ha" in p and "2.5 ha" in p


# ------------------------------------------------------------------ ASR engine choice
def test_asr_mock_reads_sidecar_and_never_invents(tmp_path):
    a = tmp_path / "n.ogg"
    a.write_bytes(b"x")
    assert asr.transcribe(a, pref="mock").text is None
    (tmp_path / "n.ogg.txt").write_text("se heló la milpa\n", encoding="utf-8")
    t = asr.transcribe(a, pref="mock")
    assert t.text == "se heló la milpa" and t.engine == "mock"


def test_asr_auto_uses_the_real_engine_when_it_is_installed(monkeypatch):
    """auto = faster-whisper when it is importable and its weights are on disk (WHISPER_MODEL only picks the size);
    otherwise the stored transcripts, so a voice note never waits for a download. "whisper" forces the real engine.
    "mock" is always the stored transcripts."""
    import importlib.util
    installed = importlib.util.find_spec("faster_whisper") is not None
    monkeypatch.setattr(asr, "weights_cached", lambda name: True)
    assert asr.engine_name("auto", "") == ("whisper" if installed else "mock")
    assert asr.engine_name("auto", "small") == ("whisper" if installed else "mock")
    monkeypatch.setattr(asr, "weights_cached", lambda name: False)
    assert asr.engine_name("auto", "small") == "mock"
    assert asr.engine_name("whisper", "small") == ("whisper" if installed else "mock")
    assert asr.engine_name("mock", "small") == "mock"


def test_asr_default_model_is_documented():
    assert asr.DEFAULT_MODEL in ("tiny", "base", "small")
    assert asr.DEFAULT_MODEL in (APP / "README.md").read_text(encoding="utf-8")


# ------------------------------------------------------------------ bench helpers + clips
def _bench():
    sys.path.insert(0, str(APP / "scripts"))
    import bench_small_ai
    return bench_small_ai


def test_wer_normalisation_and_distance():
    b = _bench()
    assert b.wer_norm("¿Cuándo siembro, este año?") == ["cuando", "siembro", "este", "ano"]
    assert b.wer_norm("tres hectáreas") == b.wer_norm("3 hectareas")
    assert b.edit_distance("a b c".split(), "a x c d".split()) == 2
    assert b.pct([1, 2, 3, 4], 0.5) == 2.5


def test_bench_clips_manifest_is_complete():
    man = json.loads((APP / "scripts" / "bench_clips" / "manifest.json").read_text(encoding="utf-8"))
    assert len(man["clips"]) == 12
    assert sum(1 for c in man["clips"] if c.get("noise")) == 3
    assert "SYNTHETIC" in man["_note"]
    for c in man["clips"]:
        assert set(c["slots"]) == {"crop", "cause", "area_ha", "date"}
        assert (APP / "scripts" / "bench_clips" / f"{c['id']}.ogg").exists()


def test_regex_on_clip_reference_texts():
    """The deterministic fallback must get every slot of the 12 clip texts right."""
    man = json.loads((APP / "scripts" / "bench_clips" / "manifest.json").read_text(encoding="utf-8"))
    today = date.fromisoformat(man["today"])
    f = RegexSlotFiller()
    for c in man["clips"]:
        out = f.fill(c["text"], today, man["parcel_area_ha"])
        for k, v in c["slots"].items():
            assert out[k] == v, f"{c['id']} {k}: {out[k]!r} != {v!r}"
