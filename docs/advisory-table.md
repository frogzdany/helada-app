# Frost advisory table (rainfed maize, potato, oats, flowers)

Status: DRAFT. **Precondition: an agronomist (INIFAP Valle de Toluca or a Tec Toluca / UAEMex agronomy faculty member) must review and sign this table before any farmer sees it.** The app adds "Borrador sin firma agronómica" to its messages until `meta.signed_by` is filled in the YAML. Nothing here is a guarantee of crop safety.

Verified 2026-09-29. Items with [V] are not confirmed by a primary source we could open.

## 1. Key message: what a farmer can do, ranked by leverage

1. **Choose planting date and cycle length from the parcel's own frost climatology** (main lever for maize). Helada computes last-spring-frost and first-autumn-frost dates from the nearest station corrected to the parcel (`FrostClimatology`), then checks days-to-maturity of the chosen variety against the frost-free window. The Toluca Rural Development District has more than 100 frost days a year and 2019 losses hit 3,511 ha, worst in Almoloya de Juárez, Zinacantepec, Tenango del Valle; the flowering and grain-fill stages (August frosts) were most damaging (source S1).
2. **Protect what can be protected** on the night: potato, vegetables, oats seedlings, flower beds and nurseries (covers, smoke/irrigation only where feasible).
3. **Document losses within the program's 10-day window** (see pasacme.md).

For maize at flowering or grain fill there is little to do the same night. The alert text says so and makes no false promise.

## 2. Sources

- S1: Jasso-Miranda et al., Rev. Mex. Cienc. Agríc. 2022 (UAEMéx article in the INIFAP journal), "Pérdida de superficies cultivadas de maíz de temporal por efecto de heladas en el valle de Toluca". https://www.scielo.org.mx/scielo.php?script=sci_arttext&pid=S2007-09342022000200207 . Facts used: >100 frost days/yr; 3,511 ha affected in 2019; flowering and grain fill most vulnerable; damaging frosts in May, August, October 2019; correlation of losses with altitude (r=0.516). A summary of the page gives the damage threshold as "4 °C" for temperature at the station, which is a screening threshold, not lethal temperature. This figure was not checked against the paper's methods section [V].
- S2: FAO, Protección contra las heladas, ch. 4, Table 4.5 "Intervalo de temperaturas críticas (°C) que dañan a los cultivos forrajeros": maize germination -2/-3, flowering -1/-2, fruiting -2/-3; oats -8/-9, -1/-2, -2/-4; potato -2/-3, -1/-2, -1/-2. https://www.fao.org/4/y7223s/y7223s05.pdf . These are general international values, not Toluca-specific; they are the defaults pending INIFAP review. The offset between air temperature in a shelter and leaf temperature is up to several degrees on radiative nights; the exact value in the FAO text was not confirmed [V].
- S3: Tolerancia de papas nativas a heladas (Peru, SciELO): most cultivated potato varieties are sensitive below -2.5 °C, with visible leaf damage and yield/quality loss. http://www.scielo.org.pe/scielo.php?script=sci_arttext&pid=S2077-99172018000300010 . A search summary of INIFAP-linked literature says emergence and start of stolon formation were the most susceptible stages, with 30-50% yield loss when aerial damage exceeded 50%. The original is a Ciencias Agrícolas / ResearchGate paper, "Efectos de las heladas en el cultivo de papa", which was not opened [V].
- S4: INIFAP early highland maize varieties as frost-escape (Coztli Puma, Kuautli Puma, Mistli UNAM 2021; V-54 A, HV 60 A). V-54 A: 135 days to physiological maturity, 2,100-2,650 masl, plant late May and all June, ~6.7 t/ha 2006-2009. https://www.scielo.org.mx/scielo.php?script=sci_arttext&pid=S2007-09342010000500005 ; Nueva Variedad Rev. Fitotec. Mex. 44(1) 2021 https://revfitotecnia.mx/index.php/RFM/article/download/857/1095/1587 ; HV 60 A: 137 days at 2,250 masl, not checked against the paper [V].
- S5: Oat varieties for rainfed Mexico: Zafiro (INIFAP), recommended for early June and late July plantings in the State of Mexico among others; frost tolerance is a listed trait. https://www.scielo.org.mx/scielo.php?script=sci_arttext_plus&pid=S0187-73802024000300325&lng=pt&tlng=es&nrm=iso . Chihuahua and Cuauhtémoc are rust-susceptible.
- S6: Planting window guidance for altitude zones: May 15 - June 20 with frost risk later, from a commercial blog (bioproi.com), a low-quality source [V]. The intended replacement is the INIFAP Agenda Técnica for Estado de México, which was not checked; the state agendas are at https://vun.inifap.gob.mx/VUN_MEDIA/BibliotecaWeb/_media/_agendas/ .
- S7: SMN station daily Tmin (climatology input): see data-sources.md.
- CIMMYT: no CIMMYT document with critical temperatures per stage for highland maize was found, so CIMMYT is not cited for thresholds. Generic literature says chilling around generative stages causes male sterility and a longer anthesis-silking interval. CIMMYT's tropical-highland gene pools show early low-temperature vigor (Euphytica, Springer; no URL recorded here) [V].
- SADER/SMN: SMN issues "aviso de heladas" for Estado de México in the cold season; the exact SMN product URL is not recorded here [V]. SADER/SIAP supply crop calendars for Edomex [V].
- Flowers: no primary source was found for Villa Guerrero open-field or greenhouse thresholds. Flower thresholds are not shown beyond the FAO category table (S2 Table 4.2 for annual flowers) until an agronomist supplies them [V].

