"""Small-AI benchmark for Helada: on-device Spanish ASR + 1-2B slot-filling LLM vs. rules.

    cd app
    uv run --extra asr python scripts/bench_small_ai.py                      # everything available
    uv run --extra asr python scripts/bench_small_ai.py --asr tiny,base,small --llm helada-qwen3-1.7b
    uv run --extra asr python scripts/bench_small_ai.py --no-llm             # ASR + regex only

Writes ../docs/small-ai-bench.md (the "Small AI" table) and scripts/bench_results.json.

What is measured
  ASR  faster-whisper (CTranslate2 int8, CPU, 4 threads, beam 1, VAD) through the app's own
       backend.asr.transcribe (ffmpeg OGG/Opus -> 16 kHz -> whisper), on 12 SYNTHETIC clips
       (scripts/bench_clips, macOS TTS voices, 3 with noise). WER after normalisation
       (lowercase, no accents/punctuation, Spanish number words -> digits). Latency is per clip,
       model already loaded. Peak RSS is measured in a fresh subprocess per model.
  Slots {crop, cause, area_ha, date} exact-match accuracy per slot on
       (a) the 15 phrases in tests/test_slots.py (only the slots each case specifies),
       (b) the 12 reference transcripts, (c) the 12 transcripts from the chosen ASR model
       (end-to-end), for: regex rules, LLM alone (JSON-schema constrained), hybrid (LLM
       proposes, rules validate; disagreement -> empty slot -> follow-up question).
       "Wrong" = a confidently wrong value (worse than empty, because empty triggers a question).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
try:
    import resource
except ImportError:      # Windows: no peak-RSS figure, the rest of the bench still runs
    resource = None
import statistics
import subprocess
import sys
import time
import unicodedata
from datetime import date as Date
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
sys.path.insert(0, str(APP))
CLIPS = HERE / "bench_clips"
OUT_MD = APP.parent / "docs" / "small-ai-bench.md"
OUT_JSON = HERE / "bench_results.json"
# The app's previous generic initial prompt, kept for the prompt ablation ("small@generic").
GENERIC_PROMPT = "Milpa, maíz, helada, granizo, hectárea, parcela, Toluca."
WHISPER_PARAMS_M = {"tiny": 39, "base": 74, "small": 244, "medium": 769, "large-v3": 1550}
SLOT_KEYS = ("crop", "cause", "area_ha", "date")


# ------------------------------------------------------------------ helpers
def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


NUMW = {"un": "1", "una": "1", "uno": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5",
        "seis": "6", "siete": "7", "ocho": "8", "nueve": "9", "diez": "10"}


def wer_norm(s: str) -> list[str]:
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s]", " ", s)
    return [NUMW.get(w, w) for w in s.split()]


def edit_distance(a: list[str], b: list[str]) -> int:
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (x != y))
    return d[-1]


def hf_model_mb(name: str) -> float | None:
    from huggingface_hub import scan_cache_dir
    try:
        for repo in scan_cache_dir().repos:
            if repo.repo_id == f"Systran/faster-whisper-{name}":
                return repo.size_on_disk / 1e6
    except Exception:
        pass
    return None


def rss_mb() -> float:
    if resource is None:
        return float("nan")
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 1e6 if sys.platform == "darwin" else r / 1e3   # macOS: bytes, Linux: KiB


# ------------------------------------------------------------------ ASR worker (own process)
def asr_worker(name: str) -> None:
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model

    from backend import asr
    label = name
    name, _, variant = name.partition("@")
    if variant == "generic":
        asr.INITIAL_PROMPT = GENERIC_PROMPT
    man = json.loads((CLIPS / "manifest.json").read_text(encoding="utf-8"))
    download_model(name)                        # no-op when cached; not part of load time
    base = rss_mb()
    t = time.perf_counter()
    asr._model_cache[name] = WhisperModel(name, device="cpu", compute_type="int8")
    load_s = time.perf_counter() - t
    asr.transcribe(CLIPS / "c01.ogg", pref="whisper", whisper_model=name)   # warm-up
    rows = []
    for c in man["clips"]:
        t = time.perf_counter()
        tr = asr.transcribe(CLIPS / f"{c['id']}.ogg", pref="whisper", whisper_model=name)
        rows.append({"id": c["id"], "text": tr.text or "", "latency_s": time.perf_counter() - t,
                     "audio_s": tr.seconds})
    print(json.dumps({"model": label, "load_s": load_s, "rss_base_mb": base, "rss_peak_mb": rss_mb(),
                      "clips": rows}, ensure_ascii=False))


def run_asr(models: list[str], man: dict) -> list[dict]:
    res = []
    for name in models:
        print(f"[asr] {name} ...", file=sys.stderr, flush=True)
        p = subprocess.run([sys.executable, __file__, "--asr-worker", name], capture_output=True, text=True,
                           cwd=APP, timeout=3600)
        if p.returncode != 0:
            print(p.stderr[-2000:], file=sys.stderr)
            continue
        r = json.loads(p.stdout.strip().splitlines()[-1])
        refs = {c["id"]: c for c in man["clips"]}
        errs = words = 0
        errs_noisy = words_noisy = 0
        for row in r["clips"]:
            ref, hyp = wer_norm(refs[row["id"]]["text"]), wer_norm(row["text"])
            e = edit_distance(ref, hyp)
            row["wer"] = e / max(len(ref), 1)
            errs += e
            words += len(ref)
            if refs[row["id"]].get("noise"):
                errs_noisy += e
                words_noisy += len(ref)
        lat = [row["latency_s"] for row in r["clips"]]
        aud = sum(row["audio_s"] or 0 for row in r["clips"])
        r.update(wer=errs / words, wer_clean=(errs - errs_noisy) / (words - words_noisy),
                 wer_noisy=errs_noisy / max(words_noisy, 1), p50_s=pct(lat, .5), p95_s=pct(lat, .95),
                 rtf=sum(lat) / aud if aud else None, size_mb=hf_model_mb(name.split("@")[0]),
                 params_m=WHISPER_PARAMS_M.get(name.split("@")[0]))
        res.append(r)
        print(f"[asr] {name}: WER {r['wer']:.1%}  p50 {r['p50_s']:.2f}s  peak RSS {r['rss_peak_mb']:.0f} MB",
              file=sys.stderr)
    return res


# ------------------------------------------------------------------ slots
def slot_cases(man: dict, transcripts: dict[str, str] | None = None):
    """Returns list of (text, today, area, expected-dict)."""
    today = Date.fromisoformat(man["today"])
    out = []
    for c in man["clips"]:
        text = transcripts[c["id"]] if transcripts else c["text"]
        out.append((text, today, man["parcel_area_ha"], c["slots"]))
    return out


def test_phrase_cases():
    sys.path.insert(0, str(APP / "tests"))
    import test_slots as ts  # the 15 phrases the regex filler is unit-tested on
    return [(p, ts.TODAY, ts.AREA, {k: v for k, v in exp.items() if k in SLOT_KEYS}) for p, exp in ts.PHRASES]


def score(fill, cases) -> dict:
    """fill(text, today, area) -> (slots, latency_s, conflicts)"""
    per = {k: [0, 0, 0] for k in SLOT_KEYS}     # correct, wrong(non-null & != exp), total
    lat, conflicts, errors = [], 0, 0
    for text, today, area, exp in cases:
        try:
            out, dt, conf = fill(text, today, area)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"   ! {e}", file=sys.stderr)
            out, dt, conf = {k: None for k in SLOT_KEYS}, float("nan"), []
        lat.append(dt)
        conflicts += len(conf)
        for k, v in exp.items():
            got = out.get(k)
            ok = (got == v) or (isinstance(got, float) and isinstance(v, (int, float)) and abs(got - v) < 1e-6)
            per[k][0] += ok
            per[k][1] += (not ok and got is not None)
            per[k][2] += 1
    c = sum(v[0] for v in per.values())
    w = sum(v[1] for v in per.values())
    n = sum(v[2] for v in per.values())
    lat = [x for x in lat if x == x]
    return {"acc": c / n, "wrong": w / n, "n_slots": n, "per_slot": {k: v[0] / v[2] for k, v in per.items() if v[2]},
            "p50_s": pct(lat, .5), "p95_s": pct(lat, .95), "conflicts": conflicts, "errors": errors}


def ollama_info(base: str, model: str) -> dict:
    import httpx
    root = base.rstrip("/").removesuffix("/v1")
    info = {"size_mb": None, "params": None, "quant": None, "ram_mb": None}
    try:
        for m in httpx.get(root + "/api/tags", timeout=5).json().get("models", []):
            if m["name"] in (model, model + ":latest"):
                info["size_mb"] = m["size"] / 1e6
                info["params"] = m["details"].get("parameter_size")
                info["quant"] = m["details"].get("quantization_level")
        for m in httpx.get(root + "/api/ps", timeout=5).json().get("models", []):
            if m["name"] in (model, model + ":latest"):
                info["ram_mb"] = m["size"] / 1e6
                info["ctx"] = m.get("context_length")
                info["gpu_mb"] = m.get("size_vram", 0) / 1e6
    except Exception:
        pass
    return info


def run_slots(llm_url: str, llm_models: list[str], sets: dict[str, list]) -> list[dict]:
    from backend.slots import HybridSlotFiller, LLMSlotFiller, RegexSlotFiller
    rows = []
    rx = RegexSlotFiller()

    def f_regex(text, today, area):
        t = time.perf_counter()
        out = rx.fill(text, today, area)
        return out, time.perf_counter() - t, []

    rows.append({"engine": "regex", "model": "rules (slots.py)", "size_mb": 0.03, "params": "-",
                 **{name: score(f_regex, cases) for name, cases in sets.items()}})
    for m in llm_models:
        llm = LLMSlotFiller(llm_url, m, timeout=120)
        hyb = HybridSlotFiller(llm)
        try:
            llm.fill("se heló la milpa", Date(2026, 10, 4), 2.5)      # load + warm-up
        except Exception as e:  # noqa: BLE001
            print(f"[llm] {m} unavailable: {e}", file=sys.stderr)
            continue

        def f_llm(text, today, area, llm=llm):
            t = time.perf_counter()
            out = llm.fill(text, today, area)
            return out, time.perf_counter() - t, []

        def f_hyb(text, today, area, hyb=hyb):
            t = time.perf_counter()
            out, _, conf = hyb.fill_ex(text, today, area)
            return out, time.perf_counter() - t, conf

        print(f"[llm] {m} ...", file=sys.stderr, flush=True)
        r_llm = {name: score(f_llm, cases) for name, cases in sets.items()}
        r_hyb = {name: score(f_hyb, cases) for name, cases in sets.items()}
        info = ollama_info(llm_url, m)
        rows.append({"engine": "llm", "model": m, **info, **r_llm})
        rows.append({"engine": "hybrid", "model": m + " + rules", **info, **r_hyb})
    return rows


# ------------------------------------------------------------------ report
DECISIONS = """
1. **ASR default: faster-whisper `small` int8 on CPU** (`backend/asr.py` `DEFAULT_MODEL`). It is the smallest model
   that transcribes these notes almost verbatim; `base` loses farm words ("quemó", "haba", "hectáreas") and
   `tiny` is unusable. `medium` is 3x slower and 2.5x the RAM for no gain here.
