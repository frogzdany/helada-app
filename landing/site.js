/* Helada landing page: outbound links, the English reading aid, and the pause button of the hero scene.
 *
 * Spanish is the page: it is in index.html and works with scripts off. `en` below is for reviewers who do not read
 * Spanish; it changes the words on screen and nothing else. The example alert and the "No estoy seguro" card
 * stay in Spanish in both languages, because that is what the farmer gets.
 */
(function () {
  "use strict";

  // Every outbound address. index.html carries the same three as its no-script fallback.
  var LINKS = {
    panel: "https://panel.helada.app",          // dashboard demo with the built-in phone simulator
    phone: "https://m.helada.app/",   // the installable phone page
    contact: "mailto:hola@helada.app",
  };

  var en = {
    title: "Helada: frost warnings for your parcel, by WhatsApp",
    description: "Per-parcel frost warnings for rainfed maize in Mexico's Toluca valley, and loss-evidence packets. By WhatsApp voice note, and on the phone with no signal.",
    skip: "Skip to content",
    nav_label: "Sections",
    nav_what: "What it does",
    nav_evidence: "Evidence",
    nav_offices: "For offices",
    mast_l: "Rainfed maize · Toluca, Ixtlahuaca and Atlacomulco valleys, Mexico",
    mast_r: "Prototype, October 2026",
    hero_h1: "Frost warnings for your parcel, by WhatsApp",
    hero_lead: "Helada corrects tonight's forecast for each parcel of rainfed maize and warns the farmer with a voice note.",
    cta_demo: "See the demo",
    cta_phone: "Open on the phone",
    hero_fine: "This is a prototype. The names, phone numbers and parcels in the demo are fictional.",
    hero_alt: "An engraving of the Nevado de Toluca volcano behind a row of maize killed by frost. The sky runs through cloud, rain, a clear night and dawn.",
    hero_c: "The Nevado de Toluca, the frost-killed maize and the clouds are AI-generated engravings, not photographs. The sky runs through cloud, rain, a clear night with frost, and dawn.",
    anim_pause: "Pause the animation",
    anim_play: "Resume the animation",
    how_h2: "How the warning arrives",
    how_p: "The farmer hears the warning as a voice note and reads it in the same message: the chance of frost, the parcel's temperature with its range, one action, and the word that stops the warnings. Helada also works on the phone, with no signal.",
    slip_label: "Example warning",
    read_temp: "Your parcel, tonight",
    read_prob: "Chance of frost",
    voice_aria: "Voice note, 18 seconds",
    phone_caption: "An example of the warning, in Spanish as the farmer receives it: a 70% chance of frost tonight, the parcel at −2\u00a0°C (range −4 to 0\u00a0°C), one action, and BAJA to stop. The parcel and the values are fictional.",
    prob_h2: "The valley has more than a hundred frost days a year, and the free forecast rarely warns",
    prob1_n: "More than 100",
    prob1_t: "frost days a year in the Toluca district. In the 2019 season, frost took 3,511 ha of rainfed maize, by an estimate from satellite images.",
    prob1_s: "Jasso-Miranda, Soria-Ruiz and Antonio-Némiga, Rev. Mex. Cienc. Agríc. 13(2), 2022.",
    prob2_n: "1 in 4",
    prob2_t: "frost nights is all the free regional forecast catches. It also reads 1.5\u00a0°C warm at weather stations.",
    prob2_s: "Measured by the team: 39 SMN stations, 2025-26 season.",
    prob3_n: "10 days",
    prob3_t: "is what a farmer has to report the damage to the state program (PASACME), which then verifies it on the parcel. They are calendar days.",
    prob3_s: "PASACME rules, Gaceta del Gobierno No. 114, 2024.",
    what_h2: "What Helada does",
    what1_t: "Frost warning",
    what1_d: "A small model corrects tonight's forecast for each parcel with what weather stations have measured, and works out the chance of frost. The farmer hears it as a WhatsApp voice note between 18:00 and 20:00, at most twice a week.",
    what2_t: "Planting calendar",
    what2_d: "Each parcel's own frost dates help choose the sowing date and the variety's cycle. When the maize is in flower, a warning that same evening rarely saves the crop, and Helada says so.",
    what3_t: "Loss packet",
    what3_d: "A voice note and a few photos become a PDF with the parcel, the date, the location, the modelled and the observed temperature, and the program's list of documents. It says only \"Documentos completos\" or \"Falta: X\". It never decides whether support is due: the Secretaría del Campo decides that, on the parcel.",
    ev_h2: "Out of every 100 frost nights, how many get a warning",
    ev_lead: "Each square is a frost night. The filled squares are the nights the method warned about. Both methods give the same false alarms: 5 in every 100 nights without frost.",
    ev_near_h3: "A parcel near an SMN weather station",
    ev_far_h3: "A parcel with no station nearby",
    ev_forecast: "Free regional forecast",
    ev_c1_aria: "24 of 100 squares filled",
    ev_c2_aria: "58 of 100 squares filled",
    ev_c3_aria: "32 of 100 squares filled",
    ev_near_p: "Helada gives a warning on 57.9% of frost nights, against 24.3% for the forecast. A spreadsheet with one correction per station reaches 36.2%.",
    ev_far_p: "<strong>Away from a station, Helada does not detect frost better</strong> (32.4% against 32.1%). It only removes the forecast's warm bias: the error falls from 2.83 to 2.32\u00a0°C, and the range it shows is wider. Better accuracy needs temperature loggers on about 5 parcels per ejido.",
    ev_source: "Near a station: the 2025-26 season, which the model never saw in training; 39 SMN stations, 5,855 nights, 503 of them with frost. \"Near\" means within 1.5 km and 50 m of elevation. No station nearby: 80 stations held out of training, the 2024-25 and 2025-26 seasons, 19,592 nights. The ground truth is the temperature in the station's shelter, not on the crop.",
    small_h2: "A 372 KB model that runs on the phone",
    small_p1: "The frost model is 372 KB. It runs on the server and also on the farmer's phone, in a page that installs and works with no signal. The phone version gives the same result as the original: under 0.005\u00a0°C of difference on 204 test cases.",
    small_p2: "With no signal, the phone stores readings and reports, and sends them when the signal returns.",
    small_f1: "the frost model, on the phone and on the server",
    small_f2n: "About 1 MB",
    small_f2: "the whole phone page",
    small_f3: "speech to text, which runs on the server and not on the phone. It was tested on 12 synthetic voice notes, not on farmers",
    small_f4n: "Fixed rules",
    small_f4: "to read a damage report: AI did not beat them there, so we do not use it",
    who_h2: "A person decides at every step",
    who_p: "The phone says this (\"I am not sure. Ask your technician before you decide.\") when there is no saved forecast, when the forecast is old, when inputs are missing, or when there is no station nearby on a cold night. One word, BAJA, stops every warning.",
    who1_t: "What to do with a warning",
    who1_d: "The farmer decides.",
    who2_t: "When information is missing",
    who2_d: "The technician decides.",
    who3_t: "Support for a loss",
    who3_d: "The Secretaría del Campo decides, with a visit to the parcel.",
    who4_t: "The advice",
    who4_d: "An agronomist must sign it. Until then it reads \"Borrador sin firma agronómica\" (unsigned draft).",
    lim_h3: "What is still missing",
    lim1: "The improvement is measured only near SMN stations.",
    lim2: "Speech recognition has not been measured with real farmers.",
    lim3: "Mazahua is not supported yet.",
    lim4: "There is no consent step at enrollment, and no deletion of data on request.",
    lim5: "Sending through WhatsApp Business has not been tested on a live account.",
    off_h2: "For agriculture offices and partners",
    off_p1: "The next step is one ejido for one frost season: 5 temperature loggers (about MX$3,300), a consented roster, an agronomist's signature, and an agreement with the Secretaría del Campo on how it receives a digital packet.",
    off_p2: "The same system already runs for potato in Puno, Peru, on the forecast alone and with no claim of improvement: there too, accuracy has to be earned with local measurements.",
    off_cta: "Write to hola@helada.app",
    cost_p1: "per farmer in the first year, then US$0.15 to US$0.30 per season.",
    cost_s: "These are estimates. They include messages, the office's equipment and the loggers. They leave out extension and agronomist time, which is the largest cost.",
    foot_1: "Helada is a prototype by team SysCallOx4, October 2026.",
    foot_2: "Data: SMN and CONAGUA, Open-Meteo (CC BY 4.0), Copernicus GLO-30. Typefaces Besley and Libre Franklin (SIL OFL).",
    foot_3: "The photograph is from Wikimedia Commons, credited below it. The engravings are AI-generated illustrations (Gemini), not photographs.",
    jocotitlan_alt: "A field of standing maize with the Jocotitlán volcano behind it.",
    jocotitlan_c: "Maize at the foot of the Jocotitlán volcano, Estado de México, July 2007.",
    jocotitlan_s: "Photo: <a href=\"https://commons.wikimedia.org/wiki/File:Volc%C3%A1n_Jocotitl%C3%A1n.JPG\">Hernan Corona</a>, <a href=\"https://creativecommons.org/licenses/by-sa/4.0/\">CC BY-SA 4.0</a>. Cropped and printed in one ink.",
  };

  var KEY = "helada.landing.lang";
  var es = null; // Spanish texts, read from the page the first time the language changes
  var doc = document;

  function nodes(attr) { return doc.querySelectorAll("[" + attr + "]"); }

  function remember() {
    es = { title: doc.title, description: doc.querySelector('meta[name="description"]').content };
    nodes("data-i18n").forEach(function (el) { es[el.dataset.i18n] = el.innerHTML; });
    nodes("data-i18n-aria").forEach(function (el) { es[el.dataset.i18nAria] = el.getAttribute("aria-label"); });
    nodes("data-i18n-alt").forEach(function (el) { es[el.dataset.i18nAlt] = el.alt; });
  }

  function apply(lang) {
    if (!es) remember();
    var t = lang === "en" ? en : es;
    doc.documentElement.lang = lang === "en" ? "en" : "es-MX";
    doc.title = t.title;
    doc.querySelector('meta[name="description"]').content = t.description;
    nodes("data-i18n").forEach(function (el) { el.innerHTML = t[el.dataset.i18n]; });
    nodes("data-i18n-aria").forEach(function (el) { el.setAttribute("aria-label", t[el.dataset.i18nAria]); });
    nodes("data-i18n-alt").forEach(function (el) { el.alt = t[el.dataset.i18nAlt]; });
    var btn = doc.getElementById("lang");
    btn.textContent = lang === "en" ? "Español" : "English";
    btn.lang = lang === "en" ? "es" : "en";
    btn.dataset.to = lang === "en" ? "es" : "en";
  }

  nodes("data-link").forEach(function (a) { a.href = LINKS[a.dataset.link]; });

  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) { /* storage blocked: stay in Spanish */ }
  if (saved === "en") apply("en");

  // ?demo=1 runs the hero scene's loop in 10 seconds, for a quick look at the whole scene.
  if (/[?&]demo=1(&|$)/.test(location.search)) doc.documentElement.classList.add("demo");

  // The hero scene: a button to pause it, and it rests while it is off screen.
  var scene = doc.getElementById("scene");
  var motion = doc.getElementById("motion");
  motion.addEventListener("click", function () {
    var paused = scene.classList.toggle("is-paused");
    motion.setAttribute("aria-pressed", String(paused));
  });
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) {
      scene.classList.toggle("is-away", !entries[0].isIntersecting);
    }).observe(scene.parentNode);
  }

  doc.getElementById("lang").addEventListener("click", function (ev) {
    var to = ev.currentTarget.dataset.to || "en";
    apply(to);
    try { localStorage.setItem(KEY, to); } catch (e) { /* not remembered */ }
  });
})();
