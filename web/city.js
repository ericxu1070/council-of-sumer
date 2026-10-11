/* Simple overhead symbols, sized against fixed units rather than invented capacities. */
(function () {
  "use strict";
  const resources = [
    { key: "acres", label: "Land", unit: "acres", step: 100, symbol: "plot", noun: "plot", max: 40, color: "#86b97b", x: 58, y: 205, cols: 5, dx: 40, dy: 53,
      icon: '<path d="M3 5h18v14H3zM9 5v14M15 5v14M3 12h18"/>' },
    { key: "grain", label: "Grain", unit: "bushels", step: 200, symbol: "sack", noun: "sack", max: 32, color: "#eac16e", x: 456, y: 700, cols: 8, dx: 37, dy: 20,
      icon: '<path d="M8 3h8l-2 5c5 4 7 12 2 13H8C3 20 5 12 10 8zM9 8h6"/>' },
    { key: "population", label: "People", unit: "citizens", step: 10, symbol: "home", noun: "home", max: 32, color: "#8fbcd3", x: 938, y: 208, cols: 4, dx: 49, dy: 53,
      icon: '<circle cx="12" cy="6" r="3"/><path d="M6 21v-5a6 6 0 0 1 12 0v5M3 21h18"/>' },
  ];
  let map, cards, effectLayer, effectTimer = 0;
  function mount() {
    map = document.getElementById("cityMap");
    cards = document.getElementById("cityResources");
    clearEffects();
    map.innerHTML = `<svg viewBox="0 0 1200 900" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <pattern id="city-soil" width="28" height="28" patternUnits="userSpaceOnUse"><circle cx="6" cy="8" r="1" fill="#614a2c" opacity=".24"/></pattern>
        <pattern id="city-field" width="8" height="8" patternUnits="userSpaceOnUse"><path d="M2 0v8" stroke="#b9ba7c" stroke-width="2" opacity=".6"/></pattern>
        <g id="city-plot"><rect width="32" height="43" rx="0" fill="#777d47" stroke="#414b2b"/><rect x="4" y="4" width="24" height="35" fill="url(#city-field)"/></g>
        <g id="city-home"><rect x="1" y="3" width="36" height="35" rx="0" fill="#7a715c"/><rect width="34" height="33" rx="0" fill="#c1ab82" stroke="#d8c6a0"/><rect x="7" y="7" width="20" height="19" fill="#8f7b59"/><rect x="12" y="12" width="10" height="9" fill="#b6a381"/><path d="M15 26v7" stroke="#5c513e" stroke-width="5"/></g>
        <g id="city-sack"><path d="M7 1h12l-3 5c7 5 10 15 3 16H7C0 21 3 11 10 6z" fill="#dfb762" stroke="#f0d398"/><path d="M8 6h10M13 11v7" stroke="#a17c35"/></g>
      </defs>
      <rect width="1200" height="900" fill="#ae8e57"/>
      <rect width="1200" height="900" fill="url(#city-soil)"/>
      <path d="M-30 50C300 190 380 -30 640 65S990 130 1240 0" fill="none" stroke="#776344" stroke-width="100"/>
      <path d="M-30 50C300 190 380 -30 640 65S990 130 1240 0" fill="none" stroke="#668077" stroke-width="68" opacity=".65"/>
      <path d="M305 156V865M895 156V865M20 658H1180" fill="none" stroke="#c6aa71" stroke-width="18" opacity=".6"/>
      <path d="M288 170V865M913 170V865" stroke="#668077" stroke-width="6" opacity=".8"/>
      <path d="M332 188H474V168H732V187H871V639H854V655H343V630H332Z" fill="#c2a16a"/>
      <path d="M332 188H474V168H732V187H871V639H854V655H343V630H332Z" fill="none" stroke="#8a6b3e" stroke-width="6" stroke-dasharray="12 4"/>
      <path d="M394 244h46m276 28h55M415 550h24m351-86h33M507 607h39" stroke="#9d7f4b" stroke-width="3"/>
      <path d="M55 145v-18m-6 12 6-12 6 12m18 5v-24m-6 16 6-16 6 16M1073 147v-20m-6 12 6-12 6 12m20 6v-25" stroke="#536346" stroke-width="3" fill="none"/>
      <g fill="#48361f" font-family="monospace" font-size="13" letter-spacing="1" text-anchor="middle">
        <text x="160" y="183">FIELDS</text><text x="1030" y="183">CITY HOMES</text><text x="600" y="677">GRANARY</text>
      </g>
      <rect x="434" y="689" width="330" height="97" rx="0" fill="#917042" stroke="#574124" stroke-width="2"/>
      <g id="city-acres"></g><g id="city-grain"></g><g id="city-population"></g><g id="city-effects" aria-hidden="true"></g>
      <path d="M40 798v-40m-8 10 8-10 8 10" fill="none" stroke="#4a3820" stroke-width="2"/>
      <text x="40" y="817" text-anchor="middle" font-family="monospace" font-size="12" fill="#4a3820">N</text>
    </svg>`;
    effectLayer = document.getElementById("city-effects");
    cards.innerHTML = resources.map(r => `<div class="resource" style="--resource-color:${r.color}" title="1 ${r.noun} = ${r.step} ${r.unit}">
      <span class="resource-head"><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round">${r.icon}</svg>${r.label}</span>
      <span class="resource-value" id="city-value-${r.key}">—</span>

    </div>`).join("");
    render(null);
  }
  function render(state) {
    if (!map) return;
    const descriptions = [];
    for (const r of resources) {
      const known = state?.[r.key] != null && Number.isFinite(Number(state[r.key]));
      const value = known ? Math.max(0, Number(state[r.key])) : 0;
      const units = value / r.step, shown = Math.min(r.max, Math.ceil(units));
      document.getElementById(`city-${r.key}`).innerHTML = Array.from({ length: r.max }, (_, i) => {
        const x = r.x + (i % r.cols) * r.dx, y = r.y + Math.floor(i / r.cols) * r.dy;
        const opacity = i < shown ? .35 + .65 * Math.min(1, units - i) : .08;
        return `<use href="#city-${r.symbol}" x="${x}" y="${y}" opacity="${opacity}"/>`;
      }).join("");
      document.getElementById(`city-value-${r.key}`).innerHTML = known
        ? `${value.toLocaleString("en-US")} <small>${r.unit}</small>` : "—";
      document.getElementById(`city-value-${r.key}`).parentElement.title = `1 ${r.noun} = ${r.step} ${r.unit}`
        + (units > r.max ? `; map capped at ${r.max * r.step} ${r.unit}` : "");
      if (known) descriptions.push(`${r.label}: ${value.toLocaleString("en-US")} ${r.unit}`);
    }
    map.setAttribute("aria-label", descriptions.length
      ? `Bird’s-eye view of Sumer. ${descriptions.join(". ")}. Symbols show fixed units; exact totals appear below.`
      : "Bird’s-eye view of Sumer. Awaiting city totals when the council convenes.");
  }
  function clearEffects() {
    clearTimeout(effectTimer); effectTimer = 0;
    effectLayer?.replaceChildren();
    map?.classList.remove("omen-plague", "omen-rats");
  }
  function effect(kinds) {
    const events = (Array.isArray(kinds) ? kinds : [kinds]).filter(k => k === "plague" || k === "rats");
    if (!effectLayer || !events.length) return;
    clearEffects();
    let drawing = "";
    if (events.includes("rats")) {
      map.classList.add("omen-rats");
      for (let i = 0; i < 9; i++) {
        const x = 434 + (i % 5) * 58, y = 708 + Math.floor(i / 5) * 31;
        drawing += `<g transform="translate(${x} ${y})"><g class="omen-rat" style="--delay:${i * 70}ms;--run:${i % 2 ? -55 : 65}px">
          <path d="M0 12H6V7H10V4H21V7H26V10H30V14H24V17H9V14H6" fill="#342c21"/>
          <path d="M2 12H-7V7H-12M11 17v3m10-3v3" fill="none" stroke="#342c21" stroke-width="2"/>
          <path d="M13 3h5v5h-5z" fill="#5a4630"/><path d="M25 9h2v2h-2z" fill="#d1ad65"/>
        </g></g>`;
      }
    }
    if (events.includes("plague")) {
      map.classList.add("omen-plague");
      for (let i = 0; i < 12; i++) {
        const x = 947 + (i % 4) * 49, y = 241 + Math.floor(i / 4) * 58;
        drawing += `<g transform="translate(${x} ${y})"><g class="plague-person ${i % 2 ? "survives" : "falls"}" style="--delay:${i * 80}ms">
          <path d="M6 0h8v8H6zM4 9h12v12H4zM4 21h4v8H4zM12 21h4v8h-4z" fill="#4f4b2e"/>
          <path d="M7 2l5 4m0-4L7 6" stroke="#ccaf71" stroke-width="1.5"/>
          <path d="M0 11h4v9H0zM16 11h4v9h-4z" fill="#827d42"/>
        </g><path class="plague-mark" d="M-3 -8l6 6m0-6-6 6" stroke="#703d2b" stroke-width="3"/></g>`;
      }
    }
    effectLayer.innerHTML = drawing;
    effectTimer = setTimeout(clearEffects, matchMedia("(prefers-reduced-motion: reduce)").matches ? 1800 : 3600);
  }
  window.CityView = { mount, render, effect, clearEffects };
})();
