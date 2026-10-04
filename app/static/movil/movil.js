/* Helada on the phone: screens, storage and sync. The model itself is helada-model.js.
 *
 * Offline first. Everything the screen needs is on the phone: the model files, the parcel's site pack, the last
 * forecast that was downloaded, the thermometer readings and the damage reports. The network is used only to
 *   (1) download the forecast for the next nights when there is signal (Open-Meteo, CC BY 4.0), and
 *   (2) send queued readings and damage reports to the Helada server (store and forward).
 */
(function () {
  "use strict";
  const HM = window.HeladaModel, I18N = window.HeladaI18n, Q = window.HeladaCola;
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  // ------------------------------------------------------------------ icons
  // Phosphor Icons (MIT, phosphoricons.com), bold and fill weights, inlined so the page needs no network.
  const ICONS = {
    "frost": '<path d="M227.65,149.14a12,12,0,0,1-8.79,14.51l-20.67,5.08,5.4,20.16a12,12,0,0,1-23.18,6.22l-7.29-27.2L140,148.78V187l20.48,20.48a12,12,0,0,1-17,17L128,209l-15.51,15.52a12,12,0,0,1-17-17L116,187V148.78L82.88,167.91l-7.29,27.2a12,12,0,0,1-23.18-6.22l5.4-20.16-20.67-5.08a12,12,0,1,1,5.72-23.3l27.89,6.85L104,128,70.75,108.8l-27.89,6.85A11.8,11.8,0,0,1,40,116a12,12,0,0,1-2.85-23.65l20.67-5.08-5.4-20.16a12,12,0,0,1,23.18-6.22l7.29,27.2L116,107.21V69L95.52,48.48a12,12,0,0,1,17-17L128,47l15.51-15.52a12,12,0,1,1,17,17L140,69v38.24l33.12-19.12,7.29-27.2a12,12,0,0,1,23.18,6.22l-5.4,20.16,20.67,5.08A12,12,0,0,1,216,116a11.8,11.8,0,0,1-2.87-.35l-27.89-6.85L152,128l33.25,19.2,27.89-6.85A12,12,0,0,1,227.65,149.14Z"/>',
    "unsure": '<path d="M235.33,116.72,139.28,20.66a16,16,0,0,0-22.56,0l-96,96.06a16,16,0,0,0,0,22.56l96.05,96.06h0a16,16,0,0,0,22.56,0l96.05-96.06a16,16,0,0,0,0-22.56ZM120,80a8,8,0,0,1,16,0v56a8,8,0,0,1-16,0Zm8,104a12,12,0,1,1,12-12A12,12,0,0,1,128,184Z"/>',
    "ok": '<path d="M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm45.66,85.66-56,56a8,8,0,0,1-11.32,0l-24-24a8,8,0,0,1,11.32-11.32L112,148.69l50.34-50.35a8,8,0,0,1,11.32,11.32Z"/>',
    "moon": '<path d="M244,96a12,12,0,0,1-12,12H220v12a12,12,0,0,1-24,0V108H184a12,12,0,0,1,0-24h12V72a12,12,0,0,1,24,0V84h12A12,12,0,0,1,244,96ZM144,60h4v4a12,12,0,0,0,24,0V60h4a12,12,0,0,0,0-24h-4V32a12,12,0,0,0-24,0v4h-4a12,12,0,0,0,0,24Zm75.81,90.38A12,12,0,0,1,222,162.3,100,100,0,1,1,93.7,34a12,12,0,0,1,15.89,13.6A85.12,85.12,0,0,0,108,64a84.09,84.09,0,0,0,84,84,85.22,85.22,0,0,0,16.37-1.59A12,12,0,0,1,219.81,150.38ZM190,172A108.13,108.13,0,0,1,84,66,76,76,0,1,0,190,172Z"/>',
    "thermo": '<path d="M180,150.69V56A52,52,0,0,0,76,56v94.69a64,64,0,1,0,104,0ZM128,228a40,40,0,0,1-30.91-65.39,12,12,0,0,0,2.91-7.83V56a28,28,0,0,1,56,0v98.77a12,12,0,0,0,2.77,7.68A40,40,0,0,1,128,228Zm24-40a24,24,0,1,1-36-20.78V92a12,12,0,0,1,24,0v75.22A24,24,0,0,1,152,188Z"/>',
    "report": '<path d="M232.49,55.51l-32-32a12,12,0,0,0-17,0l-96,96A12,12,0,0,0,84,128v32a12,12,0,0,0,12,12h32a12,12,0,0,0,8.49-3.51l96-96A12,12,0,0,0,232.49,55.51ZM192,49l15,15L196,75,181,60Zm-69,99H108V133l56-56,15,15Zm105-7.43V208a20,20,0,0,1-20,20H48a20,20,0,0,1-20-20V48A20,20,0,0,1,48,28h67.43a12,12,0,0,1,0,24H52V204H204V140.57a12,12,0,0,1,24,0Z"/>',
    "speaker": '<path d="M157.27,21.22a12,12,0,0,0-12.64,1.31L75.88,76H32A20,20,0,0,0,12,96v64a20,20,0,0,0,20,20H75.88l68.75,53.47A12,12,0,0,0,164,224V32A12,12,0,0,0,157.27,21.22ZM36,100H68v56H36Zm104,99.46L92,162.13V93.87l48-37.33ZM212,128a44,44,0,0,1-11,29.11,12,12,0,1,1-18-15.88,20,20,0,0,0,0-26.43,12,12,0,0,1,18-15.86A43.94,43.94,0,0,1,212,128Zm40,0a83.87,83.87,0,0,1-21.39,56,12,12,0,0,1-17.89-16,60,60,0,0,0,0-80,12,12,0,1,1,17.88-16A83.87,83.87,0,0,1,252,128Z"/>',
    "refresh": '<path d="M228,48V96a12,12,0,0,1-12,12H168a12,12,0,0,1,0-24h19l-7.8-7.8a75.55,75.55,0,0,0-53.32-22.26h-.43A75.49,75.49,0,0,0,72.39,75.57,12,12,0,1,1,55.61,58.41a99.38,99.38,0,0,1,69.87-28.47H126A99.42,99.42,0,0,1,196.2,59.23L204,67V48a12,12,0,0,1,24,0ZM183.61,180.43a75.49,75.49,0,0,1-53.09,21.63h-.43A75.55,75.55,0,0,1,76.77,179.8L69,172H88a12,12,0,0,0,0-24H40a12,12,0,0,0-12,12v48a12,12,0,0,0,24,0V189l7.8,7.8A99.42,99.42,0,0,0,130,226.06h.56a99.38,99.38,0,0,0,69.87-28.47,12,12,0,0,0-16.78-17.16Z"/>',
    "phone": '<path d="M231.88,175.08A56.26,56.26,0,0,1,176,224C96.6,224,32,159.4,32,80A56.26,56.26,0,0,1,80.92,24.12a16,16,0,0,1,16.62,9.52l21.12,47.15,0,.12A16,16,0,0,1,117.39,96c-.18.27-.37.52-.57.77L96,121.45c7.49,15.22,23.41,31,38.83,38.51l24.34-20.71a8.12,8.12,0,0,1,.75-.56,16,16,0,0,1,15.17-1.4l.13.06,47.11,21.11A16,16,0,0,1,231.88,175.08Z"/>',
    "signal": '<path d="M172,72V200a12,12,0,0,1-24,0V72a12,12,0,0,1,24,0Zm-52,28a12,12,0,0,0-12,12v88a12,12,0,0,0,24,0V112A12,12,0,0,0,120,100ZM80,140a12,12,0,0,0-12,12v48a12,12,0,0,0,24,0V152A12,12,0,0,0,80,140ZM40,180a12,12,0,0,0-12,12v8a12,12,0,0,0,24,0v-8A12,12,0,0,0,40,180Z"/>',
    "signal-off": '<path d="M92,152v48a12,12,0,0,1-24,0V152a12,12,0,0,1,24,0ZM40,180a12,12,0,0,0-12,12v8a12,12,0,0,0,24,0v-8A12,12,0,0,0,40,180Zm176.88,27.93-160-176A12,12,0,1,0,39.12,48.07L108,123.84V200a12,12,0,0,0,24,0V150.24l16,17.6V200a12,12,0,0,0,24,0v-5.76l27.12,29.83a12,12,0,0,0,17.76-16.14ZM160,115.74a12,12,0,0,0,12-12V72a12,12,0,0,0-24,0v31.74A12,12,0,0,0,160,115.74Zm40,44a12,12,0,0,0,12-12V32a12,12,0,0,0-24,0V147.74A12,12,0,0,0,200,159.74Z"/>',
    "chevron": '<path d="M216.49,104.49l-80,80a12,12,0,0,1-17,0l-80-80a12,12,0,0,1,17-17L128,159l71.51-71.52a12,12,0,0,1,17,17Z"/>',
    "check": '<path d="M232.49,80.49l-128,128a12,12,0,0,1-17,0l-56-56a12,12,0,1,1,17-17L96,183,215.51,63.51a12,12,0,0,1,17,17Z"/>',
    "clock": '<path d="M128,20A108,108,0,1,0,236,128,108.12,108.12,0,0,0,128,20Zm0,192a84,84,0,1,1,84-84A84.09,84.09,0,0,1,128,212Zm68-84a12,12,0,0,1-12,12H128a12,12,0,0,1-12-12V72a12,12,0,0,1,24,0v44h44A12,12,0,0,1,196,128Z"/>',
    "eye": '<path d="M251,123.13c-.37-.81-9.13-20.26-28.48-39.61C196.63,57.67,164,44,128,44S59.37,57.67,33.51,83.52C14.16,102.87,5.4,122.32,5,123.13a12.08,12.08,0,0,0,0,9.75c.37.82,9.13,20.26,28.49,39.61C59.37,198.34,92,212,128,212s68.63-13.66,94.48-39.51c19.36-19.35,28.12-38.79,28.49-39.61A12.08,12.08,0,0,0,251,123.13Zm-46.06,33C183.47,177.27,157.59,188,128,188s-55.47-10.73-76.91-31.88A130.36,130.36,0,0,1,29.52,128,130.45,130.45,0,0,1,51.09,99.89C72.54,78.73,98.41,68,128,68s55.46,10.73,76.91,31.89A130.36,130.36,0,0,1,226.48,128,130.45,130.45,0,0,1,204.91,156.12ZM128,84a44,44,0,1,0,44,44A44.05,44.05,0,0,0,128,84Zm0,64a20,20,0,1,1,20-20A20,20,0,0,1,128,148Z"/>',
    "sun": '<path d="M116,36V20a12,12,0,0,1,24,0V36a12,12,0,0,1-24,0Zm80,92a68,68,0,1,1-68-68A68.07,68.07,0,0,1,196,128Zm-24,0a44,44,0,1,0-44,44A44.05,44.05,0,0,0,172,128ZM51.51,68.49a12,12,0,1,0,17-17l-12-12a12,12,0,0,0-17,17Zm0,119-12,12a12,12,0,0,0,17,17l12-12a12,12,0,1,0-17-17ZM196,72a12,12,0,0,0,8.49-3.51l12-12a12,12,0,0,0-17-17l-12,12A12,12,0,0,0,196,72Zm8.49,115.51a12,12,0,0,0-17,17l12,12a12,12,0,0,0,17-17ZM48,128a12,12,0,0,0-12-12H20a12,12,0,0,0,0,24H36A12,12,0,0,0,48,128Zm80,80a12,12,0,0,0-12,12v16a12,12,0,0,0,24,0V220A12,12,0,0,0,128,208Zm108-92H220a12,12,0,0,0,0,24h16a12,12,0,0,0,0-24Z"/>',
    "night": '<path d="M236.37,139.4a12,12,0,0,0-12-3A84.07,84.07,0,0,1,119.6,31.59a12,12,0,0,0-15-15A108.86,108.86,0,0,0,49.69,55.07,108,108,0,0,0,136,228a107.09,107.09,0,0,0,64.93-21.69,108.86,108.86,0,0,0,38.44-54.94A12,12,0,0,0,236.37,139.4Zm-49.88,47.74A84,84,0,0,1,68.86,69.51,84.93,84.93,0,0,1,92.27,48.29Q92,52.13,92,56A108.12,108.12,0,0,0,200,164q3.87,0,7.71-.27A84.79,84.79,0,0,1,186.49,187.14Z"/>',
    "hail": '<path d="M184,208a16,16,0,1,1-16-16A16,16,0,0,1,184,208Zm-64-16a16,16,0,1,0,16,16A16,16,0,0,0,120,192Zm-48,0a16,16,0,1,0,16,16A16,16,0,0,0,72,192ZM236,92a80.09,80.09,0,0,1-80,80H76A56,56,0,0,1,76,60a56.76,56.76,0,0,1,6.39.36A80.08,80.08,0,0,1,236,92Zm-24,0a56.06,56.06,0,0,0-112-3.31,12,12,0,1,1-24-1.38c.06-1.11.15-2.21.26-3.31H76a32,32,0,0,0,0,64h80A56.06,56.06,0,0,0,212,92Z"/>',
    "flood": '<path d="M225.24,174.74a12,12,0,0,1-1.58,16.89C205.49,206.71,189.06,212,174.15,212c-19.76,0-36.86-9.29-51.88-17.44-25.06-13.62-44.86-24.37-74.61.3a12,12,0,1,1-15.32-18.48c42.25-35,75-17.23,101.39-2.92,25.06,13.61,44.86,24.37,74.61-.3A12,12,0,0,1,225.24,174.74Zm-16.9-57.59c-29.75,24.67-49.55,13.91-74.61.3-26.35-14.3-59.14-32.11-101.39,2.92a12,12,0,0,0,15.32,18.48c29.75-24.67,49.55-13.92,74.61-.3,15,8.15,32.12,17.44,51.88,17.44,14.91,0,31.34-5.29,49.51-20.36a12,12,0,0,0-15.32-18.48ZM47.66,82.84c29.75-24.67,49.55-13.92,74.61-.3,15,8.15,32.12,17.44,51.88,17.44,14.91,0,31.34-5.29,49.51-20.36a12,12,0,0,0-15.32-18.48c-29.75,24.67-49.55,13.92-74.61.3-26.35-14.3-59.14-32.11-101.39,2.93A12,12,0,1,0,47.66,82.84Z"/>',
    "plant": '<path d="M255.62,51.65a12,12,0,0,0-11.27-11.27c-53.27-3.13-96.2,13.36-114.84,44.14-12.14,20-12.56,44.17-1.46,67.3a75.14,75.14,0,0,0-12.28,23l-12.66-12.66c7.19-16.77,6.43-34.11-2.4-48.69C86.73,90.36,54.89,78,15.55,80.27A12,12,0,0,0,4.28,91.55C2,130.89,14.36,162.73,37.45,176.71a49.76,49.76,0,0,0,26,7.27,57.54,57.54,0,0,0,22.7-4.87L112,205v23a12,12,0,0,0,24,0V198.51a51.63,51.63,0,0,1,9.49-29.95,76.82,76.82,0,0,0,32.1,7.39,64.91,64.91,0,0,0,33.89-9.46C242.25,147.85,258.76,104.92,255.62,51.65ZM49.88,156.18c-13.19-8-21.18-27.46-21.83-52.13,24.67.65,44.14,8.64,52.13,21.83a26,26,0,0,1,3.63,17L72.48,131.51a12,12,0,0,0-17,17l11.34,11.34A26.27,26.27,0,0,1,49.88,156.18ZM199.05,146c-10.66,6.45-23,7.67-35.81,3.76l37.25-37.24a12,12,0,0,0-17-17l-37.25,37.24C142.37,120,143.59,107.61,150,97c12.7-21,42.65-33,81.32-33H232C232.14,103,220.14,133.18,199.05,146Z"/>',
    "play": '<path d="M234.49,111.07,90.41,22.94A20,20,0,0,0,60,39.87V216.13a20,20,0,0,0,30.41,16.93l144.08-88.13a19.82,19.82,0,0,0,0-33.86ZM84,208.85V47.15L216.16,128Z"/>',
    "pause": '<path d="M200,28H160a20,20,0,0,0-20,20V208a20,20,0,0,0,20,20h40a20,20,0,0,0,20-20V48A20,20,0,0,0,200,28Zm-4,176H164V52h32ZM96,28H56A20,20,0,0,0,36,48V208a20,20,0,0,0,20,20H96a20,20,0,0,0,20-20V48A20,20,0,0,0,96,28ZM92,204H60V52H92Z"/>',
    "prev": '<path d="M168.49,199.51a12,12,0,0,1-17,17l-80-80a12,12,0,0,1,0-17l80-80a12,12,0,0,1,17,17L97,128Z"/>',
    "next": '<path d="M184.49,136.49l-80,80a12,12,0,0,1-17-17L159,128,87.51,56.49a12,12,0,1,1,17-17l80,80A12,12,0,0,1,184.49,136.49Z"/>',
    "close": '<path d="M208.49,191.51a12,12,0,0,1-17,17L128,145,64.49,208.49a12,12,0,0,1-17-17L111,128,47.51,64.49a12,12,0,0,1,17-17L128,111l63.51-63.52a12,12,0,0,1,17,17L145,128Z"/>',
  };
  const NS = "http://www.w3.org/2000/svg";
  function svg(name, size) {
    const n = document.createElementNS(NS, "svg");
    n.setAttribute("viewBox", "0 0 256 256"); n.setAttribute("width", size || 24); n.setAttribute("height", size || 24);
    n.setAttribute("fill", "currentColor"); n.setAttribute("aria-hidden", "true");
    n.setAttribute("class", "i i-" + name); n.innerHTML = ICONS[name];
    return n;
  }
  const withIcon = (node, name, size) => { node.insertBefore(svg(name, size), node.firstChild); return node; };

  /** The likely range drawn against the freezing line: how much of tonight's range is below 0 degC.
   *  Each part says what it is on hover, keyboard focus or tap (invisible hit areas, wide enough for a thumb). */
  function ruler(fc, label, opt) {
    const lo = fc.tmin_lo_c, hi = fc.tmin_hi_c, o = opt || {};
    let a = o.domain ? o.domain[0] : Math.floor(Math.min(lo, 0) - 2), b = o.domain ? o.domain[1] : Math.ceil(Math.max(hi, 0) + 2);
    if ((b - a) % 2) b += 1;
    const W = 328, L = 14, R = 14, x = (v) => L + ((v - a) / (b - a)) * (W - L - R), step = b - a > 16 ? 4 : 2;
    let h = '<line class="axis" x1="' + L + '" y1="34" x2="' + (W - R) + '" y2="34"/>';
    for (let v = Math.ceil(a / step) * step; v <= b; v += step) {
      h += '<line class="tick" x1="' + x(v) + '" y1="34" x2="' + x(v) + '" y2="41"/>';
      if (v !== 0) h += '<text class="lbl" x="' + x(v) + '" y="56">' + (v < 0 ? "−" + -v : v) + "</text>";
    }
    const x0 = x(0), xl = x(lo), xh = x(hi);
    if (hi > 0) h += '<rect class="warm" x="' + Math.max(xl, x0) + '" y="23" width="' + (xh - Math.max(xl, x0)) + '" height="22"/>';
    if (lo < 0) h += '<rect class="cold" x="' + xl + '" y="23" width="' + (Math.min(xh, x0) - xl) + '" height="22"/>';
    h += '<line class="zero" x1="' + x0 + '" y1="14" x2="' + x0 + '" y2="48"/><text class="zlbl" x="' + x0 + '" y="10">0 °C</text>';
    if (o.observed != null) h += '<circle class="obs" cx="' + x(o.observed) + '" cy="34" r="10"/>';   // what was measured
    h += '<circle class="dot" cx="' + x(fc.tmin_c) + '" cy="34" r="' + (o.observed != null ? 5 : 7) + '"/>';
    const n = document.createElementNS(NS, "svg");
    n.setAttribute("viewBox", "0 0 " + W + " 60"); n.setAttribute("class", "ruler"); n.setAttribute("role", "group"); n.setAttribute("aria-label", label);
    n.innerHTML = h;
    const g0 = (v) => (v === 0 ? "0 °C" : grados1(v));
    const hit = (tag, attrs, tip) => {
      const s = document.createElementNS(NS, tag);
      for (const k in attrs) s.setAttribute(k, attrs[k]);
      s.setAttribute("class", "hit"); s.setAttribute("tabindex", "0"); s.setAttribute("role", "img"); s.setAttribute("aria-label", tip); s.setAttribute("data-tip", tip);
      n.appendChild(s);
    };
    if (lo < 0) hit("rect", { x: xl, y: 4, width: Math.min(xh, x0) - xl, height: 54 }, t("tip_cold", { lo: g0(lo), hi: g0(Math.min(hi, 0)), n: de10(fc.p_frost) }));
    if (hi > 0) hit("rect", { x: Math.max(xl, x0), y: 4, width: xh - Math.max(xl, x0), height: 54 }, t("tip_warm", { lo: g0(Math.max(lo, 0)), hi: g0(hi) }));
    hit("rect", { x: x0 - 11, y: 0, width: 22, height: 60 }, t("tip_zero"));
    if (o.observed != null) hit("circle", { cx: x(o.observed), cy: 34, r: 18 }, t("tip_obs", { t: grados1(o.observed) }));
    hit("circle", { cx: x(fc.tmin_c), cy: 34, r: o.observed != null ? 10 : 16 }, t("tip_calc", { t: grados1(fc.tmin_c) }));

    const wrap = el("div", "ruler-wrap"), tip = el("div", "ruler-tip");
    tip.hidden = true; tip.setAttribute("role", "tooltip");
    wrap.appendChild(n); wrap.appendChild(tip);
    const show = (s) => {
      tip.textContent = s.getAttribute("data-tip"); tip.style.left = "0px"; tip.hidden = false;
      const wb = wrap.getBoundingClientRect(), sb = s.getBoundingClientRect(), cx = sb.left + sb.width / 2 - wb.left;
      const left = Math.max(0, Math.min(wb.width - tip.offsetWidth, cx - tip.offsetWidth / 2));   // never past the edges
      tip.style.left = left + "px"; tip.style.setProperty("--ax", Math.max(10, Math.min(tip.offsetWidth - 10, cx - left)) + "px");
    };
    const hide = () => { tip.hidden = true; };
    n.querySelectorAll(".hit").forEach((s) => {
      s.addEventListener("mouseenter", () => show(s)); s.addEventListener("mouseleave", hide);
      s.addEventListener("focus", () => show(s)); s.addEventListener("blur", hide);
      s.addEventListener("click", () => show(s));
    });
    wrap.showTip = (which) => { const s = n.querySelectorAll(".hit"); if (s.length) show(which === "calc" ? s[s.length - 1] : s[0]); };
    return wrap;
  }
  const hideTips = () => document.querySelectorAll(".ruler-tip").forEach((n) => { n.hidden = true; });

  // ------------------------------------------------------------------ storage (always guarded: private mode, quota)
  const store = {
    get(k, d) { try { const v = localStorage.getItem("helada." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("helada." + k, JSON.stringify(v)); return true; } catch (e) { return false; } },
  };

  const urlLang = new URLSearchParams(location.search).get("lang");
  const S = {
    lang: urlLang === "en" || urlLang === "es" ? urlLang : store.get("lang", "es"),
    theme: store.get("theme", null),   // "light" | "dark" | null = follow the phone
    mode: location.hash === "#demo" ? "demo" : store.get("mode", "real"),
    parcelId: store.get("parcel", null),
    tab: "noche", loaded: {}, pack: null, models: null, revealed: false, busy: false, lastError: null,
  };

  // ------------------------------------------------------------------ words
  // Spanish is the product. English is a reading aid for reviewers: same screens, same numbers, other words.
  const fill = (s, vars) => (vars ? s.replace(/\{([a-z0-9_]+)\}/g, (m, k) => (k in vars ? vars[k] : m)) : s);
  function tIn(lang, key, vars) { const tb = I18N[lang] || {}; const s = tb[key] != null ? tb[key] : I18N.es[key]; return s == null ? key : fill(s, vars); }
  const t = (key, vars) => tIn(S.lang, key, vars);
  const dmy = (iso) => { const [y, m, d] = iso.split("-").map(Number); return { y, m, d, wd: new Date(Date.UTC(y, m - 1, d)).getUTCDay() }; };
  const fechaLargaIn = (lang, iso) => { const x = dmy(iso); return tIn(lang, "date_long", { wd: tIn(lang, "days").split(",")[x.wd], d: x.d, month: tIn(lang, "months").split(",")[x.m - 1] }); };
  const fechaCortaIn = (lang, iso) => { const x = dmy(iso); return tIn(lang, "date_short", { d: x.d, month: tIn(lang, "months").split(",")[x.m - 1] }); };
  const fechaLarga = (iso) => fechaLargaIn(S.lang, iso);
  const fechaCorta = (iso) => fechaCortaIn(S.lang, iso);
  const noche = (ev) => {
    const a = dmy(ev), nx = HM.addDays(ev, 1), b = dmy(nx), days = t("days").split(",");
    return a.m === b.m ? t("night_one_month", { wd1: days[a.wd], d1: a.d, wd2: days[b.wd], d2: b.d, month: t("months").split(",")[a.m - 1] })
                       : t("night_two_months", { a: fechaLarga(ev), b: fechaLarga(nx) });
  };
  const grados = (x) => { const n = Math.round(x); return (n < 0 ? "−" + Math.abs(n) : String(n)) + " °C"; };
  const grados1 = (x) => (x < 0 ? "−" : "") + Math.abs(x).toFixed(1) + " °C";
  const gradosVoz = (x) => {
    const n = Math.round(x), m = Math.abs(n);
    if (S.lang === "en") return n === 0 ? "zero degrees" : (m === 1 ? "one degree" : m + " degrees") + (n < 0 ? " below zero" : "");
    if (n === 0) return "cero grados";
    if (m === 1) return n < 0 ? "un grado bajo cero" : "un grado";
    return m + " grados" + (n < 0 ? " bajo cero" : "");
  };
  const de10 = (p) => Math.max(1, Math.min(10, Math.round(p * 10)));
  const localISO = (d) => d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  const cropName = (crop) => { const k = "crop_" + crop; return I18N.es[k] ? t(k) : t("crop_other"); };
  const ownerName = (p) => p.owner_name.replace(/\((fictici[oa])\)/, (m, w) => t(w === "ficticia" ? "fictional_f" : "fictional_m"));
  // The server reads Spanish: these two never follow the screen language.
  const CROP_ES = { maiz_temporal: "el maíz", papa: "la papa", avena: "la avena", haba: "el haba" };
  const CAUSA_ES = { helada: "helada", granizo: "granizo", sequia: "sequía", inundacion: "inundación" };

  /** Stage name + actions to show for a crop and month: the fixed advisory table, in the screen language. */
  function stageView(crop, month) {
    const adv = S.pack.advisory, crops = adv.crops;
    let ck = crops[crop] ? crop : "_default", i = crops[ck].stages.findIndex((st) => st.months.indexOf(month) >= 0);
    if (i < 0) { ck = "_default"; i = 0; }
    const es = crops[ck].stages[i];
    const en = S.lang === "en" && adv.en && adv.en.crops[ck] ? adv.en.crops[ck].stages[i] : null;
    return { stage: en ? en.stage : es.stage, actions: es.actions.map((x, j) => (en && en.actions[j]) || x.text) };
  }

  // ------------------------------------------------------------------ pack + model files
  async function gunzipText(buf) {
    const b = new Uint8Array(buf);
    if (b[0] !== 0x1f || b[1] !== 0x8b) return new TextDecoder().decode(b);   // the server already decoded it
    if (typeof DecompressionStream === "undefined") throw new Error(t("err_browser"));
    return new Response(new Blob([b]).stream().pipeThrough(new DecompressionStream("gzip"))).text();
  }

  async function load(mode) {
    if (S.loaded[mode]) return S.loaded[mode];
    const pack = await (await fetch(mode === "demo" ? "data/pack-demo.json" : "data/pack.json")).json();
    const models = {};
    await Promise.all(Object.entries(pack.models.files).map(async ([k, fn]) => {
      const r = await fetch(pack.models.dir + fn);
      if (!r.ok) throw new Error(t("err_model_file", { file: fn }));
      models[k] = HM.parseLightGBM(await gunzipText(await r.arrayBuffer()));
    }));
    return (S.loaded[mode] = { pack, models });
  }

  const parcel = () => S.pack.parcels.find((p) => p.parcel_id === S.parcelId) || S.pack.parcels[0];
  const isDemo = () => S.mode === "demo";

  // ------------------------------------------------------------------ forecast cache
  // helada.fx = { [parcel_id]: { [eveningDate]: { fd: {...}, fetched: "YYYY-MM-DD", at: epoch_ms } } }
  const fxAll = () => store.get("fx", {});
  function fxNight(pid, ev) { const n = (fxAll()[pid] || {})[ev]; return n || null; }
  function fxLastFetch() { let t = 0; const all = fxAll(); for (const pid in all) for (const ev in all[pid]) t = Math.max(t, all[pid][ev].at || 0); return t; }

  async function fetchForecasts() {
    const ps = S.loaded.real ? S.loaded.real.pack.parcels : S.pack.parcels;
    const src = S.pack.meta.forecast_source;
    const q = new URLSearchParams({
      latitude: ps.map((p) => p.lat.toFixed(5)).join(","), longitude: ps.map((p) => p.lon.toFixed(5)).join(","),
      hourly: "temperature_2m,dew_point_2m,cloud_cover,wind_speed_10m", timezone: "America/Mexico_City",
      models: src, past_days: "2", forecast_days: "8",
    });
    const r = await fetch("https://api.open-meteo.com/v1/forecast?" + q.toString(), { cache: "no-store" });
    if (!r.ok) throw new Error("forecast service: " + r.status);
    let j = await r.json();
    if (!Array.isArray(j)) j = [j];
    const today = localISO(new Date()), now = Date.now(), all = fxAll(), limit = HM.addDays(today, -45);
    ps.forEach((p, i) => {
      const nights = HM.nightsFromHourly(j[i].hourly, j[i].elevation);
      const mine = all[p.parcel_id] || (all[p.parcel_id] = {});
      for (const ev in nights) {
        // a past night keeps the forecast that was on the phone when it happened (that is what the model saw)
        if (ev < HM.addDays(today, -1) && mine[ev]) continue;
        mine[ev] = { fd: nights[ev], fetched: today, at: now };
      }
      for (const ev in mine) if (ev < limit) delete mine[ev];
    });
    store.set("fx", all);
  }

  // ------------------------------------------------------------------ thermometer readings + queue
  const lecturas = (pid) => store.get("lecturas." + pid, []);
  const loggerNights = (pid) => lecturas(pid).filter((x) => x.forecast).map((x) => ({ date: x.date, tmin_c: x.tmin_c, forecast: x.forecast }));
  const cola = () => store.get("cola", []);

  function encolar(item) {
    const q = cola();
    q.push(Object.assign({ id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), created_at: new Date().toISOString(), sent_at: null }, item));
    store.set("cola", q);
  }

  /** Store and forward: send what is waiting. Anything that fails stays in the queue for the next time.
   *  How each try ended is written on the item (cola.js), so the screen can say it: sent, refused, or no server. */
  async function enviarCola() {
    if (!navigator.onLine) return;
    const q = cola(); let changed = false;
    for (const it of q) {
      if (it.sent_at || it.tour) continue;   // the demo's own report is never sent
      let status = null;
      try {
        const fd = new FormData();
        let url;
        if (it.kind === "dano") { url = "/api/sim/inbound"; fd.append("phone", it.phone); fd.append("text", it.text); }
        else { url = "/api/logger/" + encodeURIComponent(it.parcel_id) + "/readings"; fd.append("text", it.text); fd.append("date_is", "evening"); }
        status = (await fetch(url, { method: "POST", body: fd })).status;
      } catch (e) { /* the request never got out: it waits for signal */ }
      Q.anotar(it, status, new Date().toISOString()); changed = true;
    }
    if (changed) store.set("cola", q);
  }

  /** One line under a reading or a report: where it is now. Words and an icon, never colour alone. */
  function estadoLinea(it, pre) {
    const s = Q.estado(it);
    if (s === "sent") return withIcon(el("span", "st sent", t(pre + "_sent")), "check", 20);
    if (s === "wait") return withIcon(el("span", "st wait", t(pre + "_wait")), "clock", 20);
    if (s === "failed") return withIcon(el("span", "st fail", t("send_failed")), "unsure", 20);
    return el("span", "st local", t("only_here"));
  }
  /** «Enviar ahora», only where something of that kind is waiting and there is signal. */
  function enviarAhora(box, kind) {
    if (!Q.pendientes(cola(), kind, parcel().parcel_id) || !navigator.onLine) return;
    const b = el("button", "btn", t("dano_send_now")); b.type = "button"; b.addEventListener("click", () => enviarCola().then(render)); box.appendChild(b);
  }

  // ------------------------------------------------------------------ tonight
  function tonightView() {
    const p = parcel(), pack = S.pack;
    let date, entry;
    if (isDemo()) {
      date = pack.demo.night_date;
      const d = pack.demo.parcels[p.parcel_id];
      entry = d ? { fd: d.forecast, lead: 0, at: null, observed: d.observed_tmin_c, station: d.station } : null;
    } else {
      date = HM.tonight(new Date());
      const n = fxNight(p.parcel_id, date);
      entry = n ? { fd: n.fd, lead: Math.max(0, HM.daysBetween(n.fetched, date)), at: n.at } : null;
    }
    let fc = null;
    if (entry) {
      try { fc = HM.predictNight(pack, S.models, p, date, entry.fd, isDemo() ? [] : loggerNights(p.parcel_id)); }
      catch (e) { fc = null; S.lastError = String(e.message || e); }
    }
    return { p, date, entry, fc, v: HM.judge(pack, fc, { leadDays: entry ? entry.lead : 0 }) };
  }

  const ICON = { riesgo: "frost", no_seguro: "unsure", sin_riesgo: "ok" };
  const contactName = () => (S.pack.contact.fictional ? t("contact_name") : S.pack.contact.name);

  function baseLine(fc, p) {
    if (fc.support === "station-anchored") return t("base_station", { name: p.station.name, km: p.station.km.toFixed(1) });
    if (fc.support.startsWith("logger-anchored")) {
      const k = fc.drivers.logger_nights, mae = fc.drivers.logger_expected_mae_c.toFixed(1);
      return k === 1 ? t("base_logger_one", { mae }) : t("base_logger", { k, mae });
    }
    return t("base_transfer");
  }

  function spoken(view) {
    const { fc, v, date } = view;
    const parts = [t("say_head", { title: t("st_" + v.state), noche: noche(date) })];
    if (fc) parts.push(t("say_range", { t: gradosVoz(fc.tmin_c), lo: gradosVoz(fc.tmin_lo_c), hi: gradosVoz(fc.tmin_hi_c), n: de10(fc.p_frost) }));
    if (v.state === "no_seguro") parts.push(v.reasons.map((r) => t("r_" + r)).join(" ") + " " + t("say_ask"));
    if (v.state === "riesgo") parts.push(stageView(view.p.crop, dmy(date).m).actions.slice(0, 2).join(" "));
    parts.push(t("say_decide"));
    return parts.join(" ");
  }

  // ------------------------------------------------------------------ read aloud: listen, pause, continue
  const SP = { state: "idle", u: null };   // idle | playing | paused
  function paintSay() {
    const b = $("sayBtn"); if (!b) return;
    const k = SP.state === "playing" ? ["btn_pause", "pause"] : SP.state === "paused" ? ["btn_resume", "play"] : ["btn_listen", "speaker"];
    b.textContent = t(k[0]); withIcon(b, k[1]);
  }
  function stopSpeech() {
    if (!("speechSynthesis" in window)) return;
    SP.u = null; SP.state = "idle";
    try { speechSynthesis.cancel(); } catch (e) { /* no voice on this phone */ }
    paintSay();
  }
  function toggleSpeech(text) {
    const ss = window.speechSynthesis;
    try {
      if (SP.state === "playing") {
        const u = SP.u;
        ss.pause(); SP.state = "paused"; paintSay();
        // some phones (Chrome on Android) ignore pause(): then stop, so the button never lies
        setTimeout(() => { if (SP.u === u && SP.state === "paused" && !ss.paused) stopSpeech(); }, 300);
        return;
      }
      if (SP.state === "paused") { ss.resume(); SP.state = "playing"; paintSay(); return; }
      ss.cancel(); if (ss.paused) ss.resume();
      const u = new SpeechSynthesisUtterance(text); u.lang = t("voice"); u.rate = 0.95;
      const done = () => { if (SP.u === u) { SP.u = null; SP.state = "idle"; paintSay(); } };
      u.onend = done; u.onerror = done;
      SP.u = u; SP.state = "playing"; ss.speak(u); paintSay();
    } catch (e) { SP.u = null; SP.state = "idle"; paintSay(); }
  }

  function renderNoche() {
    const root = $("noche"); root.textContent = "";
    const view = tonightView(), { p, date, entry, fc, v } = view, pack = S.pack;
    const block = (cls) => { const n = el("div", "block" + (cls ? " " + cls : "")); root.appendChild(n); return n; };

    // the verdict band
    const st = el("div", "state " + v.state);
    st.appendChild(svg(ICON[v.state], 44));
    const ht = el("div"); ht.appendChild(el("div", "state-title", t("st_" + v.state))); ht.appendChild(el("div", "state-night", t("night_of", { noche: noche(date) })));
    st.appendChild(ht); root.appendChild(st);

    // the number, and where the likely range sits against freezing
    if (fc) {
      const r = el("div", "reading");
      const g = grados(fc.tmin_c).split(" "), z = grados(fc.grid_tmin_c).split(" ");
      const likely = t("likely", { lo: grados(fc.tmin_lo_c), hi: grados(fc.tmin_hi_c) });
      // the point of the tool: what the zone forecast says, and what this plot will do
      const vs = el("div", "vs");
      const cell = (cls, label, parts) => { const c = el("div", cls); c.appendChild(el("p", "vs-l", label)); const tp = el("p", "temp"); tp.appendChild(el("span", "num", parts[0])); tp.appendChild(el("span", "unit", parts.slice(1).join(" "))); c.appendChild(tp); return c; };
      vs.appendChild(cell("zone", t("cmp_zone"), z)); vs.appendChild(cell("plot", t("cmp_plot"), g));
      vs.dataset.tour = "compare";
      r.appendChild(vs);
      const gap = Math.round(fc.tmin_c) - Math.round(fc.grid_tmin_c);
      r.appendChild(el("p", "gap", gap === 0 ? t("cmp_same") : t(gap < 0 ? "cmp_colder" : "cmp_warmer", { d: Math.abs(gap) })));
      r.appendChild(el("p", "fine", t(fc.support === "station-anchored" ? "why_station" : fc.support.startsWith("logger") ? "why_logger" : "why_transfer")));
      r.appendChild(ruler(fc, likely));
      r.appendChild(el("p", null, likely));
      r.appendChild(el("p", null, t("prob", { n: de10(fc.p_frost) })));
      root.appendChild(r);
    }

    if (v.state === "no_seguro") {
      const c = block(fc ? "" : "first"); c.dataset.tour = "ask";
      const ul = el("ul", "reasons"); v.reasons.forEach((x) => ul.appendChild(el("li", null, t("r_" + x)))); c.appendChild(ul);
      c.appendChild(el("h2", null, t("ask_title")));
      c.appendChild(el("p", null, contactName()));
      const a = el("a", "call", pack.contact.phone); a.href = "tel:" + pack.contact.phone.replace(/\s+/g, ""); withIcon(a, "phone", 26); c.appendChild(a);
      if (fc && v.level === "watch") c.appendChild(el("p", null, t("watch_tip")));
    }
    if (v.state === "riesgo") {
      const stg = stageView(p.crop, dmy(date).m);
      const c = block(); c.dataset.tour = "do";
      c.appendChild(withIcon(el("h2", "with-ico", t("do_title")), "plant", 26));
      c.appendChild(el("p", "fine", t("crop_stage", { crop: cropName(p.crop), stage: stg.stage })));
      const ol = el("ol", "steps"); stg.actions.slice(0, 2).forEach((x) => ol.appendChild(el("li", null, x))); c.appendChild(ol);
      if (!pack.advisory.signed) c.appendChild(el("span", "tag", S.lang === "es" ? pack.advisory.unsigned_label : t("unsigned")));
      if (t("advisory_note")) c.appendChild(el("p", "cap", t("advisory_note")));
      const rb = withIcon(el("button", "btn", t("btn_report")), "report"); rb.type = "button";
      rb.addEventListener("click", () => { S.tab = "dano"; render(); window.scrollTo(0, 0); });
      c.appendChild(rb);
    }

    // whose decision it is, read aloud, and how fresh the forecast is
    const act = block(root.children.length === 1 ? "first" : "");
    act.appendChild(el("p", "decide", t("decide")));
    const say = el("button", "btn"); say.type = "button"; say.id = "sayBtn";
    say.disabled = !("speechSynthesis" in window);
    say.addEventListener("click", () => toggleSpeech(spoken(view)));
    act.appendChild(say); paintSay();
    if (isDemo()) {
      const b = withIcon(el("button", "btn", t("btn_reveal")), "eye"); b.type = "button"; b.disabled = !entry || entry.observed == null;
      b.addEventListener("click", () => { S.revealed = true; renderNoche(); });
      act.appendChild(b);
    } else {
      let msg;
      if (!entry) msg = t(navigator.onLine ? "fx_none_online" : "fx_none_offline");
      else msg = (navigator.onLine ? "" : t("fx_offline_prefix") + " ") + (entry.lead === 0 ? t("fx_age_today") : entry.lead === 1 ? t("fx_age_yesterday") : t("fx_age_days", { n: entry.lead }));
      act.appendChild(el("p", "fine", msg));
      if (S.lastError) act.appendChild(el("p", "err", S.lastError));
      const b = withIcon(el("button", "btn", t(S.busy ? "btn_updating" : "btn_update")), "refresh"); b.type = "button"; b.disabled = S.busy || !navigator.onLine;
      b.addEventListener("click", actualizar);
      act.appendChild(b);
    }

    if (isDemo() && S.revealed && entry && entry.observed != null) {
      const c = block(); c.dataset.tour = "happened";
      c.appendChild(el("h2", null, t("happened_title")));
      c.appendChild(el("p", "decide", t("happened_big", { station: entry.station || p.station.name, t: grados1(entry.observed) })));
      c.appendChild(el("p", "fine", t("happened_fine", { date: fechaCorta(pack.demo.observation_date) })));
    }

    if (fc) {
      const d = el("details", "block"); d.dataset.tour = "how"; const sm = el("summary", null, t("how_title")); sm.appendChild(svg("chevron")); d.appendChild(sm);
      const inner = el("div", "inner");
      const dl = el("dl", "kv");
      const kv = (k, val, hl) => { dl.appendChild(el("dt", hl ? "hl" : null, k)); dl.appendChild(el("dd", hl ? "hl" : null, val)); };
      kv(t("how_zone"), grados1(fc.grid_tmin_c));
      kv(t("how_adjust"), (fc.drivers.correction_c > 0 ? "+" : "−") + Math.abs(fc.drivers.correction_c).toFixed(1) + " °C");
      kv(t("how_parcel"), grados1(fc.tmin_c), true);
      kv(t("how_clear"), Math.round(fc.drivers.clear_sky * 100) + " %");
      kv(t("how_wind"), fc.drivers.wind_kmh.toFixed(0) + " km/h");
      inner.appendChild(dl);
      inner.appendChild(el("p", "cap", t("how_base", { base: baseLine(fc, p) })));
      inner.appendChild(el("p", "cap", t("how_fine", { kb: Math.round(pack.models.total_bytes / 1000), nights: pack.meta.n_nights.toLocaleString(t("locale")), stations: pack.meta.n_stations })));
      d.appendChild(inner); root.appendChild(d);
    }
    $("loading").hidden = true; root.hidden = false;
  }

  async function actualizar() {
    if (S.busy || !navigator.onLine) return;
    S.busy = true; S.lastError = null; renderNoche();
    try { await fetchForecasts(); await enviarCola(); }
    catch (e) { S.lastError = t("fx_error"); }
    S.busy = false; render();
  }

  // ------------------------------------------------------------------ day strip
  /** The last `n` days as big cards, most recent first. The date field stays as the value store (the forms read
   *  it as before); «Otra fecha» shows it for an older day. */
  function dayStrip(box, input, n) {
    const today = localISO(new Date()), wds = t("days_short").split(","), mos = t("months_short").split(",");
    const other = input.dataset.other === "1", keep = box.scrollLeft;
    box.textContent = "";
    const card = (pressed, label) => { const b = el("button", "day"); b.type = "button"; b.setAttribute("aria-pressed", String(pressed)); if (label) b.setAttribute("aria-label", label); box.appendChild(b); return b; };
    for (let i = 0; i < n; i++) {
      const iso = HM.addDays(today, -i), x = dmy(iso);
      const b = card(!other && input.value === iso, (i === 0 ? t("day_today") + ", " : i === 1 ? t("day_yesterday") + ", " : "") + fechaLarga(iso));
      b.appendChild(el("span", "day-wd", wds[x.wd])); b.appendChild(el("span", "day-n", String(x.d)));
      b.appendChild(el("span", i < 2 ? "day-m rel" : "day-m", i === 0 ? t("day_today") : i === 1 ? t("day_yesterday") : mos[x.m - 1]));
      b.addEventListener("click", () => { input.dataset.other = ""; input.value = iso; dayStrip(box, input, n); });
    }
    const o = card(other); o.classList.add("other"); o.appendChild(el("span", "day-o", t("day_other")));
    o.addEventListener("click", () => { input.dataset.other = "1"; dayStrip(box, input, n); try { input.focus(); if (input.showPicker) input.showPicker(); } catch (e) { /* the field is on screen anyway */ } });
    input.hidden = !other;
    box.scrollLeft = keep;
  }

  // ------------------------------------------------------------------ thermometer
  function renderTermo() {
    const p = parcel(), list = lecturas(p.parcel_id), k = list.filter((x) => x.forecast).length;
    const lv = HM.levelFor(S.pack.logger.levels, k);
    const mae = lv.mae_c.toFixed(1), own = !isDemo() && !p.anchored;
    $("termoLead").textContent = isDemo() ? t("termo_demo_lead", { name: S.pack.demo.logger.site.name }) : p.anchored ? t("termo_anchored", { name: p.station.name, km: p.station.km.toFixed(1) }) : t("termo_lead");
    $("termoStat").hidden = !own;
    $("termoStat").textContent = own ? (k === 0 ? t("termo_k0", { mae }) : k === 1 ? t("termo_k_one", { mae }) : t("termo_k", { k, mae })) : "";
    $("termoForm").hidden = isDemo();
    if (!$("termoFecha").value) $("termoFecha").value = localISO(new Date());
    $("termoFecha").max = localISO(new Date());
    dayStrip($("termoDias"), $("termoFecha"), 7);
    const box = $("termoList"); box.textContent = ""; box.className = "";
    if (isDemo()) { renderTermoDemo(box); return; }
    if (!list.length) return;
    box.className = "block";
    const wrap = el("div", "rows"), q = cola();
    list.slice().sort((a, b) => (a.date < b.date ? 1 : -1)).slice(0, 14).forEach((x) => {
      const it = el("div", "item");
      it.appendChild(el("b", null, grados1(x.tmin_c).replace(" °C", "°")));
      it.appendChild(el("span", null, t("termo_item", { date: fechaLarga(HM.addDays(x.date, 1)) })));
      if (!x.forecast) it.appendChild(el("span", "note", t("termo_nofx")));
      const sent = Q.deLectura(q, p.parcel_id, x.date);
      if (sent) it.appendChild(estadoLinea(sent, "lect"));
      wrap.appendChild(it);
    });
    box.appendChild(wrap);
    enviarAhora(box, "lectura");
  }

  /** Demo: a real station the model never saw stands in for a plot's thermometer. Tap how many nights have been
   *  written down and see, computed on this phone, what the page would have said for the replay night. */
  function renderTermoDemo(box) {
    const L = S.pack.demo.logger, pack = S.pack;
    if (S.demoK == null || L.steps.indexOf(S.demoK) < 0) S.demoK = 0;
    const calc = (k) => HM.predictNight(pack, S.models, L.site, L.night_date, L.forecast, L.nights.slice(0, k));
    const all = L.steps.map(calc), fc = all[L.steps.indexOf(S.demoK)], v = HM.judge(pack, fc, { leadDays: 0 });
    const lows = all.map((f) => f.tmin_lo_c).concat([0, L.observed_tmin_c]), highs = all.map((f) => f.tmin_hi_c).concat([0, L.observed_tmin_c]);
    const domain = [Math.floor(Math.min.apply(null, lows) - 1), Math.ceil(Math.max.apply(null, highs) + 1)];   // same scale at every step
    const lv = HM.levelFor(pack.logger.levels, S.demoK), mae = lv.mae_c.toFixed(1), k = S.demoK;

    const pick = el("div", "block"); pick.dataset.tour = "termo-pick";
    pick.appendChild(el("p", "q", t("termo_demo_nights")));
    const row = el("div", "choices five");
    L.steps.forEach((n) => {
      const b = el("button", "choice", String(n)); b.type = "button"; b.setAttribute("aria-pressed", String(n === k));
      b.addEventListener("click", () => { S.demoK = n; renderTermo(); });
      row.appendChild(b);
    });
    pick.appendChild(row);
    pick.appendChild(el("p", "stat", k === 0 ? t("termo_k0", { mae }) : k === 1 ? t("termo_k_one", { mae }) : t("termo_k", { k, mae })));
    box.appendChild(pick);

    const res = el("div", "block"); res.dataset.tour = "termo-res";
    res.appendChild(el("p", "fine", t("termo_demo_night", { noche: noche(L.night_date) })));
    res.appendChild(withIcon(el("p", "verdict " + v.state, t("st_" + v.state)), ICON[v.state], 30));
    const parts = t("can_drop", { t: "\u0001" }).split("\u0001"), g = grados1(fc.tmin_c).split(" ");
    const likely = t("likely", { lo: grados(fc.tmin_lo_c), hi: grados(fc.tmin_hi_c) });
    if (parts[0].trim()) res.appendChild(el("p", null, parts[0].trim()));
    const tp = el("p", "temp"); tp.appendChild(el("span", "num", g[0])); tp.appendChild(el("span", "unit", g.slice(1).join(" "))); res.appendChild(tp);
    res.appendChild(ruler(fc, likely, { domain, observed: L.observed_tmin_c }));
    res.appendChild(el("p", "cap", t("termo_demo_legend")));
    res.appendChild(el("p", null, likely));
    res.appendChild(el("p", null, t("prob", { n: de10(fc.p_frost) })));
    res.appendChild(el("p", "decide", t("termo_demo_observed", { t: grados1(L.observed_tmin_c) })));
    box.appendChild(res);

    if (k > 0) {
      const last = el("div", "block");
      last.appendChild(el("h2", null, t("termo_demo_last")));
      const wrap = el("div", "rows");
      L.nights.slice(0, k).slice(-4).reverse().forEach((x) => {
        const it = el("div", "item");
        it.appendChild(el("b", null, grados1(x.tmin_c).replace(" °C", "°")));
        it.appendChild(el("span", null, t("termo_item", { date: fechaLarga(HM.addDays(x.date, 1)) })));
        wrap.appendChild(it);
      });
      last.appendChild(wrap); box.appendChild(last);
    }
    const note = el("div", "block");
    note.appendChild(el("p", "cap", t("termo_demo_note")));
    box.appendChild(note);
  }

  function onTermo(ev) {
    ev.preventDefault();
    const err = $("termoErr"); err.hidden = true;
    const p = parcel(), morning = $("termoFecha").value;
    let temp = parseFloat(String($("termoTemp").value).replace(",", ".").replace("−", "-"));
    if ($("termoSigno").getAttribute("aria-pressed") === "true") temp = -Math.abs(temp);
    if (!morning || morning > localISO(new Date())) { err.textContent = t("termo_err_date"); err.hidden = false; return; }
    if (Number.isNaN(temp) || temp < -25 || temp > 30) { err.textContent = t("termo_err_temp"); err.hidden = false; return; }
    const date = HM.addDays(morning, -1), n = fxNight(p.parcel_id, date);
    const list = lecturas(p.parcel_id).filter((x) => x.date !== date);   // a later reading for the same night replaces it
    list.push({ date, tmin_c: temp, forecast: n ? n.fd : null, at: Date.now() });
    if (!store.set("lecturas." + p.parcel_id, list)) { err.textContent = t("err_save"); err.hidden = false; return; }
    store.set("cola", Q.sinLecturaPendiente(cola(), p.parcel_id, date));
    encolar({ kind: "lectura", parcel_id: p.parcel_id, fecha: date, text: date + "," + temp });
    $("termoTemp").value = ""; $("termoSigno").setAttribute("aria-pressed", "false");
    render(); enviarCola().then(render);
    if (temp <= 0 && !TOUR.on) preguntarDano(temp, morning);
  }

  /** A reading is not a damage report. After a reading at or below freezing, ask; «Sí» only opens the damage form
   *  with what is already known (frost, that morning). Nothing is sent until the farmer saves the report there. */
  function preguntarDano(temp, morning) {
    const dlg = $("lecturaDlg");
    $("lecturaTxt").textContent = t("lect_saved", { t: grados1(temp), date: fechaLarga(morning) });
    dlg.returnValue = "";
    dlg.onclose = () => {
      if (dlg.returnValue !== "si") return;
      const f = $("danoFecha");
      D.causa = "helada"; f.value = morning; f.dataset.other = morning < HM.addDays(localISO(new Date()), -9) ? "1" : "";
      S.tab = "dano"; render(); window.scrollTo(0, 0);
    };
    dlg.showModal();
  }

  // ------------------------------------------------------------------ damage report
  const CAUSAS = ["helada", "granizo", "sequia", "inundacion"];
  const D = { causa: "helada", area: "media" };

  const CAUSE_ICON = { helada: "frost", granizo: "hail", sequia: "sun", inundacion: "flood" };
  function choice(box, items, key) {
    box.textContent = "";
    items.forEach(([val, label]) => {
      const b = el("button", "choice", label); b.type = "button"; b.setAttribute("aria-pressed", String(D[key] === val));
      if (key === "causa") { b.classList.add("pic", "c-" + val); withIcon(b, CAUSE_ICON[val], 30); }
      b.addEventListener("click", () => { D[key] = val; renderDano(); });
      box.appendChild(b);
    });
  }

  function renderDano() {
    const p = parcel();
    choice($("danoCausa"), CAUSAS.map((k) => [k, t("cause_" + k)]), "causa");
    choice($("danoArea"), ["media", "una", "toda", "otra"].map((k) => [k, t("area_" + k)]), "area");
    $("danoAreaOtra").hidden = D.area !== "otra";
    if (!$("danoFecha").value) $("danoFecha").value = localISO(new Date());
    $("danoFecha").max = localISO(new Date());
    dayStrip($("danoDias"), $("danoFecha"), 10);   // 10 days: the program's deadline to notify the Delegación
    const box = $("danoList"); box.textContent = ""; box.className = "";
    const mine = cola().filter((x) => x.kind === "dano" && x.parcel_id === p.parcel_id).reverse();
    if (!mine.length) return;
    box.className = "block";
    const wrap = el("div", "rows");
    mine.slice(0, 10).forEach((x) => {
      const it = el("div", "item wide");
      it.appendChild(el("b", null, x.causa ? t("dano_item", { cause: t("cause_" + x.causa), date: fechaCorta(x.fecha) }) : x.resumen));
      it.appendChild(estadoLinea(x, "dano"));
      wrap.appendChild(it);
    });
    box.appendChild(wrap);
    enviarAhora(box, "dano");
  }

  function onDano(ev) {
    ev.preventDefault();
    const err = $("danoErr"); err.hidden = true;
    const p = parcel(), fecha = $("danoFecha").value;
    if (!fecha || fecha > localISO(new Date())) { err.textContent = t("dano_err_date"); err.hidden = false; return; }
    let area;
    if (D.area === "media") area = "Fue media hectárea"; else if (D.area === "una") area = "Fue 1 hectárea"; else if (D.area === "toda") area = "Fue toda la parcela";
    else {
      const ha = parseFloat(String($("danoAreaOtra").value).replace(",", "."));
      if (Number.isNaN(ha) || ha <= 0 || ha > 500) { err.textContent = t("dano_err_area"); err.hidden = false; return; }
      area = "Fueron " + ha + " hectáreas";
    }
    const nota = $("danoNota").value.trim();
    // A plain Spanish sentence with an explicit date: the server's rules read it the same today or in a week.
    const text = "Se me dañó " + (CROP_ES[p.crop] || "el cultivo") + " por " + CAUSA_ES[D.causa] + ". " + area + ". Pasó el " + fechaCortaIn("es", fecha) + "." + (nota ? " " + nota : "");
    encolar({ kind: "dano", parcel_id: p.parcel_id, phone: p.phone, text, causa: D.causa, fecha });
    $("danoNota").value = ""; $("danoAreaOtra").value = "";
    render(); enviarCola().then(render);
  }

  // ------------------------------------------------------------------ shell
  /** Static texts of index.html: every element with data-t (text) or data-t-ph (placeholder). */
  function applyStatic() {
    document.documentElement.lang = S.lang;
    document.title = t("title");
    document.querySelectorAll("[data-t]").forEach((n) => { n.textContent = t(n.dataset.t); });
    document.querySelectorAll("[data-t-ph]").forEach((n) => { n.placeholder = t(n.dataset.tPh); });
    const other = S.lang === "es" ? "EN" : "ES";
    $("langBtn").textContent = other; $("langBtn").setAttribute("aria-label", t("switch_lang"));
    const bar = $("reviewerBar"); bar.hidden = !t("reviewer_bar"); bar.textContent = t("reviewer_bar");
    document.querySelectorAll("[data-ico]").forEach((n) => { if (!n.firstChild) n.appendChild(svg(n.dataset.ico, +n.dataset.size || 24)); });
    paintTourBtn();
    applyTheme();
  }

  function renderShell() {
    const p = parcel();
    applyStatic();
    $("parcelId").textContent = t("parcel", { id: p.parcel_id });
    $("parcelWhere").textContent = p.owner_name.replace(/\s*\(fictici[oa]\)/, "") + ", " + p.municipality;
    const net = $("net"); net.textContent = ""; net.classList.toggle("off", !navigator.onLine);
    net.appendChild(svg(navigator.onLine ? "signal" : "signal-off", 22));
    net.appendChild(el("span", "txt", t(navigator.onLine ? "net_on" : "net_off")));
    const bar = $("demoBar"); bar.hidden = !isDemo();
    if (isDemo()) bar.textContent = t("demo_bar", { label: t("demo_label") });
    $("notice").hidden = bar.hidden && $("reviewerBar").hidden;
    // each tab counts only what waits on that tab, for this plot: a reading never puts a number on «Daño»
    const q = cola(), badge = (id, n) => { $(id).hidden = !n; $(id).textContent = String(n); };
    badge("colaBadge", Q.pendientes(q, "dano", p.parcel_id));
    badge("termoBadge", isDemo() ? 0 : Q.pendientes(q, "lectura", p.parcel_id));   // the demo shows no readings of the plot
    document.querySelectorAll(".tabs button").forEach((b) => { const on = b.dataset.tab === S.tab; if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
    ["noche", "termo", "dano"].forEach((x) => { $("tab-" + x).hidden = x !== S.tab; });
  }

  function setLang(lang) { stopSpeech(); S.lang = lang; store.set("lang", lang); render(); if (TOUR.on) tourPaint(); }

  // Light or dark: follows the phone until the farmer picks one with the button, then stays as chosen.
  const isDark = () => (S.theme ? S.theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches);
  function applyTheme() {
    if (S.theme) document.documentElement.dataset.theme = S.theme; else delete document.documentElement.dataset.theme;
    const dark = isDark(), b = $("themeBtn");
    b.textContent = ""; b.appendChild(svg(dark ? "sun" : "night", 22)); b.setAttribute("aria-label", t(dark ? "theme_to_light" : "theme_to_dark"));
    document.querySelectorAll('meta[name="theme-color"]').forEach((m) => { m.setAttribute("content", dark ? "#0f1319" : "#ffffff"); });
  }
  function toggleTheme() { S.theme = isDark() ? "light" : "dark"; store.set("theme", S.theme); applyTheme(); }

  function maybeReload() { if (S.reloadPending && S.tab === "noche" && !TOUR.on) { S.reloadPending = false; location.reload(); } }

  function render() {
    if (!S.pack) return;
    maybeReload(); renderShell();
    if (S.tab === "noche") renderNoche(); else if (S.tab === "termo") renderTermo(); else renderDano();
    if (TOUR.on) tourMark(false);   // the screen was rebuilt: put the ring back
  }

  function openParcels() {
    const box = $("parcelList"); box.textContent = "";
    S.pack.parcels.forEach((p) => {
      const b = el("button", "parcel-opt"); b.type = "button"; b.setAttribute("aria-pressed", String(p.parcel_id === parcel().parcel_id));
      b.appendChild(el("b", null, t("parcel", { id: p.parcel_id }) + ", " + cropName(p.crop)));
      b.appendChild(el("small", null, ownerName(p) + ", " + p.municipality));
      b.appendChild(el("small", null, p.anchored ? t("opt_station", { km: p.station.km.toFixed(1) }) : t("opt_nostation")));
      b.addEventListener("click", () => { stopSpeech(); S.parcelId = p.parcel_id; store.set("parcel", p.parcel_id); if (TOUR.on) TOUR.before.parcelId = p.parcel_id; S.revealed = false; $("parcelDlg").close(); render(); });
      box.appendChild(b);
    });
    $("demoChk").checked = isDemo(); $("demoChk").disabled = TOUR.on;   // the demo that runs by itself stays on the replay night
    if (TOUR.on && !TOUR.paused) tourToggle();
    $("parcelDlg").showModal();
  }

  async function setMode(mode, keep) {
    stopSpeech();
    const L = await load(mode);   // the mode and its pack change together: a render in between must not see one without the other
    S.mode = mode; if (keep !== false) store.set("mode", mode); S.revealed = false;
    S.pack = L.pack; S.models = L.models;
    if (!S.pack.parcels.some((p) => p.parcel_id === S.parcelId)) S.parcelId = S.pack.parcels[0].parcel_id;
    render();
    // tonight's real forecast: fetch it when there is signal and the saved one is missing or older than 6 hours
    if (mode === "real" && navigator.onLine && Date.now() - fxLastFetch() > 6 * 3600 * 1000) actualizar();
  }

  // ------------------------------------------------------------------ the demo that runs by itself
  /* One caption per step, above the tabs. Each step states the whole screen it needs (tab, plot, what is revealed,
   * how many thermometer nights, whether the example report is saved), so going back or forward is the same code.
   * Nothing is stored for good: the mode and the plot are put back on close, and the example report (marked
   * `tour`) is never sent and is deleted. */
  const TOUR = { on: false, i: 0, paused: false, timer: null, left: 0, since: 0, anim: null, before: null };
  const TOUR_NIGHT = "P01", TOUR_UNSURE = "P10";
  const tourSteps = () => {
    const L = S.pack.demo.logger, last = L.steps[L.steps.length - 1];
    const noche = (key, sel, more) => Object.assign({ key, tab: "noche", parcel: TOUR_NIGHT, sel }, more);
    return [
      noche("verdict", ".state"), noche("compare", '[data-tour="compare"]'), noche("ruler", "#noche .ruler-wrap", { tip: true }),
      noche("do", '[data-tour="do"]'), noche("listen", "#sayBtn"),
      noche("reveal", '[data-tour="happened"]', { revealed: true }), noche("how", '[data-tour="how"]', { revealed: true, open: true }),
      noche("unsure", ".state", { parcel: TOUR_UNSURE }), noche("ask", '[data-tour="ask"]', { parcel: TOUR_UNSURE }),
    ].concat(L.steps.map((k, j) => ({ key: j === 0 ? "termo0" : k === last ? "termo_last" : "termo_k", tab: "termo", parcel: TOUR_NIGHT, k, sel: j === 0 ? '[data-tour="termo-pick"]' : '[data-tour="termo-res"]' })), [
      { key: "dano_cause", tab: "dano", parcel: TOUR_NIGHT, sel: '[data-tour="dano-cause"]', dano: 1 },
      { key: "dano_area", tab: "dano", parcel: TOUR_NIGHT, sel: '[data-tour="dano-area"]', dano: 2 },
      { key: "dano_day", tab: "dano", parcel: TOUR_NIGHT, sel: '[data-tour="dano-day"]', dano: 3 },
      { key: "dano_save", tab: "dano", parcel: TOUR_NIGHT, sel: '[data-tour="dano-list"]', dano: 4 },
      noche("end", ".state", { dano: 4 }),
    ]);
  };

  /** Words of a step: the numbers come from what the phone just computed, not from the text table. */
  function tourText(st) {
    const v = {};
    if (st.tab === "noche") {
      const view = tonightView();
      v.id = view.p.parcel_id; v.kb = Math.round(S.pack.models.total_bytes / 1000);
      if (view.fc) { v.zone = grados(view.fc.grid_tmin_c); v.plot = grados(view.fc.tmin_c); }
      if (view.entry && view.entry.observed != null) { v.t = grados1(view.entry.observed); v.station = view.entry.station || view.p.station.name; }
    }
    if (st.tab === "termo") {
      const L = S.pack.demo.logger, fc = HM.predictNight(S.pack, S.models, L.site, L.night_date, L.forecast, L.nights.slice(0, st.k));
      v.k = st.k; v.mae = HM.levelFor(S.pack.logger.levels, st.k).mae_c.toFixed(1); v.t = grados1(L.observed_tmin_c);
      v.verdict = t("st_" + HM.judge(S.pack, fc, { leadDays: 0 }).state);
    }
    return t("tour_" + st.key, v);
  }

  function tourApply(st) {
    stopSpeech(); hideTips();
    S.tab = st.tab; S.parcelId = st.parcel; S.revealed = !!st.revealed;
    if (st.k != null) S.demoK = st.k;
    // the damage form, as far as this step has filled it
    const today = localISO(new Date()), day = HM.addDays(today, -1), p = parcel(), n = st.dano || 0;
    D.causa = n >= 1 ? "helada" : null; D.area = n >= 2 ? "una" : null;
    $("danoFecha").dataset.other = ""; $("danoFecha").value = n >= 3 ? day : today;
    const q = cola().filter((x) => !x.tour);
    if (n >= 4) q.push({ id: "tour", created_at: new Date().toISOString(), sent_at: null, tour: true, kind: "dano", parcel_id: p.parcel_id, phone: p.phone, text: "", causa: "helada", fecha: day });
    store.set("cola", q);
  }

  /** Ring around what the caption talks about; with `scroll`, bring it between the header and the caption bar. */
  function tourMark(scroll) {
    document.querySelectorAll(".tour-hl").forEach((n) => n.classList.remove("tour-hl"));
    const st = TOUR.steps[TOUR.i], n = document.querySelector(st.sel);
    if (!n) return;
    if (st.open) n.open = true;
    n.classList.add("tour-hl");
    if (!scroll) return;
    const top = document.querySelector(".top").getBoundingClientRect().bottom + 12, bottom = $("tour").getBoundingClientRect().top - 12;
    const r = n.getBoundingClientRect(), room = bottom - top;
    const y = window.scrollY + r.top - (r.height < room ? top + (room - r.height) / 2 : top);
    window.scrollTo({ top: Math.max(0, y), behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    if (st.tip) { const w = n.showTip ? n : n.querySelector(".ruler-wrap"); if (w && w.showTip) w.showTip("calc"); }
  }

  function tourPaint() {
    const st = TOUR.steps[TOUR.i], last = TOUR.i === TOUR.steps.length - 1, ended = last && TOUR.paused && TOUR.left <= 0;
    $("tour").setAttribute("aria-label", t("tour_region"));
    $("tourN").textContent = t("tour_step", { n: TOUR.i + 1, total: TOUR.steps.length });
    $("tourText").textContent = tourText(st);
    const set = (id, icon, label, off) => { const b = $(id); b.textContent = ""; b.appendChild(svg(icon, 26)); b.setAttribute("aria-label", t(label)); b.disabled = !!off; };
    set("tourPrev", "prev", "tour_prev", TOUR.i === 0); set("tourNext", "next", "tour_next", last);
    set("tourPlay", TOUR.paused ? "play" : "pause", ended ? "tour_again" : TOUR.paused ? "tour_play" : "tour_pause"); set("tourClose", "close", "tour_close");
    paintTourBtn();
  }
  function paintTourBtn() {
    const tb = $("tourBtn"); tb.textContent = ""; tb.appendChild(svg(TOUR.on ? "close" : "play", 22));
    tb.setAttribute("aria-label", t(TOUR.on ? "tour_close" : "tour_start")); tb.setAttribute("aria-pressed", String(TOUR.on));
  }

  /** Time on a step: about 5 to 6 seconds, a little more for a long caption. */
  const tourDur = (st) => Math.min(8500, 4200 + 22 * tourText(st).length);
  function tourRun(ms) {
    clearTimeout(TOUR.timer); if (TOUR.anim) TOUR.anim.cancel();
    TOUR.left = ms; TOUR.since = Date.now();
    const fill = $("tourFill"), total = tourDur(TOUR.steps[TOUR.i]);
    fill.style.transform = "scaleX(" + (1 - ms / total) + ")";
    if (TOUR.paused) return;
    if (fill.animate) TOUR.anim = fill.animate([{ transform: "scaleX(" + (1 - ms / total) + ")" }, { transform: "scaleX(1)" }], { duration: ms, fill: "forwards" });
    TOUR.timer = setTimeout(() => {
      if (TOUR.i < TOUR.steps.length - 1) return tourGo(TOUR.i + 1);
      TOUR.paused = true; TOUR.left = 0; fill.style.transform = "scaleX(1)"; tourPaint();   // the end: wait there
    }, ms);
  }
  function tourGo(i) {
    if (!TOUR.on || i < 0 || i >= TOUR.steps.length) return;
    TOUR.i = i; tourApply(TOUR.steps[i]); render(); tourPaint();
    requestAnimationFrame(() => tourMark(true));
    tourRun(tourDur(TOUR.steps[i]));
  }
  function tourToggle() {
    if (!TOUR.on) return;
    if (!TOUR.paused) { TOUR.left = Math.max(0, TOUR.left - (Date.now() - TOUR.since)); TOUR.paused = true; clearTimeout(TOUR.timer); if (TOUR.anim) TOUR.anim.pause(); tourPaint(); return; }
    TOUR.paused = false;
    if (TOUR.left <= 0 && TOUR.i === TOUR.steps.length - 1) return tourGo(0);   // at the end: play it again
    tourPaint(); tourRun(TOUR.left || tourDur(TOUR.steps[TOUR.i]));
  }
  async function tourStart() {
    if (TOUR.on || TOUR.starting) return;
    TOUR.starting = true;
    const dlg = $("parcelDlg"); if (dlg.open) dlg.close();
    TOUR.before = { mode: S.mode, parcelId: S.parcelId, tab: S.tab, revealed: S.revealed, demoK: S.demoK, causa: D.causa, area: D.area,
      fecha: $("danoFecha").value, otra: $("danoFecha").dataset.other || "" };
    try { await setMode("demo", false); } catch (e) { TOUR.starting = false; return; }   // the demo pack is not on this phone yet
    TOUR.starting = false; TOUR.on = true; TOUR.paused = false; TOUR.steps = tourSteps();
    document.body.classList.add("touring"); $("tour").hidden = false;
    tourGo(0);
  }
  function tourClose() {
    if (!TOUR.on) return;
    clearTimeout(TOUR.timer); if (TOUR.anim) TOUR.anim.cancel();
    TOUR.on = false; stopSpeech(); hideTips();
    document.body.classList.remove("touring"); $("tour").hidden = true;
    document.querySelectorAll(".tour-hl").forEach((n) => n.classList.remove("tour-hl"));
    store.set("cola", cola().filter((x) => !x.tour));
    const b = TOUR.before;
    S.parcelId = b.parcelId; S.tab = b.tab; S.demoK = b.demoK; D.causa = b.causa; D.area = b.area;
    $("danoFecha").value = b.fecha; $("danoFecha").dataset.other = b.otra;
    if (location.hash === "#tour") history.replaceState(null, "", location.pathname + location.search);
    paintTourBtn();
    setMode(b.mode, false).then(() => { S.revealed = b.revealed; render(); window.scrollTo(0, 0); });
  }

  async function start() {
    document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => { stopSpeech(); S.tab = b.dataset.tab; render(); window.scrollTo(0, 0); }));
    $("parcelBtn").addEventListener("click", openParcels);
    $("demoChk").addEventListener("change", (e) => { setMode(e.target.checked ? "demo" : "real"); });
    const flip = () => setLang(S.lang === "es" ? "en" : "es");
    $("langBtn").addEventListener("click", flip);
    $("themeBtn").addEventListener("click", toggleTheme);
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
    $("tourBtn").addEventListener("click", () => (TOUR.on ? tourClose() : tourStart()));
    $("tourPrev").addEventListener("click", () => tourGo(TOUR.i - 1));
    $("tourNext").addEventListener("click", () => tourGo(TOUR.i + 1));
    $("tourPlay").addEventListener("click", tourToggle);
    $("tourClose").addEventListener("click", tourClose);
    document.addEventListener("keydown", (e) => {
      if (!TOUR.on || /^(INPUT|TEXTAREA)$/.test(e.target.tagName || "")) return;
      if (e.key === "Escape") tourClose(); else if (e.key === "ArrowRight") tourGo(TOUR.i + 1); else if (e.key === "ArrowLeft") tourGo(TOUR.i - 1);
    });
    window.addEventListener("hashchange", () => { if (location.hash === "#tour" && !TOUR.on) tourStart(); });
    // a tap anywhere else puts the ruler's label away
    document.addEventListener("pointerdown", (e) => { if (!(e.target.closest && e.target.closest(".hit"))) hideTips(); });
    window.addEventListener("pagehide", stopSpeech);
    $("termoFecha").addEventListener("change", () => dayStrip($("termoDias"), $("termoFecha"), 7));
    $("danoFecha").addEventListener("change", () => dayStrip($("danoDias"), $("danoFecha"), 10));
    store.set("cola", cola().filter((x) => !x.tour));   // a demo that was cut short leaves nothing behind
    applyStatic();
    $("termoForm").addEventListener("submit", onTermo);
    $("danoForm").addEventListener("submit", onDano);
    $("termoSigno").addEventListener("click", (e) => { const b = e.currentTarget; b.setAttribute("aria-pressed", String(b.getAttribute("aria-pressed") !== "true")); });
    window.addEventListener("online", () => { render(); enviarCola().then(render); if (!isDemo()) actualizar(); });
    window.addEventListener("offline", render);
    if ("serviceWorker" in navigator) {
      // A new version installs as one set and takes over; reload once so the screen is that set. Not while the
      // farmer is in the middle of a form: then wait until they are back on the first tab.
      const hadOne = !!navigator.serviceWorker.controller;
      navigator.serviceWorker.addEventListener("controllerchange", () => { if (hadOne) { S.reloadPending = true; maybeReload(); } });
      const local = /^(localhost|127\.0\.0\.1)$/.test(location.hostname) && !/[?&]sw=prod\b/.test(location.search);
      navigator.serviceWorker.register(local ? "sw.js?dev=1" : "sw.js").then((reg) => {
        // an installed page can stay open for days: look for a new version each time it comes back to the front
        document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && navigator.onLine) reg.update().catch(() => {}); });
      }).catch(() => { /* http without localhost: no offline shell */ });
    }
    try {
      await setMode(S.mode);
    } catch (e) {
      $("loading").removeAttribute("data-t");
      $("loading").textContent = t("load_error", { err: e.message || e });
      return;
    }
    if (navigator.onLine) enviarCola().then(render);   // with signal: send whatever is waiting
    if (location.hash === "#tour") tourStart();
  }

  start();
})();
