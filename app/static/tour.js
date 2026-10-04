"use strict";
/* The demo that runs by itself («▶ Demo»): the replay night of 14 Nov 2025, feature by feature.
 *
 * One list of steps. Each step has a caption and up to three parts:
 *   before()  where to look before anything happens (idempotent)
 *   act()     what the step changes: sends alerts, answers from the phone... Runs ONCE per run and always in order,
 *             so going back, or jumping ahead on the progress bar, never leaves the story half told
 *   after()   where to look once it happened (idempotent)
 * Everything goes through the same functions the buttons call (app.js): the demo shows the real screens.
 */
(function () {
  const T = { on: false, i: -1, playing: false, speed: 1, done: new Set(), token: 0, chain: Promise.resolve(),
    busy: false, timer: null, left: 0, due: 0, audio: null, note: null };
  const SPEEDS = [1, 1.5, 2];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const row = (pid) => (state.fc ? state.fc.rows.find((r) => r.parcel.parcel_id === pid) : null);
  const parcelPhone = (pid) => (state.parcels.find((p) => p.parcel_id === pid) || {}).phone;
  const T1 = (t) => (t == null ? "—" : (t > 0 ? "+" : t < 0 ? "−" : "") + Math.abs(t).toFixed(1) + " °C");
  const nextDay = (iso) => new Date(Date.parse(iso + "T12:00:00Z") + 864e5).toISOString().slice(0, 10);

  // ------------------------------------------------------------------ what the steps do
  function focus(sel) {
    $$(".tour-focus").forEach((n) => n.classList.remove("tour-focus"));
    const n = typeof sel === "string" ? $(sel) : sel;
    if (!n) return null;
    n.classList.add("tour-focus");
    // something taller than the free part of the window is shown from its top, clear of the player bar
    n.scrollIntoView({ block: n.getBoundingClientRect().height > innerHeight - 330 ? "start" : "center", behavior: "smooth" });
    return n;
  }
  async function press(sel) {
    const n = focus(sel);
    if (!n) return;
    await sleep(700);
    n.classList.add("tour-press"); await sleep(260); n.classList.remove("tour-press");
  }
  const idle = async () => { for (let k = 0; k < 100 && polling; k++) await sleep(40); };
  /** The chat shows everything the server has (the page's own 2.5 s poll may be in flight: wait for it, then ask). */
  async function chat() { await idle(); await pollChat(); }
  async function phoneOf(pid) {
    const ph = parcelPhone(pid), sel = $("#phoneSel");
    if (!ph || sel.value === ph && state.phone === ph) return;
    await idle();
    sel.value = ph; sel.onchange();
    await sleep(60); await idle();
  }
  async function demoNight() {
    if ($("#scenario").value !== "demo" || !state.fc || state.fc.scenario !== "demo") { $("#scenario").value = "demo"; await loadForecast(); }
  }
  /** The farmer types: letter by letter into the composer, then send. */
  async function typeAndSend(text, fast) {
    const box = $("#textIn");
    if (!fast) { focus(".phone"); for (let k = 1; k <= text.length; k++) { box.value = text.slice(0, k); await sleep(45); } await sleep(350); }
    box.value = "";
    await inbound({ text }); await chat();
  }
  const lastAudio = () => { const a = $$("#chat .from-helada audio"); return a[a.length - 1] || null; };

  // ------------------------------------------------------------------ the steps
  // t: [title es, title en]   c: () => [caption es, caption en]
  const STEPS = [
    { t: ["Una noche real", "One real night"],
      c: () => ["Noche del 14 al 15 de noviembre de 2025. El pronóstico es el que había el día anterior; lo que pasó lo midieron las estaciones del SMN. El modelo de esta noche nunca vio esa temporada.",
                "Night of 14–15 November 2025. The forecast is the one issued the day before; what happened was measured by the national weather service's stations. Tonight's model never saw that season."],
      async act() {
        try { await api("/api/demo/reset", { method: "POST" }); } catch (e) { T.note = "reset"; }   // an older server: the run still works
        state.lastMsgId = 0; state.sel = null; $("#chat").innerHTML = ""; $("#toast").hidden = true; $("#sendNote").hidden = true; $("#textIn").value = "";
        $("#demoNow").value = ""; $("#scenario").value = "demo";
        await loadForecast(); await phoneOf("P01"); await chat();
        api("/api/packets").then((l) => ($("#pkCount").textContent = l.length)).catch(() => {});
      },
      async after() { await openTab("mapa"); await demoNight(); focus("#scenarioBanner"); } },

    { t: ["El mapa", "The map"],
      c: () => ["12 parcelas en el valle de Toluca. El color es la probabilidad de helada en cada parcela; el borde punteado marca las que no tienen estación cerca.",
                "12 plots in the Toluca valley. Colour is the chance of frost on each plot; a dashed edge marks plots with no weather station nearby."],
      async after() { await openTab("mapa"); await demoNight(); focus("#map"); } },

    { t: ["Pronóstico regional contra su parcela", "Regional forecast vs. the plot"],
      c: () => { const f = (row("P01") || {}).forecast || {}, d = Math.abs(f.tmin_c - f.grid_tmin_c).toFixed(1);
        return [`Parcela P01. El pronóstico regional anunciaba ${T1(f.grid_tmin_c)}: sin riesgo. Helada calcula ${T1(f.tmin_c)} para esta parcela, ${d} °C más fría, porque aprendió cómo se enfría la estación que está junto a ella.`,
                `Plot P01. The regional forecast said ${T1(f.grid_tmin_c)}: no risk. Helada estimates ${T1(f.tmin_c)} for this plot, ${d} °C colder, because it learned how the station next to it cools at night.`]; },
      async after() { await openTab("mapa"); await demoNight(); await select("P01"); focus("#compare .vs"); } },

    { t: ["La regla del aviso", "The alert rule"],
      c: () => { const f = (row("P01") || {}).forecast || {};
        return [`Probabilidad de helada: ${pct(f.p_frost || 0)}. La regla es fija y está a la vista: con 30% o más se avisa al productor.`,
                `Chance of frost: ${pct(f.p_frost || 0)}. The rule is fixed and visible: at 30% or more the farmer gets an alert.`]; },
      async after() { await openTab("mapa"); await select("P01", false); focus("#compare .pbar"); } },

    { t: ["Lo que pasó", "What happened"],
      c: () => { const t = (row("P01") || {}).truth || {};
        return [`Esa noche la estación junto a la parcela midió ${T1(t.observed_tmin_c)}. Heló, y el pronóstico regional no lo anunciaba.`,
                `That night the station next to the plot measured ${T1(t.observed_tmin_c)}. It froze, and the regional forecast had not announced it.`]; },
      async after() { await openTab("mapa"); await select("P01", false); focus("#compare .truth"); } },

    { t: ["También falla, y se ve", "It also misses, and shows it"],
      c: () => { const r = row("P06") || {}, f = r.forecast || {}, t = r.truth || {};
        return [`Parcela P06: heló (${T1(t.observed_tmin_c)}) y Helada no avisó: daba ${pct(f.p_frost || 0)}. Los fallos se muestran igual que los aciertos.`,
                `Plot P06: it froze (${T1(t.observed_tmin_c)}) and Helada did not alert: it gave ${pct(f.p_frost || 0)}. Misses are shown the same way as hits.`]; },
      async after() { await openTab("mapa"); await select("P06"); focus("#compare .truth"); } },

    { t: ["Una falsa alarma", "A false alarm"],
      c: () => { const r = row("P09") || {}, f = r.forecast || {}, t = r.truth || {};
        return [`Parcela P09: Helada avisó (${pct(f.p_frost || 0)}) y no heló: se midió ${T1(t.observed_tmin_c)}. Una falsa alarma en la noche.`,
                `Plot P09: Helada alerted (${pct(f.p_frost || 0)}) and it did not freeze: ${T1(t.observed_tmin_c)} was measured. One false alarm that night.`]; },
      async after() { await openTab("mapa"); await select("P09"); focus("#compare .truth"); } },

    { t: ["Sin estación cerca", "No station nearby"],
      c: () => { const f = (row("P10") || {}).forecast || {};
        return [`Parcela P10: no hay estación a menos de 1.5 km. El cálculo es aproximado y lo dice: rango de ${T1(f.tmin_lo_c)} a ${T1(f.tmin_hi_c)}. No se manda un aviso sino un mensaje de vigilancia.`,
                `Plot P10: no station within 1.5 km. The estimate is rough and says so: range ${T1(f.tmin_lo_c)} to ${T1(f.tmin_hi_c)}. It sends a watch message, not an alert.`]; },
      async after() { await openTab("mapa"); await select("P10"); focus("#compare .support"); } },

    { t: ["Las parcelas, por riesgo", "Plots, ranked by risk"],
      c: () => { const rs = state.fc ? state.fc.rows : [], a = rs.filter((r) => r.level === "alert").length, w = rs.filter((r) => r.level === "watch").length;
        return [`La tabla ordena las ${rs.length} parcelas de mayor a menor probabilidad: ${a} con aviso, ${w} en vigilancia. En cada una: pronóstico regional, cálculo de Helada, rango y lo que se midió.`,
                `The table ranks the ${rs.length} plots from highest to lowest chance: ${a} with an alert, ${w} on watch. For each: regional forecast, Helada's estimate, range, and what was measured.`]; },
      async after() { await openTab("mapa"); focus("#fcTable"); } },

    { t: ["Enviar avisos", "Send the alerts"],
      c: () => ["«Enviar avisos»: sale un texto y una nota de voz por WhatsApp a cada productor en riesgo. A la derecha, el teléfono de Aurelio, de la parcela P01.",
                "«Send alerts»: a text and a voice note go out on WhatsApp to every farmer at risk. On the right, the phone of Aurelio, plot P01."],
      async before() { await openTab("mapa"); await demoNight(); await select("P01", false); await phoneOf("P01"); },
      async act({ fast }) { if (!fast) await press("#sendBtn"); await sendAlerts(); await chat(); },
      async after() { await phoneOf("P01"); focus(".phone"); } },

    { t: ["El aviso, en voz", "The alert, by voice"],
      c: () => ["El aviso llega en texto y en voz, en español sencillo: qué tan probable es la helada, cuánto puede bajar en su parcela y qué hacer con el cultivo en esta etapa.",
                "The alert arrives as text and as a voice note, in plain Spanish: how likely the frost is, how cold the plot can get, and what to do with the crop at this stage."],
      media: lastAudio,
      async after() { await phoneOf("P01"); await chat(); focus(lastAudio() ? lastAudio().closest(".bub") : ".phone"); } },

    { t: ["El productor contesta por voz", "The farmer answers by voice"],
      c: () => { const real = state.cfg && state.cfg.asr !== "mock";   // say what this server really does
        return [`A la mañana siguiente Aurelio contesta con una nota de voz. ${real ? "Se transcribe aquí mismo, con un modelo de voz pequeño y sin servicios externos," : "En este servidor no está el modelo de voz: la nota de ejemplo trae su texto guardado,"} y Helada repite lo que entendió, para que él lo corrija si hace falta.`,
                `The next morning Aurelio answers with a voice note. ${real ? "It is transcribed right here, by a small speech model and with no outside service," : "This server does not have the speech model: the sample note carries its stored text,"} and Helada says back what it understood, so he can correct it.`]; },
      async act({ fast }) {
        const d = state.fc.date;
        if (!$("#demoNow").value || $("#demoNow").value <= d + "T23:59") $("#demoNow").value = nextDay(d) + "T08:00";   // "anoche" is the replay night
        await phoneOf("P01");
        if (!fast) await press("#sampleAudio");
        await sample("audio"); await chat();
      },
      async after() { await phoneOf("P01"); focus(".phone"); } },

    { t: ["Fotos del daño", "Photos of the damage"],
      c: () => ["Manda 4 fotos desde la parcela. De cada una se leen la hora y el GPS, para comprobar que se tomaron ahí y después de la helada.",
                "He sends 4 photos from the plot. Each one's time and GPS are read, to check they were taken there and after the frost."],
      async act({ fast }) {
        await phoneOf("P01");
        for (let k = 0; k < 4; k++) { if (!fast) await press("#samplePhoto"); await sample("photo"); await chat(); }
      },
      async after() { await phoneOf("P01"); focus(".phone"); } },

    { t: ["El paquete de evidencia", "The evidence packet"],
      c: () => ["Comparte su ubicación y contesta la pregunta del pre-registro. Con eso Helada arma el paquete de evidencia en PDF y se lo manda por el mismo chat.",
                "He shares his location and answers the pre-registration question. With that, Helada builds the evidence packet as a PDF and sends it in the same chat."],
      async act({ fast }) {
        await phoneOf("P01");
        if (!fast) await press("#sampleLoc");
        await sample("location"); await chat();
        await typeAndSend("sí", fast);
      },
      async after() { await phoneOf("P01"); const d = $$("#chat .doc"); focus(d.length ? d[d.length - 1].closest(".bub") : ".phone"); } },

    { t: ["Paquetes", "Packets"],
      c: () => ["El expediente para el programa de siniestros (PASACME): qué está completo, qué debe revisar una persona y la fecha límite para avisar a la Delegación. Helada arma la evidencia; lo decide la Secretaría del Campo.",
                "The file for the state crop-loss programme (PASACME): what is complete, what a person must review, and the deadline to notify the office. Helada assembles the evidence; the Ministry decides."],
      async after() { await openTab("paquetes"); focus("#packets .card") || focus("#packets"); } },

    { t: ["Bitácora", "Audit log"],
      c: () => ["Cada aviso, cada respuesta y cada paquete queda en una cadena de hashes. Solo se puede agregar: si alguien edita un registro, la cadena se rompe y se ve aquí.",
                "Every alert, reply and packet goes into a hash chain. It is append-only: if anyone edits a record, the chain breaks and it shows here."],
      async after() { await openTab("bitacora"); focus("#verify"); } },

    { t: ["Calendario de siembra", "Sowing calendar"],
      c: () => ["Con las fechas de helada de la parcela: para cada ciclo de maíz, si madura antes de la primera helada de otoño. Sembrando a mediados de abril, el ciclo corto madura a tiempo.",
                "From the plot's own frost dates: for each maize cycle, whether it matures before the first autumn frost. Sown in mid-April, the short cycle matures in time."],
      async after() { await select("P01", false); CAL.sow = "04-15"; await openTab("calendario"); focus(".cal-chart"); } },

    { t: ["Elegir la fecha", "Pick the date"],
      c: () => ["Elija otra fecha: sembrando el 1 de junio, con las lluvias, ningún ciclo madura seguro antes de la helada. Es un borrador calculado: requiere la firma de un agrónomo.",
                "Pick another date: sown on 1 June, with the rains, no cycle matures safely before the frost. It is a computed draft: it needs an agronomist's signature."],
      async after() {
        await select("P01", false); await openTab("calendario");
        const b = $('.cal-day[data-sow="06-01"]');
        if (b && b.getAttribute("aria-pressed") !== "true") { await press(b); b.click(); }
        focus(".cal-cards");
      } },

    { t: ["Un termómetro en la parcela", "A thermometer on the plot"],
      c: () => ["Donde no hay estación, un termómetro barato en la parcela (US$30 a 60) hace el trabajo. Ejemplo real: con 44 noches anotadas en Jocotitlán, una estación que el modelo nunca vio, el rango se angosta y el aviso en falso desaparece.",
                "Where there is no station, a cheap thermometer on the plot (US$30–60) does the job. Real example: with 44 nights written down at Jocotitlán, a station the model never saw, the range narrows and the false alert goes away."],
      async after() { await openTab("registrador"); await showLoggerDemo(); focus("#lgResult"); } },

    { t: ["La lectura, por WhatsApp", "The reading, over WhatsApp"],
      c: () => ["El productor de la parcela P05 manda su lectura por el chat: «anoche marcó -2». Helada pregunta antes de guardarla y le dice cuántas noches lleva y qué error esperar.",
                "The farmer of plot P05 sends his reading in the chat: “last night it read -2”. Helada asks before saving it, and tells him how many nights he has and what error to expect."],
      async act({ fast }) {
        await phoneOf("P05");
        await typeAndSend("anoche marcó -2", fast);
        if (!fast) await sleep(1200);
        await typeAndSend("sí", fast);
      },
      async after() { await phoneOf("P05"); focus(".phone"); } },

    { t: ["Evidencia: esa misma noche", "Evidence: that same night"],
      c: () => ["Estación por estación: lo que anunciaba el pronóstico regional, el cálculo de Helada con su rango, y lo que se midió. Pase el cursor por una fila para ver los números.",
                "Station by station: what the regional forecast announced, Helada's estimate with its range, and what was measured. Hover a row to see the numbers."],
      async after() { await openTab("evidencia"); focus(".nc .nc-plot"); } },

    { t: ["Evidencia: la temporada completa", "Evidence: the whole season"],
      c: () => { const v = T.bt && T.bt.known_station && T.bt.known_station.vs_open_meteo_default;
        if (!v) return ["Una temporada completa que el modelo no vio. Cerca de una estación baja el error y detecta más heladas; lejos de una estación la ventaja es menor, y se dice.",
                        "A whole season the model never saw. Near a station the error drops and more frosts are caught; far from one the gain is smaller, and it says so."];
        const a = v.raw_best_match, m = v.model;
        return [`Una temporada completa que el modelo no vio. Cerca de una estación el error baja de ${a.mae.toFixed(2)} a ${m.mae.toFixed(2)} °C y se detecta ${pct(m.recall_at_pofd5)} de las heladas en vez de ${pct(a.recall_at_pofd5)}, con 5% de falsas alarmas. Lejos de una estación la ventaja es menor, y se dice.`,
                `A whole season the model never saw. Near a station the error drops from ${a.mae.toFixed(2)} to ${m.mae.toFixed(2)} °C and ${pct(m.recall_at_pofd5)} of frosts are caught instead of ${pct(a.recall_at_pofd5)}, at 5% false alarms. Far from a station the gain is smaller, and it says so.`]; },
      async before() { if (!T.bt) { try { T.bt = await api("/api/backtest"); } catch (e) { /* the generic caption stays */ } } },
      async after() { await openTab("evidencia"); focus(".evtable") || focus("#backtest"); } },

    { t: ["Esta noche, en vivo", "Tonight, live"],
      c: () => ["El mismo cálculo con el pronóstico de esta noche, para las mismas parcelas. Sin conexión, usa lo último que descargó y lo avisa.",
                "The same computation with tonight's forecast, for the same plots. With no connection it uses the last one it downloaded, and says so."],
      async after() { await openTab("mapa"); if ($("#scenario").value !== "") { $("#scenario").value = ""; $("#nightDate").value = state.cfg.today; await loadForecast(); } focus("#map"); } },

    { t: ["En el teléfono, sin conexión", "On the phone, offline"],
      c: () => ['El mismo modelo corre en el teléfono del productor, sin señal: 372 KB. <a href="/static/movil/index.html#tour" target="_blank" rel="noopener">Abrir la página del teléfono ↗</a>',
                'The same model runs on the farmer\'s phone, with no signal: 372 KB. <a href="/static/movil/index.html#tour" target="_blank" rel="noopener">Open the phone page ↗</a>'],
      async after() { await openTab("mapa"); await demoNight(); await select("P01"); focus(null); } },
  ];

  // ------------------------------------------------------------------ player
  let bar = null;
  function build() {
    bar = document.createElement("div");
    bar.className = "tour"; bar.id = "tour"; bar.setAttribute("role", "region"); bar.setAttribute("aria-label", "Demostración");
    bar.innerHTML = `<div class="tour-segs">${STEPS.map((s, k) => `<button type="button" class="tour-seg" data-k="${k}"><i></i></button>`).join("")}</div>
      <div class="tour-body">
        <div class="tour-text"><div class="tour-kicker"></div><p class="tour-cap" aria-live="polite"></p></div>
        <div class="tour-ctl">
          <button type="button" data-a="prev" title="Anterior (←)" aria-label="Anterior">⏮</button>
          <button type="button" data-a="play" class="main" title="Pausa o seguir (espacio)"></button>
          <button type="button" data-a="next" title="Siguiente (→)" aria-label="Siguiente">⏭</button>
          <span class="sep"></span>
          <button type="button" data-a="speed" title="Velocidad"></button>
          <button type="button" data-a="lang" title="Idioma de los textos de la demostración"></button>
          <button type="button" data-a="close" title="Salir (Esc)" aria-label="Salir de la demostración">✕</button>
        </div>
      </div>`;
    document.body.appendChild(bar);
    bar.addEventListener("click", (e) => {
      const seg = e.target.closest(".tour-seg"), b = e.target.closest("[data-a]");
      if (seg) return go(+seg.dataset.k);
      if (!b) return;
      const a = b.dataset.a;
      if (a === "prev") go(T.i - 1);
      else if (a === "next") go(T.i + 1);
      else if (a === "play") toggle();
      else if (a === "speed") { T.speed = SPEEDS[(SPEEDS.indexOf(T.speed) + 1) % SPEEDS.length]; if (T.timer) { pauseTimer(); schedule(T.left); } paint(); }
      else if (a === "lang") setLang(LANG === "es" ? "en" : "es");    // captions and screens share one language (app.js)
      else if (a === "close") stop();
    });
  }

  const atEnd = () => T.i === STEPS.length - 1 && !T.busy && !T.timer && !T.playing;
  function paint() {
    if (!bar) return;
    const s = STEPS[T.i], li = LANG === "es" ? 0 : 1;
    bar.classList.toggle("paused", !T.playing);
    bar.querySelector(".tour-kicker").textContent = `${T.i + 1} / ${STEPS.length} · ${s.t[li]}`;
    bar.querySelector(".tour-cap").innerHTML = s.c()[li];   // captions are written here, not user input
    bar.querySelectorAll(".tour-seg").forEach((n, k) => {
      n.classList.toggle("done", k < T.i); n.classList.toggle("cur", k === T.i); n.title = `${k + 1}. ${STEPS[k].t[li]}`;
      if (k !== T.i) n.classList.remove("run", "full");
    });
    const play = bar.querySelector('[data-a="play"]');
    play.textContent = atEnd() ? "↻" : T.playing ? "⏸" : "▶";
    play.setAttribute("aria-label", atEnd() ? TX("Repetir", "Replay") : T.playing ? TX("Pausa", "Pause") : TX("Seguir", "Resume"));
    bar.querySelector('[data-a="prev"]').disabled = T.i <= 0;
    bar.querySelector('[data-a="next"]').disabled = T.i >= STEPS.length - 1;
    bar.querySelector('[data-a="speed"]').textContent = T.speed + "×";
    bar.querySelector('[data-a="lang"]').textContent = LANG === "es" ? "EN" : "ES";
    bar.setAttribute("aria-label", TX("Demostración", "Demo"));
    for (const [a, es, en] of [["prev", "Anterior (←)", "Previous (←)"], ["play", "Pausa o seguir (espacio)", "Pause or resume (space)"], ["next", "Siguiente (→)", "Next (→)"],
      ["speed", "Velocidad", "Speed"], ["lang", "Idioma del tablero y de la demostración", "Language of the dashboard and the demo"], ["close", "Salir (Esc)", "Exit (Esc)"]])
      bar.querySelector(`[data-a="${a}"]`).title = TX(es, en);
    bar.querySelector('[data-a="prev"]').setAttribute("aria-label", TX("Anterior", "Previous"));
    bar.querySelector('[data-a="next"]').setAttribute("aria-label", TX("Siguiente", "Next"));
    bar.querySelector('[data-a="close"]').setAttribute("aria-label", TX("Salir de la demostración", "Exit the demo"));
  }

  // time on screen: enough to read the caption aloud
  const dwell = (s) => Math.min(15000, 2600 + 48 * s.c()[LANG === "es" ? 0 : 1].replace(/<[^>]+>/g, "").length);
  function schedule(ms) {
    clearTimeout(T.timer);
    T.left = ms; T.due = Date.now() + ms / T.speed;
    const seg = bar.querySelector(".tour-seg.cur");
    if (seg && !seg.classList.contains("run")) { seg.style.setProperty("--dur", ms / T.speed + "ms"); seg.classList.remove("full"); void seg.offsetWidth; seg.classList.add("run"); }
    T.timer = setTimeout(() => {
      T.timer = null;
      if (T.i < STEPS.length - 1) go(T.i + 1);
      else { T.playing = false; if (seg) { seg.classList.remove("run"); seg.classList.add("full"); } paint(); }
    }, ms / T.speed);
  }
  function pauseTimer() { if (!T.timer) return; clearTimeout(T.timer); T.timer = null; T.left = Math.max(400, (T.due - Date.now()) * T.speed); }
  function stopAudio() {
    const a = T.audio; T.audio = null;
    if (a) { a.onended = a.onpause = a.onplay = null; try { a.pause(); } catch (e) { /* gone */ } }
  }
  // A focused audio player takes the keyboard for itself (space, arrows): the keys belong to the demo.
  const unfocusMedia = () => { const n = document.activeElement; if (n && /^(AUDIO|VIDEO)$/.test(n.tagName)) n.blur(); };

  /** After a step is on screen: play its voice note if it has one, then count down to the next step. */
  function settle() {
    const s = STEPS[T.i], a = s.media && s.media();
    if (a && T.playing) {
      T.audio = a;
      a.onended = () => { T.audio = null; a.onpause = a.onplay = null; if (T.playing) schedule(1500); };
      // the player's own pause and play buttons pause and resume the demo too
      a.onpause = () => { setTimeout(unfocusMedia, 0); if (T.audio === a && !a.ended && T.playing) { T.playing = false; paint(); } };
      a.onplay = () => { setTimeout(unfocusMedia, 0); if (T.audio === a && !T.playing) { T.playing = true; paint(); } };
      try { a.currentTime = 0; } catch (e) { /* not loaded yet */ }
      const p = a.play();
      if (p && p.catch) p.catch(() => { T.audio = null; if (T.playing) schedule(dwell(s)); });   // the browser refused to play: just read
      return;
    }
    if (T.playing) schedule(dwell(s));
  }

  function go(i) {
    if (!T.on || i < 0 || i >= STEPS.length) return;
    const my = ++T.token;
    clearTimeout(T.timer); T.timer = null; stopAudio(); unfocusMedia();
    T.i = i; T.busy = true;
    const cur = bar.querySelector(".tour-seg.cur"); if (cur) cur.classList.remove("run", "full");
    paint();
    // one at a time: a step that is sending something finishes before the next one starts
    T.chain = T.chain.then(async () => {
      if (my !== T.token || !T.on) return;
      try {
        for (let k = 0; k < i; k++) if (STEPS[k].act && !T.done.has(k)) { T.done.add(k); await STEPS[k].act({ fast: true }); }
        const s = STEPS[i];
        if (s.before) await s.before();
        if (s.act && !T.done.has(i)) { T.done.add(i); await s.act({ fast: false }); }
        if (my !== T.token || !T.on) return;
        if (s.after) await s.after();
      } catch (e) { console.warn("demo: step " + (i + 1) + " failed:", e); }
      if (my !== T.token || !T.on) return;
      T.busy = false; paint(); settle();
    });
  }

  function toggle() {
    if (atEnd()) { T.done.clear(); T.playing = true; return go(0); }
    T.playing = !T.playing; unfocusMedia();
    if (T.playing) { if (T.audio) T.audio.play().catch(() => {}); else if (!T.busy) schedule(T.left || dwell(STEPS[T.i])); }
    else { pauseTimer(); if (T.audio) T.audio.pause(); }
    paint();
  }

  function start() {
    if (T.on) return;
    if (!bar) build();
    T.on = true; T.playing = true; T.done.clear(); T.i = 0; T.left = 0; T.chain = Promise.resolve();
    bar.hidden = false; document.body.classList.add("touring");
    $("#demoBtn").hidden = true;
    go(0);
  }
  function stop() {
    T.on = false; T.playing = false; T.token++;
    clearTimeout(T.timer); T.timer = null; stopAudio();
    $$(".tour-focus").forEach((n) => n.classList.remove("tour-focus"));
    if (bar) bar.hidden = true;
    document.body.classList.remove("touring");
    $("#demoBtn").hidden = false;
    if ($("#scenario").value !== "demo") { $("#scenario").value = "demo"; loadForecast(); }
  }

  document.addEventListener("helada:lang", () => paint());      // the language button, in the header or in this bar
  window.addEventListener("keydown", (e) => {
    if (!T.on || e.metaKey || e.ctrlKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    if (/^(AUDIO|VIDEO)$/.test(e.target.tagName)) e.stopPropagation();   // the demo's key, not the player's
    if (e.key === " ") { e.preventDefault(); toggle(); }
    else if (e.key === "ArrowRight") go(T.i + 1);
    else if (e.key === "ArrowLeft") go(T.i - 1);
    else if (e.key === "Escape") stop();
  }, true);

  $("#demoBtn").onclick = start;
  window.HeladaTour = { start, stop, go, steps: STEPS.length, status: () => ({ on: T.on, step: T.i, busy: T.busy, playing: T.playing }) };
  // /#demo starts it on load (the voice note then needs one click on the page: browsers do not autoplay sound)
  if (location.hash === "#demo") window.addEventListener("load", () => setTimeout(start, 1200));
})();