## 3. Table (defaults, pending agronomist review)

Thresholds are minimum air temperature in a shelter (what the model outputs as `tmin_c`), with the model's uncertainty. "Alert" means the alert rule in `docs/interfaces.md` fires (ALERT when `p_frost >= 0.30`) and the crop is in that stage. A softer WATCH text, which is not an alert, goes to terrain-transfer parcels when `tmin_lo_c <= 0` and `p_frost < 0.30` (also to logger-anchored parcels with less than a full season of logger nights, `app/backend/alerts.py`).

### Maize, rainfed (sowing May-June, spring-summer)

| Stage | Typical timing in Toluca valley [V] | Damage risk (FAO S2) | What Helada tells the farmer |
|---|---|---|---|
| Sowing to emergence (VE) | May-Jun | Germination: -2 to -3 °C (S2) | Little risk: seed underground; if emerged plants burn, growing point still below ground until ~V5-V6 and often regrows; not confirmed by an INIFAP or CIMMYT source [V] |
| Seedling V2-V6 | Jun-Jul | as above | Low urgency. Do not replant automatically; wait 3-5 days and check the growing point (cut stem base: white/firm = alive) [V] |
| Vegetative V8-VT | Jul-Aug | between | Moderate. Document losses if leaves are killed |
| Flowering (tasseling/silking) | Aug | -1 to -2 °C (S2); S1: most damaging | High risk, no practical protection. Alert = "document and report within 10 days" |
| Grain fill / milk-dough | Aug-Sep | -2 to -3 °C (S2) | High risk for yield and quality; document losses. Nothing to do tonight |
| Physiological maturity (black layer) | Sep-Oct | none after black layer [V] | Frost after black layer causes little yield loss; harvest as planned |

### Planting-date and variety choice (the real decision)

| Situation | Advice text (Spanish, pending sign-off) | Basis |
|---|---|---|
| Parcel's last spring frost p90 is late (hollow, > 2,700 m) | "En su parcela las heladas suelen terminar tarde. Siembre después de [fecha p90]." | Helada climatology |
| Days from planned planting to first autumn frost p10 < variety cycle + margin | "Con esta fecha la variedad no alcanza a madurar antes de la primera helada. Elija una variedad más precoz o adelante la siembra." | S4 cycle lengths |
| Delayed rains (planting after June 15) | Use early-cycle varieties of 135-140 days, e.g. V-54 A, HV 60 A, Coztli Puma, Kuautli Puma, Mistli (if seed available) | S4 |
| Native landrace, cycle > 160 d [V] | Warn that the cycle may not fit above 2,600 m | to be checked with local varieties |

Margin and cycle values are inputs the agronomist confirms. Seed availability and price are not verified. The Spanish texts in this table are drafts; the texts the advisor sends are the templates under `planting.maiz_temporal.templates` in the YAML.

**Implemented** (`app/backend/planting.py`, YAML `planting` in `app/backend/advisory.yaml`): cycle table at the source altitude, scaled +4%/100 m (from H-52: tasseling 84 d at 2,250 m vs 96 d above 2,500 m; Venado H74 suggests up to +11%/100 m, so high-parcel cycles may be underestimated [V]).

| Cycle | Examples | Flowering / physiological maturity (d) | Source |
|---|---|---|---|
| corto | V-54 A, V-55 A, HV 60 A | 70 / 136 at 2,250 m | S4; HV 60 A 137 d; Espinosa-Calderón et al. 2013 Agron. Mesoam. 24:93 (flowering 69-71 d) |
| intermedio | H-51 AE, H-50, H-52 | 84 / 155 at 2,250 m | H-51 AE Rev. Fitotec. Mex. 35(4) 2012 (83/85 d, 150 d); Tadeo-Robledo et al. 2015 RMCA 6(1) (H-50/H-52 and Ixtlahuaca/Atlacomulco criollos, 160-162 d at 2,240 m) |
| largo | criollos (Cónico, Cacahuacintle, Chalqueño) | 103 / 190 at 2,640 m | Arellano et al. 2010 Rev. Fitotec. Mex. 33(4) (female flowering 99-106 d, Calimaya/Metepec); maturity 190 d is an estimate [V] |

