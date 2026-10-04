# Responsible AI, data and safety

Each point says **what the prototype does today** (checked in the code) and **what is still missing**. Nothing here is a promise about a deployment we have not run.

## 1. What data is kept, where, and who reads it

| Data | Where it lives today | Who can read it | Missing |
|---|---|---|---|
| Parcel roster: name, phone, municipality, coordinates, area, crop | `app/backend/data/roster.json` and the SQLite database on the box (`HELADA_DATA_DIR`, default `app/var`). **Demo names and phones are fictional.** | Anyone who can open the dashboard. **There is no login today** (`app/README.md`, Limitations). | Officer login with roles, encryption at rest, a written retention period |
| Messages in and out, damage-report slots, alerts | SQLite on the box (`messages`, `reports`, `alerts` tables) | Same as above | A delete-on-request command (see §2) |
| Voice notes and photos | `media/` folder on the box. Only a SHA-256 of each file goes into the audit log. | Same as above | Voice notes are **kept** today. Deleting them after transcription is planned (`docs/tradeoffs-preconditions.md` §6), not built. |
| Loss packets (PDF) | `packets/` folder on the box. Sent to the farmer in the chat. | The farmer, and whoever runs the box. The farmer decides whether to take it to the Delegación. | — |
| Audit log | SQLite, append-only. Triggers block UPDATE and DELETE. Each entry is chained by SHA-256. | Same as the dashboard | It stores phone numbers. Deletion on request needs a redaction design that keeps the chain valid (§2). |
| On the phone page (`/movil`) | Browser storage on the phone: the saved forecast, the parcel chosen, and readings or reports not yet sent | Anyone who unlocks the phone | **The site pack (`static/movil/data/pack.json`) ships the whole roster**, with names, phones and coordinates for every parcel, to every phone. That is acceptable only because the demo data is fictional. Before real farmers: one pack per farmer, holding only their own parcel. Also missing: a "borrar mis datos" button. |

**What leaves the box:**
- WhatsApp messages, through Twilio and Meta under their terms;
- parcel coordinates, without names, sent to Open-Meteo to fetch the forecast;
- map tiles requested from OpenStreetMap by the dashboard.

Nothing else. The demo runs without Twilio (built-in phone simulator).

**If the phone is lost or shared:**
- **Today:** the page holds the parcel's forecast and any unsent reading or report, in plain browser storage, protected only by the phone's own lock. No ID number, CURP or program folio is ever asked for or stored. The packet lists the documents the farmer must bring, and Helada never sees them (`docs/pasacme.md` §3B).
- **Shared phones are common, not an edge case** (15.4% of people aged 6+ in Mexico do not use a mobile phone on their own; `docs/problem-evidence.md` row 6). The alert goes to the phone registered for the parcel, which can belong to a relative or the comisariado. The packet is always in the farmer's name, not the phone owner's.
- **Missing:** a PIN on the page, and alert texts reviewed so the lock-screen preview shows no personal detail.

## 2. Consent and how to leave

| | Today | Missing |
|---|---|---|
| Joining | The roster is loaded by the officer. There is **no consent step in the chat yet**. | A consent script read by the promotora or comisariado at enrollment, in Spanish (and Mazahua through an interpreter), recorded as an audit event |
| Stopping alerts | **Built.** "BAJA", "STOP", "ya no me manden mensajes" and similar wordings set the opt-out. It cancels alerts already queued, and it is logged (`contact.optout`). Every send re-checks consent. "ALTA" turns alerts back on. A complaint ("ya no me llegó la alerta") is not taken as an opt-out (`app/backend/intents.py`). | — |
| Deleting data | **Not built.** Opting out stops messages; it does not delete records. | A delete command that removes roster fields, messages and media, and writes a redaction event to the audit log. This needs personal data kept outside the hash chain, so the chain stays verifiable. |
| Legal basis | Not settled. LFPDPPP (private operator) or the Edomex law for public bodies, if the Secretaría runs it (not verified) | A privacy notice reviewed by a lawyer before any real farmer is enrolled |

## 3. Bias: who gets a worse answer