2. **Domain vocabulary prompt** (`asr.INITIAL_PROMPT`: "se me heló", "antier", "escarcha", "haba"...) cut `small`'s
   WER from the generic-prompt run (`small@generic`) to the `small` row. Caveat: the prompt was written while looking
   at these 12 clips, so part of that gain is fitted to them; it is a list of plain farm words, not instructions.
3. **Slot filling default: the deterministic Spanish rules** (0 MB, <1 ms, 0 confidently wrong values here). Every
   1-2B LLM we tried was slower and less accurate, mostly on arithmetic ("hectárea y media") and calendar
   ("antier", "el lunes") even with a calendar lookup table in the prompt, and it answers "helada / maíz" to
   plain questions ("¿cuándo siembro?").
4. **The LLM stays optional, as a recall helper behind the rules** (`HybridSlotFiller`, used when `HELADA_LLM` is
   set): it may only add `crop`/`cause` (closed enums) the rules missed, in a message the rules already see as a
   damage report or when that slot was just asked; a crop/cause disagreement empties the slot so the bot asks;
   `area_ha`/`date` are always the rules'. If the LLM is down it silently becomes the rules. Best candidate if
   used: `qwen3:1.7b` (1.4 GB, ~1.4 s on 4 CPU threads, thinking disabled via `reasoning_effort: none`).