Sowing moments: ICAMEX recommends 20 Mar-20 Apr (punta de riego/temporal) for Valles Altos; V-54 A / HV 60 A for late May-June (S4). INIFAP (folleto 4170): October early frosts can cut the maize cycle to 140 days in Valles Altos. The margin is 0 days because the risk-averse date is already the 1-in-10-year early frost; an earlier draft of this table proposed 10 days, and the agronomist has not confirmed the value [V]. Full citations: YAML `sources_planting`, keys S7-S14.

### Potato (rainfed and irrigated, Nevado de Toluca slopes)

| Stage | Damage risk | Advice |
|---|---|---|
| Emergence to early stolon formation | Highly susceptible (search summary of INIFAP-cited work [V]); cultivated varieties sensitive below -2.5 °C (S3); FAO -2/-3 germination | Alert = cover rows or delay emergence; earthing up (aporque) burying shoots gives some protection [V] |
| Tuberization | -1/-2 at flowering (S2) | Irrigate the evening before if available (moist soil holds heat; no FAO passage on irrigation is cited [V]); document |
| Bulking to senescence | -1/-2 (S2) | Late frosts near senescence cost little; document if > 50% foliage lost |

### Oats (forage/grain, rainfed)

| Stage | Damage risk | Advice |
|---|---|---|
| Germination/seedling | -8 to -9 (S2) | No alert needed except P(Tmin) < -5 |
| Flowering | -1/-2 (S2) | Alert = document; no protection possible |
| Grain fill | -2/-4 (S2) | Document; oat for forage can be cut early if frosted [V] |
| Timing | Zafiro early June or late July planting (S5) | Late July plantings suit forage before winter; check frost date |

### Flowers (Villa Guerrero corridor; mostly greenhouse and open field)

No verified thresholds. The only advice text is "Riesgo de helada: proteja las camas y viveros al aire libre". The FAO annual-flower tolerance categories (S2 Table 4.2) are the sole numeric reference. The YAML marks the crop `no_verified_threshold: true` and names its stage "sin umbral verificado". Greenhouse crops are not covered by parcel frost alerts.

## 4. Season calendar (planting to harvest, Toluca valley)

Pending review; intended as UI copy once reviewed. May-June sowing (guidance window May 15 - June 20 [V S6]); flowering Aug; grain fill Aug-Sep; frost risk windows: May (early), Aug, Oct (2019 observed damaging frosts in May, August and October; S1).

## 5. YAML (machine-readable)

The table the app loads is `app/backend/advisory.yaml`. That file is the reference; this document keeps no copy of it. Its structure:

- `meta`: version (`0.1-draft`), `signed_by` and `signed_at` (empty until an agronomist signs: name, institution, date), `unsigned_label`, `disclaimer_es`.
- `contact`: the person "pregunte a su técnico" points to. Fictional in the demo.
- `sources`: S1-S5, plus `PASACME` (10-day notice, see pasacme.md) and `TODO_AGRONOMO` (no primary source yet [V]). `TODO_AGRONOMO` marks the maize stem check, the potato evening irrigation and the haba advice.
- `templates`: the Spanish message texts (alert, watch, "no estoy seguro", forecast reply, loss report, menu and program replies).
- `crops`: `maiz_temporal`, `papa`, `avena`, `haba`, `flores_campo_abierto` and `_default`. Each crop has stages chosen by calendar month, and each stage has actions with `text`, `sms` and `source`. Maize has five month windows; the other crops have one all-year stage. The month-to-stage mapping is crude and not confirmed by altitude band [V].
- `planting.maiz_temporal`: inputs of the planting advisor: `altitude_pct_per_100m: 4.0`, `margin_days: 0`, `flowering_window_days: 10` [V], `emergence_days: 12` [V], reference sowing dates, the three cycles of section 3, message templates.
- `sources_planting`: S7-S14, the citations for the cycle table.

The YAML holds no critical temperatures. The values in section 3 are reference for the reviewer. The alert rule uses only the model output and is the same for every crop; its constants (0.30, at most 2 alerts per parcel per 7 days, send window 18:00-20:00) are in `app/backend/alerts.py`. S6 and S7 of this document are not YAML keys: the YAML does not use S6, and its own S7 is the HV 60 A citation.

## 6. Open items for the agronomist
1. Confirm defaults and replace FAO generic values with INIFAP Toluca guidance.
2. Local variety list, seed availability, cycle lengths (native landraces).
3. Flower thresholds for Villa Guerrero.
4. Confirm stage timing by altitude band.
5. Approve Spanish text; run a 5-minute comprehension test with two farmers (audio).
