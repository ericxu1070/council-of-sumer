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
        <pattern id="city-field" width="10" height="16" patternUnits="userSpaceOnUse">
          <path d="M5 16V4" stroke="#a2aa5b" stroke-width="1.2"/>
          <path d="M5 11 2 8M5 8 2 5M5 11 8 8M5 8 8 5M5 5V2" fill="none" stroke="#e9c66a" stroke-width="1.8" stroke-linecap="round"/>
        </pattern>
        <g id="city-plot"><rect width="32" height="43" rx="3" fill="#6f7540" stroke="#b4a262"/><rect x="4" y="4" width="24" height="35" fill="url(#city-field)"/></g>
        <g id="city-home">
          <path d="M5 17 36 13 39 40 8 43Z" fill="#292f27" opacity=".4"/>
          <path d="M4 15H31V39H4Z" fill="#c1ab82" stroke="#d8c6a0"/>
          <path d="M31 15 37 11V34L31 39Z" fill="#8f7b59"/>
          <path d="M0 17 17 1 35 17Z" fill="#b18c50" stroke="#dfbe7b" stroke-linejoin="round"/>
          <path d="M17 1 23 0 39 11 35 17Z" fill="#80633c"/>
          <path d="M7 13 17 4 28 13M10 16 17 8 25 16" fill="none" stroke="#d2ac67" stroke-width="1.3"/>
          <path d="M15 39V26Q19 22 23 26V39" fill="#514735"/>
          <rect x="7" y="22" width="5" height="6" fill="#66563e"/>
          <path d="M5 33H13M24 33H30M5 37H12" stroke="#a48c67"/>
        </g>
        <g id="city-sack">
          <path d="M7 4C3 9 1 19 6 22H20C25 19 23 9 19 4Z" fill="#c9a15b" stroke="#efcf89"/>
          <ellipse cx="13" cy="4" rx="6" ry="3" fill="#8e6b32" stroke="#efcf89"/>
          <path d="M9 3 10 5M13 2 14 4M16 3 17 5M12 5 13 6" stroke="#f6d576" stroke-width="2" stroke-linecap="round"/>
          <path d="M7 10 6 17M19 10 20 17M10 19H16" fill="none" stroke="#9f783d"/>
        </g>
        <g id="city-silo">
          <path d="M3 19V81C3 91 45 91 45 81V19" fill="#bca47a" stroke="#dfc79b" stroke-width="2"/>
          <path d="M33 20V84Q45 83 45 79V19Z" fill="#8d7654"/>
          <path d="M0 20 24 0 48 20Q24 31 0 20Z" fill="#aa8549" stroke="#e1bf7e" stroke-width="2" stroke-linejoin="round"/>
          <path d="M8 41Q24 47 41 41M8 62Q24 68 41 62" fill="none" stroke="#967e59" stroke-width="2"/>
          <path d="M17 85V72Q24 66 31 72V85" fill="#5e5038"/>
        </g>
      </defs>
      <rect width="1200" height="900" fill="#38433a"/>
      <rect width="1200" height="900" fill="url(#city-soil)"/>
      <path d="M-30 50C300 190 380 -30 640 65S990 130 1240 0" fill="none" stroke="#243e42" stroke-width="100"/>
      <path d="M-30 50C300 190 380 -30 640 65S990 130 1240 0" fill="none" stroke="#5c8b90" stroke-width="68" opacity=".65"/>
      <path d="M305 156V865M895 156V865M20 658H1180" fill="none" stroke="#93896d" stroke-width="18" opacity=".6"/>
      <rect x="328" y="175" width="545" height="515" rx="180" fill="#5a604a" opacity=".45"/>
      <rect x="355" y="197" width="490" height="470" rx="165" fill="none" stroke="#a49776" stroke-width="2" opacity=".2"/>
      <g fill="#d7ceb0" font-family="system-ui,sans-serif" font-size="13" letter-spacing="3" text-anchor="middle">
        <text x="160" y="183">FIELDS</text><text x="1030" y="183">CITY HOMES</text><text x="600" y="677">GRANARY</text>
      </g>
      <rect x="434" y="689" width="330" height="97" rx="8" fill="#514e3b" stroke="#9b8a61" stroke-width="2"/>
      <use href="#city-silo" x="378" y="696"/>
      <use href="#city-silo" x="774" y="696"/>
      <g id="city-acres"></g><g id="city-grain"></g><g id="city-population"></g>
    </svg>`;
    cards.innerHTML = resources.map(r => `<div class="resource" style="--resource-color:${r.color}" title="1 ${r.noun} = ${r.step} ${r.unit}">
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