5. **On-device footprint (default config)**: whisper `small` 486 MB on disk, ~1.3 GB peak RAM, plus the rules
   (~30 KB). With the optional LLM: +1.4 GB disk, +1.6 GB RAM (2k context; Ollama's 40k default uses ~6 GB).
"""


def fmt(x, spec=".2f", dash="–"):
    return dash if x is None or x != x else format(x, spec)


def report(asr_rows, slot_rows, chosen, set_names, args) -> str:
    L = []
    mach = f"{platform.machine()} {platform.processor() or ''}".strip()
    try:
        cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
        mem = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 2**30
        mach = f"{cpu}, {mem:.0f} GB RAM"
    except Exception:
        pass
    L += ["# Small-AI benchmark (on-device ASR + slot filling)", "",
          f"Generated by `app/scripts/bench_small_ai.py` on {time.strftime('%Y-%m-%d')}. Machine: {mach}, macOS, "
          "**CPU only** (whisper: CTranslate2 int8, 4 threads; LLM: Ollama with `num_gpu 0`, 4 threads, 2k context).",
          "", "> **Synthetic audio.** The 12 clips are macOS TTS voices (Paulina, Eddy, Reed, Grandpa, Rocko, Flo; "
          "es-MX) encoded as WhatsApp-style OGG/Opus 16 kHz, 3 with pink/brown noise mixed in (`scripts/make_test_clips.py`). "
          "They are clean, well-articulated speech: real farmer voice notes (accent, wind, cheap mics, Mazahua code-switching) "
          "will be worse. Treat WER here as a lower bound and re-run on real notes before quoting field accuracy.",
          "", "> Latency is on a fast laptop CPU (Apple M3 Max, 4 threads). A Raspberry Pi 5 / low-end laptop is expected to be "
          "several times slower [V: not measured].", ""]
    # ---- summary table
    L += ["## Summary table", "", "| Component | Model | Size on disk (MB) | Params | Peak RAM (MB) | Latency p50 / p95 (s) | Accuracy |",
          "|---|---|---:|---:|---:|---:|---|"]
    for r in asr_rows:
        tag = " **(default)**" if r["model"] == chosen else ""
        L.append(f"| ASR (Spanish) | faster-whisper {r['model']} int8{tag} | {fmt(r['size_mb'], '.0f')} | {r['params_m']}M | "
                 f"{fmt(r['rss_peak_mb'], '.0f')} | {fmt(r['p50_s'])} / {fmt(r['p95_s'])} | "
                 f"WER {r['wer']:.1%} (clean {r['wer_clean']:.1%}, noisy {r['wer_noisy']:.1%}) |")
    short = {"test_phrases": "15 phrases", "clip_refs": "12 clip texts", "asr_e2e": "12 ASR transcripts"}
    for r in slot_rows:
        s = r["asr_e2e"] if "asr_e2e" in r else r[set_names[0]]
        acc = " · ".join(f"{short[k]} {r[k]['acc']:.0%} / {r[k]['wrong']:.0%}" for k in [*set_names, "asr_e2e"] if k in r)
        label = {"regex": "Slot filling", "llm": "Slot filling (LLM alone)", "hybrid": "Slot filling (hybrid)"}[r["engine"]]
        ram = r.get("ram_mb")
        L.append(f"| {label} | {r['model']}{' ' + r['quant'] if r.get('quant') else ''} | {fmt(r.get('size_mb'), '.0f') if r['engine'] != 'regex' else '<0.1'} | "
                 f"{r.get('params') or '-'} | {fmt(ram, '.0f') if ram else ('~0' if r['engine'] == 'regex' else '–')} | "
                 f"{fmt(s['p50_s'], '.3f' if r['engine'] == 'regex' else '.2f')} / {fmt(s['p95_s'], '.3f' if r['engine'] == 'regex' else '.2f')} | {acc} |")
    L += ["", "Slot accuracy cells are *slots right / confidently wrong*. A wrong value is worse than an empty one: an empty "
          "slot makes the bot ask a follow-up question, a wrong one ends up in the loss packet."]
    L += ["", "## Decisions", "", DECISIONS.strip(), ""]
    # ---- ASR detail
    L += ["", "## ASR detail", "", "| Model | Load (s) | RTF | WER | WER clean | WER noisy | p50 (s) | p95 (s) | Peak RSS (MB) |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in asr_rows:
        L.append(f"| {r['model']} | {r['load_s']:.1f} | {fmt(r['rtf'])} | {r['wer']:.1%} | {r['wer_clean']:.1%} | "
                 f"{r['wer_noisy']:.1%} | {r['p50_s']:.2f} | {r['p95_s']:.2f} | {r['rss_peak_mb']:.0f} |")
    if asr_rows:
        man = json.loads((CLIPS / "manifest.json").read_text(encoding="utf-8"))
        L += ["", "Per-clip transcripts:", "", "| Clip | Reference | " + " | ".join(r["model"] for r in asr_rows) + " |",
              "|---|---|" + "---|" * len(asr_rows)]
        by = {r["model"]: {c["id"]: c for c in r["clips"]} for r in asr_rows}
        for c in man["clips"]:
            cells = [f"{by[r['model']][c['id']]['text']} ({by[r['model']][c['id']]['wer']:.0%})" for r in asr_rows]
            L.append(f"| {c['id']}{' 🔊' if c.get('noise') else ''} | {c['text']} | " + " | ".join(cells) + " |")
        L += ["", "🔊 = noise mixed in."]
    # ---- slots detail
    if slot_rows:
        L += ["", "## Slot filling detail", "",
              "Accuracy = exact match per slot (crop, cause, area_ha, date; `null` must stay `null` on the 12-clip sets). "
              "Wrong = a confidently wrong value (an empty slot is safer: the bot asks a follow-up).", "",
              "| Engine | Model | Eval set | Acc | Wrong | crop | cause | area_ha | date | Conflicts→follow-up | p50 (s) | p95 (s) |",
              "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for r in slot_rows:
            for name in [*set_names, "asr_e2e"]:
                s = r.get(name)
                if not s:
                    continue
                ps = s["per_slot"]
                L.append(f"| {r['engine']} | {r['model']} | {name} | {s['acc']:.0%} | {s['wrong']:.0%} | "
                         + " | ".join(fmt(ps.get(k), '.0%') for k in SLOT_KEYS)
                         + f" | {s['conflicts']} | {fmt(s['p50_s'], '.3f')} | {fmt(s['p95_s'], '.3f')} |")
        L += ["", "Eval sets: `test_phrases` = the 15 phrases in `app/tests/test_slots.py` (the regex was written against "
              "these, so regex accuracy on them is optimistic); `clip_refs` = the 12 clip reference texts; "
              f"`asr_e2e` = the 12 transcripts produced by faster-whisper **{chosen}**."]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asr-worker")
    ap.add_argument("--asr", default="tiny,base,small,small@generic,medium",
                    help="'@generic' = old generic whisper prompt instead of the domain vocabulary prompt")
    ap.add_argument("--asr-default", default="small")
    ap.add_argument("--llm", default="helada-qwen3-1.7b,helada-gemma3-1b,helada-qwen3-0.6b")
    ap.add_argument("--llm-url", default=os.environ.get("HELADA_LLM") or "http://localhost:11434/v1")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--no-asr", action="store_true", help="reuse ASR results from bench_results.json")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    if a.asr_worker:
        asr_worker(a.asr_worker)
        return 0
    man = json.loads((CLIPS / "manifest.json").read_text(encoding="utf-8"))
    if a.no_asr and OUT_JSON.exists():
        asr_rows = json.loads(OUT_JSON.read_text(encoding="utf-8"))["asr"]
    else:
        asr_rows = run_asr([m for m in a.asr.split(",") if m], man)
    chosen = a.asr_default
    sets = {"test_phrases": test_phrase_cases(), "clip_refs": slot_cases(man)}
    e2e = next((r for r in asr_rows if r["model"] == chosen), None)
    if e2e:
        sets["asr_e2e"] = slot_cases(man, {c["id"]: c["text"] for c in e2e["clips"]})
    slot_rows = run_slots(a.llm_url, [] if a.no_llm else [m for m in a.llm.split(",") if m], sets)
    md = report(asr_rows, slot_rows, chosen, [k for k in sets if k != "asr_e2e"], a)
    print(md)
    if not a.no_write:
        OUT_MD.write_text(md, encoding="utf-8")
        OUT_JSON.write_text(json.dumps({"asr": asr_rows, "slots": slot_rows}, ensure_ascii=False, indent=1, default=str),
                            encoding="utf-8")
        print(f"wrote {OUT_MD} and {OUT_JSON}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