| Who | Why | What we do today | Missing |
|---|---|---|---|
| **Parcels far from an SMN station** (most parcels) | The model's skill is measured near stations. Away from one, it removes the warm bias (2.83 → 2.32 °C) but does not detect frost better (32.1% vs 32.4%). | The parcel is labelled "terrain-transfer". It gets a wider band and a softer **WATCH** message when the cold end of the band reaches 0 °C. On the phone, "No estoy seguro" appears when the band reaches 0 °C (§4). | Loggers on about 5 parcels per ejido for one season (`docs/tradeoffs-preconditions.md` §3.7) |
| **High parcels (above about 2,800 m)** | Few stations up there | Wider bands, shown, not hidden | Same as above |
| **People with no phone of their own; women farmers** | Shared or absent phones; PASACME gives priority to women heads of household and indigenous communities (§9.3.2) | A named contact phone per parcel; the packet is in the farmer's name | Outreach by promotoras and comisariado (a process, not software). An SMS fallback exists in the code but is untested on a live account. |
| **Farmers who read slowly** | 72% of producers have primary school or less (ENA 2019) | The alert is a Spanish voice note under 20 s, with text as a fallback | Testing with real farmers |
| **Rural accents** | Speech recognition was measured only on **synthetic** voices | Unclear slots are never guessed: the bot asks again (at most twice per slot) | A benchmark on real voice notes, with consent |

## 4. Who makes the final call at each step

| Step | What the AI does | Who decides |
|---|---|---|
| Frost alert | The model gives a probability and a range. A fixed, deterministic rule (P ≥ 0.30, at most 2 per week, 18:00–20:00) decides whether a message goes out. | **The farmer** decides whether to cover a crop. The extension office sets the threshold, a config value chosen by people. An officer override of the send window is logged. |
| Not enough data | The phone page returns **"No estoy seguro. Pregunte a su técnico antes de decidir."** in these cases: no saved forecast; a forecast more than 3 days old (or 2–3 days old on a cold-looking night); two or more weather inputs missing; or a parcel with no station whose cold end reaches 0 °C (`judge()` in `static/movil/`) | **The technician**, whose contact is shown on the card |
| Damage report | Rules read the crop, area, cause and date from the voice note. Anything unclear gets a follow-up question; an optional LLM may only suggest crop or cause, never area or date. | **The farmer** answers the questions. A thermometer reading counts only after the farmer confirms it with "sí". |
| Loss packet | Fills facts and a checklist. It says only "Documentos completos" or "Falta: X". A code guard raises an error if any text reads like an eligibility decision. | **The Secretaría del Campo**, through on-site inspection (Anexo 3). Footer: "Lo decide la Secretaría del Campo." |
| Advice (crop actions, planting dates) | Comes from a fixed table, cited to INIFAP, FAO and other sources | **An agronomist**, who must sign it. Until then the alert and the planting card show "Borrador sin firma agronómica". |

## 5. Language: Spanish today; Mazahua honestly

**Today:**
- **Spanish works by voice and text.** Alerts are Spanish voice notes (TTS). Replies can be voice notes, transcribed on the box by Whisper `small`: 2.7% word error on 12 synthetic clips, so real speech will be worse.
- **Mazahua: not supported yet.** A parcel can be tagged `lang: maz`, but nothing uses the tag (`app/README.md`, Limitations). **There is no Mazahua keyword spotting in the code.** Alerts to Mazahua-speaking farmers go out in Spanish.

**What it would take, in order of safety:**
1. **Recorded phrases, no AI.** Native speakers record the fixed set of alert and follow-up phrases (about 20 short audios), and the bot plays them. A fixed list of answers is checkable. A partner could be the Universidad Intercultural del Estado de México, whose main campus is in San Felipe del Progreso, a Mazahua area [V: contact not made].
2. **Keyword spotting for about 10 words** (helada, granizo, se quemó, hectárea, sí/no, baja). Start from Meta MMS, which lists a Mazahua (`maz`) speech adapter and a TTS model (`mms-tts-maz`, licensed CC BY-NC 4.0); quality on Edomex Mazahua is unmeasured. Test with native speakers, and report the number of speakers and the hit rate before claiming anything.
3. **Fall back to a person.** When the keyword spotter is unsure, the reply is "No le entendí, le va a llamar su técnico". It never guesses.
4. **Give back.** With consent, the recordings go to Mozilla Common Voice, so the next team does not start from zero.

Otomí of Temoaya, the other large indigenous language of the valley, has no speech recognition or speech synthesis in MMS, only language identification (`docs/tradeoffs-preconditions.md` §5).
