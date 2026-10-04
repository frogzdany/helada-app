# Evidence that the problem is real

Every figure below has a source, a year, a country and a link. Each link was opened on 2026-10-03. The **Type** column says what kind of number it is:

- **Measured**: an official survey or an observation.
- **Estimate**: modelled, or derived from satellite data.
- **Press**: reported by the press, not by an official publication.
- **Ours**: measured by the team in this repository.
- **National**: a Mexico-wide figure that we apply to the Toluca valley. Rural Edomex may differ.

## Main table

| # | Figure | Source | Year | Place | Type | Link |
|---|---|---|---|---|---|---|
| 1 | **3,511.6 ha** of rainfed maize lost to frost in one season, 2.77% of the 126,500.7 ha of maize mapped in July | Jasso-Miranda, Soria-Ruiz & Antonio-Némiga (UAEMéx and INIFAP), *Rev. Mex. Cienc. Agríc.* 13(2) | 2019 season (published 2022) | Toluca Rural Development District (DDR), Estado de México | Estimate (GIS and remote sensing) | [doi:10.29312/remexca.v13i2.2587](https://doi.org/10.29312/remexca.v13i2.2587) |
| 2 | **More than 100 frost days a year** in the DDR Toluca; **132,784.9 ha** of maize grown rainfed there. The frosts hit at **flowering and grain fill**. Most affected: Almoloya de Juárez (32.2%), Zinacantepec (17.6%), Tenango del Valle (9.4%) | Same article | 2019 season | DDR Toluca | Measured (area statistics), cited in the article | [same DOI](https://doi.org/10.29312/remexca.v13i2.2587) |
| 3 | The free regional forecast runs **+1.5 °C warm** at SMN stations, and it catches only **24%** of frost nights at a 5% false-alarm rate. Forwarding it with a "frost if ≤ 0 °C" rule catches **2%** | Our backtest: 39 SMN stations, 5,855 station-nights, 503 of them frost | 2025-26 frost season | Toluca, Ixtlahuaca and Atlacomulco valleys | Ours | [`backtest.json`](../model/src/helada_model/artifacts/backtest.json) (`known_station.vs_open_meteo_default`), [`model/README.md`](../model/README.md) |
| 4 | A farmer who suffers a loss must **notify the Delegación within 10 calendar days**, the damage must be **at least 60%** of the area, and support is **up to MX$4,000 per ha, up to 3 ha**. The loss is verified by **on-site inspection** | Lineamientos PASACME, *Gaceta del Gobierno* No. 114 | 2024 (in force; press reports announce 2026 changes, which are not published) | Estado de México | Measured (official rule) | [Gaceta PDF](https://secampo.edomex.gob.mx/sites/secampo.edomex.gob.mx/files/files/Acerca%20de%20la%20Secretaria/Reglas%20de%20Operacion/Lineamientos_PASACME_2024.pdf) |
| 5 | PASACME has paid **MX$40 million** to **6,720 producers** for **10,900 damaged ha** since 2024 | La Noticia es, 27 Apr 2026, quoting the state government | 2024 to Q1 2026 | Estado de México | Press | [article](https://www.lanoticiaes.com/post/gem-mejora-programa-de-atenci%C3%B3n-a-siniestros-agroclim%C3%A1ticos-en-el-campo-mexiquense) |
| 6 | **84.6%** of people aged 6+ use a mobile phone on their own and always have one at hand, so **15.4% do not**. The survey measures use, not ownership, and it includes children. **97.0%** of phone users use a smartphone | INEGI, ENDUTIH 2025 | 2025 (published 16 Jun 2026) | Mexico | Measured, National | [ENDUTIH 2025 report](https://www.inegi.org.mx/contenidos/saladeprensa/boletines/2026/endutih/ENDUTIH_25_RR.pdf) |
| 7 | **75.2%** of rural people aged 6+ use the internet, against **88.9%** in cities: a gap of 13.7 points | INEGI, ENDUTIH 2025 | 2025 | Mexico, rural vs urban | Measured, National | [ENDUTIH 2025 report](https://www.inegi.org.mx/contenidos/saladeprensa/boletines/2026/endutih/ENDUTIH_25_RR.pdf) |
| 8 | Phone use by gender is almost equal (women **84.5%**, men **84.8%**). Mobile internet use is women **83%**, men **85%**, which GSMA reads as equal use | INEGI, ENDUTIH 2025; GSMA, *Mobile Gender Gap Report 2025* (GSMA Consumer Survey 2024, adults 18+) | 2025; 2024 | Mexico | Measured, National | [ENDUTIH 2025](https://www.inegi.org.mx/contenidos/saladeprensa/boletines/2026/endutih/ENDUTIH_25_RR.pdf), [GSMA 2025 PDF](https://www.gsma.com/wp-content/uploads/2025/12/The-Mobile-Gender-Gap-Report-2025.pdf) |
| 9 | **17%** of the producers who run a farm are women. **14.8%** of producers have no schooling, and **57.1%** have primary school only | INEGI, Encuesta Nacional Agropecuaria 2019 | 2019 | Mexico | Measured, National | [ENA 2019 results](https://inegi.org.mx/contenidos/programas/ena/2019/doc/irg_ena2019.pdf) |

## What the numbers say, and what they don't

- **Frost losses are real and recurrent in this valley** (rows 1 and 2). The 3,511 ha is one season. It is the difference between two satellite classifications of the maize area, before and after the frosts, and the article reports no field validation. It counts area that left the crop map, not yield lost on parcels that survived. The article counts a frost as a minimum below 4 °C, a wider definition than our 0 °C.
- **The free forecast misses most frost nights** (row 3). This is our own measurement. It holds at SMN stations, not at every parcel (see the claim rule in `docs/why-ai.md`).
- **The program has a 10-day clock and an inspection** (row 4). This is why the loss packet matters: a late or incomplete notice can cost the claim. We could not find a published figure for **how long the payment takes**. The rules require on-site inspection, then a dictamen, then payment by electronic order (`docs/pasacme.md` §1). We do not claim a speed-up of the payment.
- **The phone is there, but not in every hand** (rows 6 to 8). Most people have a smartphone and WhatsApp-class connectivity. About 15% of people aged 6+ do not use a phone on their own (the survey includes children and does not ask who owns the phone), and the rural internet gap is still 13.7 points. This is why an alert goes to a named contact per parcel, and why outreach also goes through promotoras.
- **Voice first is not a style choice** (row 9). About 7 in 10 producers have primary school or less (71.9%).

## Gaps: figures we looked for and did not find

| Figure we looked for | Status | What the documents state |
|---|---|---|
| Producers per extension officer (Mexico or Edomex) | **Not found** in a primary source we could open. Secondary papers cite program-specific numbers (for example, technicians per group in one SAGARPA program) that do not give a ratio for the valley. | No ratio is quoted. The challenge brief's own scenario (an officer visiting twice a year) is fictional, and it is not cited as data. |
| Mobile signal coverage in the valley (OpenCelliD) | **Not measured.** OpenCelliD downloads need an API key (free registration). The Toluca valley cells were not downloaded or mapped. | Signal is a precondition, to be checked per community (`docs/tradeoffs-preconditions.md` §3). Next step: download the cells for MCC 334 inside the model's bounding box and map them against the roster. |
| Time from loss to payment under PASACME | **Not published.** The rules give the steps but no time limit for the dictamen. | Only the steps: notice within 10 days, then on-site inspection. No speed-up of the payment is claimed. |
