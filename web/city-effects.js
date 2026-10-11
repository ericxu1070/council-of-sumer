/* Screen-wide, crude event sprites; destinations follow the live city map. */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const COUNT = 100;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)");
  let overlay, cleanup = 0;
  const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
  function clear() {
    clearTimeout(cleanup); cleanup = 0;
    overlay?.remove(); overlay = null;
  }
  function cityPoint(x, y, w, h) {
    const bounds = document.getElementById("cityMap")?.getBoundingClientRect();
    if (!bounds) return { x: w * .6, y: h * .7 };
    return { x: clamp(bounds.left + x / 1200 * bounds.width, 16, w - 16),
      y: clamp(bounds.top + y / 900 * bounds.height, 16, h - 16) };
  }
  function startPoint(i, w, h) {
    // One sprite in each cell of a 10 × 10 screen grid, including the sidebar.
    return { x: (i % 10 + .12 + Math.random() * .76) * w / 10,
      y: (Math.floor(i / 10) + .12 + Math.random() * .76) * h / 10 };
  }
  const rat = `<path d="M0 12H6V7H10V4H21V7H26V10H30V14H24V17H9V14H6" fill="#342c21" stroke="#b19973" stroke-width="1"/>
    <path d="M2 12H-7V7H-12M11 17v3m10-3v3" fill="none" stroke="#342c21" stroke-width="2"/>
    <path d="M13 3h5v5h-5z" fill="#68513a"/><path d="M25 9h2v2h-2z" fill="#ead1a0"/>`;
  const bacterium = `<path d="M4 0h8v3h4v10h-4v3H4v-3H0V3h4z" fill="#8fbd59" stroke="#314525" stroke-width="2"/>
    <path d="M5-5v5m6-5v5M-5 5h5m-5 6h5m16-6h5m-5 6h5M5 16v5m6-5v5" stroke="#8fbd59" stroke-width="2"/>
    <path d="M5 5h3v3H5zm5 4h2v2h-2z" fill="#365632"/>`;
  function traveler(kind, from, to, i) {
    const delay = i % 10 * 65, duration = 3600 + i % 7 * 100;
    const midpoint = { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 + (i % 2 ? 18 : -18) };
    const facing = kind === "rat" && to.x < from.x ? -1 : 1;
    return `<g class="city-traveler ${kind}" style="--sx:${from.x}px;--sy:${from.y}px;--mx:${midpoint.x}px;--my:${midpoint.y}px;--tx:${to.x}px;--ty:${to.y}px;--delay:${delay}ms;--duration:${duration}ms">
      <g transform="scale(${facing} 1)"><g class="city-sprite">${kind === "rat" ? rat : bacterium}</g></g>
    </g>`;
  }
  function villagers(points) {
    return points.map((p, i) => `<g transform="translate(${p.x} ${p.y})">
      <g class="plague-person ${i % 2 ? "survives" : "falls"}" style="--delay:${3900 + i * 50}ms">
        <path d="M6 0h8v8H6zM4 9h12v12H4zM4 21h4v8H4zM12 21h4v8h-4z" fill="#4f4b2e"/>
        <path d="M7 2l5 4m0-4L7 6" stroke="#ccaf71" stroke-width="1.5"/>
        <path d="M0 11h4v9H0zM16 11h4v9h-4z" fill="#827d42"/>
      </g><path class="plague-mark" style="--delay:${4400 + i * 50}ms" d="M-3-8l6 6m0-6-6 6" stroke="#c86a4b" stroke-width="3"/>
    </g>`).join("");
  }
  function effect(kinds) {
    const events = [...new Set(Array.isArray(kinds) ? kinds : [kinds])].filter(k => k === "plague" || k === "rats");
    if (!events.length || document.hidden) return;
    clear();
    const w = innerWidth, h = innerHeight;
    const people = Array.from({length:12}, (_, i) => cityPoint(947 + i % 4 * 49, 241 + Math.floor(i / 4) * 58, w, h));
    let drawing = "";
    if (events.includes("plague")) drawing += villagers(people);
    for (const kind of events) for (let i = 0; i < COUNT; i++) {
      const to = kind === "rats" ? cityPoint(460 + i % 8 * 37, 713 + Math.floor(i / 8) % 3 * 20, w, h) : people[i % people.length];
      drawing += traveler(kind === "rats" ? "rat" : "bacterium", startPoint(i,w,h), to, i);
    }
    overlay = document.createElementNS(NS, "svg");
    overlay.setAttribute("class", "city-event-overlay");
    const zoom = Number(getComputedStyle(document.documentElement).getPropertyValue("--z")) || 1;
    overlay.style.zoom = String(1 / zoom);
    overlay.setAttribute("viewBox", `0 0 ${w} ${h}`);
    overlay.setAttribute("aria-hidden", "true");
    overlay.innerHTML = drawing;
    document.body.appendChild(overlay);
    cleanup = setTimeout(clear, reduce.matches ? 1800 : events.includes("plague") ? 8000 : 5600);
  }
  // Fixed viewport sprites are discarded when their map destinations move.
  addEventListener("resize", clear);
  addEventListener("scroll", clear, {passive:true});
  document.addEventListener("visibilitychange", () => { if (document.hidden) clear(); });
  window.CityEffects = { effect, clear };
})();
