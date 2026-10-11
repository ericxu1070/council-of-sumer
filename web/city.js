/* Simple overhead symbols, sized against fixed units rather than invented capacities. */
(function () {
  "use strict";
  const resources = [
    { key: "acres", label: "Land owned", unit: "acres", step: 100, symbol: "plot", noun: "plot", max: 40, color: "#86b97b", x: 58, y: 205, cols: 5, dx: 40, dy: 53,
      icon: '<path d="M3 5h18v14H3zM9 5v14M15 5v14M3 12h18"/>' },
    { key: "grain", label: "Grain in store", unit: "bushels", step: 200, symbol: "sack", noun: "sack", max: 32, color: "#eac16e", x: 456, y: 700, cols: 8, dx: 37, dy: 20,
      icon: '<path d="M8 3h8l-2 5c5 4 7 12 2 13H8C3 20 5 12 10 8zM9 8h6"/>' },
    { key: "population", label: "People", unit: "citizens", step: 10, symbol: "home", noun: "home", max: 32, color: "#8fbcd3", x: 938, y: 208, cols: 4, dx: 49, dy: 53,
      icon: '<circle cx="12" cy="6" r="3"/><path d="M6 21v-5a6 6 0 0 1 12 0v5M3 21h18"/>' },
  ];
  let map, cards;
  function mount() {
    map = document.getElementById("cityMap");
    cards = document.getElementById("cityResources");
    map.innerHTML = `<svg viewBox="0 0 1200 900" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <pattern id="city-soil" width="28" height="28" patternUnits="userSpaceOnUse"><circle cx="6" cy="8" r="1" fill="#8b8267" opacity=".2"/></pattern>
        <pattern id="city-field" width="8" height="8" patternUnits="userSpaceOnUse"><path d="M2 0v8" stroke="#abc184" stroke-width="2" opacity=".6"/></pattern>
        <g id="city-plot"><rect width="32" height="43" rx="3" fill="#597a4c" stroke="#a5b880"/><rect x="4" y="4" width="24" height="35" fill="url(#city-field)"/></g>
        <g id="city-home"><rect x="1" y="3" width="36" height="35" rx="3" fill="#7a715c"/><rect width="34" height="33" rx="2" fill="#c1ab82" stroke="#d8c6a0"/><rect x="7" y="7" width="20" height="19" fill="#8f7b59"/><rect x="12" y="12" width="10" height="9" fill="#b6a381"/><path d="M15 26v7" stroke="#5c513e" stroke-width="5"/></g>
        <g id="city-sack"><path d="M7 1h12l-3 5c7 5 10 15 3 16H7C0 21 3 11 10 6z" fill="#dfb762" stroke="#f0d398"/><path d="M8 6h10M13 11v7" stroke="#a17c35"/></g>
      </defs>
      <rect width="1200" height="900" fill="#38433a"/>
      <rect width="1200" height="900" fill="url(#city-soil)"/>
      <path d="M-30 50C300 190 380 -30 640 65S990 130 1240 0" fill="none" stroke="#243e42" stroke-width="100"/>
      <path d="M-30 50C300 190 380 -30 640 65S990 130 1240 0" fill="none" stroke="#5c8b90" stroke-width="68" opacity=".65"/>
      <path d="M305 156V865M895 156V865M20 658H1180" fill="none" stroke="#93896d" stroke-width="18" opacity=".6"/>
      <path d="M288 170V865M913 170V865" stroke="#568084" stroke-width="6" opacity=".8"/>
      <rect x="328" y="175" width="545" height="515" rx="180" fill="#5a604a" opacity=".45"/>
      <rect x="355" y="197" width="490" height="470" rx="165" fill="none" stroke="#a49776" stroke-width="2" opacity=".2"/>
      <g fill="#d7ceb0" font-family="system-ui,sans-serif" font-size="13" letter-spacing="3" text-anchor="middle">
        <text x="160" y="183">FIELDS</text><text x="1030" y="183">CITY HOMES</text><text x="600" y="677">GRANARY</text>
      </g>
      <rect x="434" y="689" width="330" height="97" rx="8" fill="#514e3b" stroke="#9b8a61" stroke-width="2"/>
      <g id="city-acres"></g><g id="city-grain"></g><g id="city-population"></g>
      <path d="M40 798v-40m-8 10 8-10 8 10" fill="none" stroke="#bab394" stroke-width="2"/>
      <text x="40" y="817" text-anchor="middle" font-family="system-ui" font-size="12" fill="#bab394">N</text>
    </svg>`;
    cards.innerHTML = resources.map(r => `<div class="resource" style="--resource-color:${r.color}">
      <span class="resource-head"><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round">${r.icon}</svg>${r.label}</span>
      <span class="resource-value" id="city-value-${r.key}">—</span>
      <span class="resource-scale">1 ${r.noun} = ${r.step} ${r.unit}</span>
      <span class="resource-scale resource-overflow" id="city-overflow-${r.key}"></span>
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
      document.getElementById(`city-overflow-${r.key}`).textContent = units > r.max
        ? `Map shows ${(r.max * r.step).toLocaleString("en-US")} ${r.unit}; total above.`
        : known && units % 1 ? "Faded symbol = a partial unit" : "";
      if (known) descriptions.push(`${r.label}: ${value.toLocaleString("en-US")} ${r.unit}`);
    }
    map.setAttribute("aria-label", descriptions.length
      ? `Bird’s-eye view of Sumer. ${descriptions.join(". ")}. Symbols show fixed units; exact totals appear below.`
      : "Bird’s-eye view of Sumer. Awaiting city totals when the council convenes.");
  }
  window.CityView = { mount, render };
})();
