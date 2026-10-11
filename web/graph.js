/* Council of Sumer - council graph.
 * Hub-and-spoke view: every advisor is a portrait node on a ring, wired to a central
 * SLIM hub (the message bus). speak() lights speaker -> hub -> listeners with flowing
 * packets; a speech bubble pops beside the speaker. Positions tween in rAF; everything
 * else animates transform/opacity/stroke-dashoffset only. Exposes window.CouncilGraph. */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const BASE = 96;              // avatar layout size; real size is a scale of this
  const HUB_BASE = 104;
  const LABEL_H = 30;           // label strip under a node
  const PAD = 14;               // min gap to the container edge
  const TRAVEL = 260;           // ms for a packet to cross one spoke
  const GAP = 22;               // min clear space between a node (with label) and the hub (with orbit/sub-label)
  const MIN_S = 34;             // smallest node size
  const reduce = matchMedia("(prefers-reduced-motion: reduce)");
  const RM = () => reduce.matches;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const ease = t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  const esc = t => String(t ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const sv = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };

  const CSS = `
.cg{position:absolute;inset:0;overflow:hidden;contain:strict;user-select:none;-webkit-user-select:none;
  --cg-speak:var(--speak,#22d3ee);--cg-listen:var(--listen,#e879f9);--cg-bg:var(--bg,#0b1020);--cg-panel:var(--panel,#121a2e);
  --cg-ink:var(--ink,#e6ecff);--cg-muted:var(--muted,#8a96b8);--cg-line:var(--line,rgba(255,255,255,.08));
  font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;color:var(--cg-ink)}
.cg-bg{position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(closest-side,rgba(34,211,238,.07),transparent 70%) center/70% 80% no-repeat,
  radial-gradient(rgba(255,255,255,.055) 1px,transparent 1.2px) 0 0/22px 22px}
.cg-svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible;pointer-events:none}
.cg-spoke path{fill:none;stroke-linecap:round}
.cg-spoke .base{stroke:rgba(255,255,255,.09);stroke-width:1.5}
.cg-spoke .glow,.cg-spoke .core,.cg-spoke .flow{stroke:var(--k,var(--cg-speak));opacity:0;transition:opacity .45s ease}
.cg-spoke .glow{stroke-width:9;}
.cg-spoke .core{stroke-width:2.2}
.cg-spoke .flow{stroke-width:3.2;stroke-dasharray:.1 13}
.cg-spoke.s{--k:var(--cg-speak)}.cg-spoke.l,.cg-spoke.lb{--k:var(--cg-listen)}
.cg-spoke.s .glow,.cg-spoke.l .glow{opacity:.16}.cg-spoke.lb .glow{opacity:.07}
.cg-spoke.s .core,.cg-spoke.l .core{opacity:.9}.cg-spoke.lb .core{opacity:.42}
.cg-spoke.s .flow,.cg-spoke.l .flow{opacity:1}.cg-spoke.lb .flow{opacity:.5}
.cg-spoke.s .flow{animation:cg-flow .8s linear infinite}
.cg-spoke.l .flow,.cg-spoke.lb .flow{animation:cg-flow .8s linear infinite reverse}
.cg-spoke.settled .flow{animation:none;opacity:0}
@keyframes cg-flow{from{stroke-dashoffset:13.1}to{stroke-dashoffset:0}}
.cg-relation{--relation:#4ade80;animation:cg-relation-flash 1.2s ease-out both}
.cg-relation.disagree{--relation:#f87171}
.cg-relation path{fill:none;stroke:var(--relation);stroke-linecap:round}
.cg-relation .relation-halo{stroke-width:10;opacity:.2}
.cg-relation .relation-line{stroke-width:3}
.cg-relation.disagree .relation-line{stroke-dasharray:8 6}
.cg-relation .relation-label{fill:var(--relation);font-size:20px;font-weight:800;paint-order:stroke;stroke:#0b1020;stroke-width:5px;stroke-linejoin:round}
@keyframes cg-relation-flash{0%{opacity:0}12%,65%{opacity:1}100%{opacity:0}}
@media(prefers-reduced-motion:reduce){.cg-relation{animation:none}}
.cg-pk{fill:#fff}.cg-pk-halo{opacity:.35}
.cg-node,.cg-hub{position:absolute;left:0;top:0;width:0;height:0;will-change:transform}
.cg-node{transition:opacity .5s ease}
.cg-node.enter,.cg-node.leave{opacity:0}
.cg-av{position:absolute;left:${-BASE / 2}px;top:${-BASE / 2}px;width:${BASE}px;height:${BASE}px;
  transform:scale(var(--k,1));transition:transform .55s cubic-bezier(.2,.8,.2,1)}
.cg-pop{position:absolute;inset:0;transition:transform .45s cubic-bezier(.3,1.5,.5,1),opacity .45s ease}
.cg-face{position:absolute;inset:0;border-radius:50%;overflow:hidden;background:var(--cg-panel);
  box-shadow:0 6px 18px rgba(0,0,0,.45)}
.cg-face>svg{display:block;width:100%;height:100%}
.cg-emoji{display:grid;place-items:center;width:100%;height:100%;font-size:46px;
  background:radial-gradient(circle at 50% 35%,color-mix(in srgb,var(--c) 55%,#fff 0%),color-mix(in srgb,var(--c) 45%,#0b1020))}
.cg-ring{position:absolute;inset:-5px;border-radius:50%;border:3px solid var(--c);opacity:.85;transition:opacity .4s ease}
.cg-glow{position:absolute;inset:-9px;border-radius:50%;opacity:0;transition:opacity .45s ease;pointer-events:none}
.cg-glow.gs{border:4px solid var(--cg-speak);box-shadow:0 0 22px 4px color-mix(in srgb,var(--cg-speak) 55%,transparent),inset 0 0 12px color-mix(in srgb,var(--cg-speak) 40%,transparent)}
.cg-glow.gl{border:5px solid var(--cg-listen);box-shadow:0 0 26px 6px color-mix(in srgb,var(--cg-listen) 60%,transparent),inset 0 0 14px color-mix(in srgb,var(--cg-listen) 45%,transparent)}
.cg-node.speaking .cg-pop{transform:scale(1.12)}
.cg-node.speaking .gs{opacity:1}
.cg-node.listening .gl{opacity:1}
.cg-node.listening-soft .gl{opacity:.55}
.cg-node.speaking .cg-ring,.cg-node.listening .cg-ring{opacity:0}
.cg-node.listening .cg-pop{animation:cg-ping .5s cubic-bezier(.3,1.6,.5,1)}
@keyframes cg-ping{40%{transform:scale(1.07)}}
.cg-node.dim .cg-pop{opacity:.38}
.cg-node.dim .cg-label{opacity:.45}
.cg-label{position:absolute;left:0;top:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:center;
  font-size:17px;font-weight:700;letter-spacing:.01em;line-height:26px;padding:0 10px;border-radius:13px;
  color:var(--cg-ink);background:rgba(11,16,32,.72);transition:transform .55s cubic-bezier(.2,.8,.2,1),color .3s,opacity .4s}
.cg-label i{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--c);margin-right:7px;vertical-align:2px}
.cg-node.speaking .cg-label{color:var(--cg-speak)}
.cg-node.speaking .cg-label,.cg-node.listening .cg-label,.cg-node.listening-soft .cg-label{--drop-on:var(--drop)}
.cg-node:is(.speaking,.listening,.listening-soft) .cg-badge{--by:var(--by-on)}
.cg-node:is(.speaking,.listening,.listening-soft) .cg-typing{--ty:var(--ty-on);--tx:var(--tx-on)}
.cg.tight .cg-node:not(.speaking):not(.listening) .cg-label{opacity:0}
.cg.tight .cg-node .cg-label{max-width:none!important;z-index:2}
.cg-node.listening .cg-label,.cg-node.listening-soft .cg-label{color:var(--cg-listen)}
.cg-node.listening .cg-label{background:color-mix(in srgb,var(--cg-listen) 18%,rgba(11,16,32,.85))}
.cg-node.speaking .cg-label{background:color-mix(in srgb,var(--cg-speak) 16%,rgba(11,16,32,.85))}
.cg-dir{position:absolute;left:0;top:0;width:20px;height:20px;margin:-10px 0 0 -10px;border-radius:50%;display:grid;place-items:center;
  opacity:0;transition:opacity .3s ease;box-shadow:0 0 0 2px var(--cg-bg),0 0 12px var(--k);background:var(--k);--k:var(--cg-listen)}
.cg-dir svg{width:12px;height:12px;transform:rotate(calc(var(--a,0deg) + 180deg))}
.cg-node.speaking .cg-dir{--k:var(--cg-speak);opacity:1}
.cg-node.speaking .cg-dir svg{transform:rotate(var(--a,0deg))}
.cg-node.listening .cg-dir{opacity:1}.cg-node.listening-soft .cg-dir{opacity:.6}
.cg-badge{position:absolute;left:0;top:0;min-width:30px;height:30px;padding:0 8px;border-radius:15px;display:grid;place-items:center;
  font-size:16px;font-weight:700;color:#0b1020;box-shadow:0 2px 8px rgba(0,0,0,.4),0 0 0 2px var(--cg-bg);
  transition:transform .55s cubic-bezier(.2,.8,.2,1),opacity .3s ease;opacity:0}
.cg-badge.on{opacity:1}
.cg-badge>b{display:block;transition:transform .35s cubic-bezier(.3,1.6,.5,1);transform:scale(.4)}
.cg-badge.on>b{transform:scale(1)}
.cg-badge.agree{background:#4ade80}.cg-badge.disagree{background:#f87171}
.cg-badge.vote-a{background:#60a5fa}.cg-badge.vote-b{background:#fbbf24}
.cg-typing{position:absolute;left:0;top:0;display:flex;gap:5px;padding:8px 11px;border-radius:14px;background:var(--cg-panel);
  border:1px solid rgba(34,211,238,.35);box-shadow:0 4px 14px rgba(0,0,0,.4);opacity:0;transition:opacity .3s ease,transform .55s cubic-bezier(.2,.8,.2,1)}
.cg-typing span{width:8px;height:8px;border-radius:50%;background:var(--cg-speak);animation:cg-dot 1.1s ease-in-out infinite}
.cg-typing span:nth-child(2){animation-delay:.15s}.cg-typing span:nth-child(3){animation-delay:.3s}
@keyframes cg-dot{0%,80%,100%{opacity:.25;transform:translateY(0)}40%{opacity:1;transform:translateY(-3px)}}
.cg-node.typing .cg-typing{opacity:1}
.cg-node.typing .cg-ring{animation:cg-think 1.4s ease-in-out infinite}
@keyframes cg-think{50%{opacity:.35}}
.cg-hub-body{position:absolute;left:${-HUB_BASE / 2}px;top:${-HUB_BASE / 2}px;width:${HUB_BASE}px;height:${HUB_BASE}px;
  transform:scale(var(--k,1));transition:transform .55s cubic-bezier(.2,.8,.2,1)}
.cg-hub svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.cg-hub .orbit{transform-origin:50px 50px;animation:cg-spin 24s linear infinite paused;opacity:.55;transition:opacity .5s}
.cg-hub .orbit2{transform-origin:50px 50px;animation:cg-spin 36s linear infinite reverse paused;opacity:.3;transition:opacity .5s}
.cg-hub.active .orbit,.cg-hub.active .orbit2{animation-play-state:running}
.cg-hub.active .orbit{opacity:1}.cg-hub.active .orbit2{opacity:.7}
@keyframes cg-spin{to{transform:rotate(360deg)}}
.cg-hub .hex{fill:var(--cg-panel);stroke:rgba(34,211,238,.55);stroke-width:1.6;transition:stroke .4s}
.cg-hub.active .hex{stroke:var(--cg-speak)}
.cg-hub-core{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;transition:transform .3s}
.cg-hub-label{font-size:24px;font-weight:800;letter-spacing:.14em;margin-right:-.14em;color:var(--cg-ink)}
.cg-hub-sub{position:absolute;left:50%;top:100%;transform:translate(-50%,12px);font-size:calc(12.5px / var(--k,1));font-weight:700;letter-spacing:.06em;
  text-transform:uppercase;color:var(--cg-muted);max-width:calc(220px / var(--k,1));white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  padding:2px 8px;border-radius:9px;background:rgba(11,16,32,.8);border:1px solid var(--cg-line)}
.cg-hub-sub:empty{display:none}
.cg-ripple{position:absolute;left:-40px;top:-40px;width:80px;height:80px;border-radius:50%;border:2px solid var(--cg-speak);opacity:0;pointer-events:none}
.cg-bubble{position:absolute;left:0;top:0;z-index:5;padding:14px 18px 16px;border-radius:14px;background:rgba(18,26,46,.97);
  border:1px solid rgba(34,211,238,.4);box-shadow:0 12px 32px rgba(0,0,0,.5),0 0 0 1px rgba(0,0,0,.2),0 0 24px rgba(34,211,238,.12);
  opacity:0;transform:translate(var(--x),var(--y)) scale(.9);
  transform-origin:var(--ox) var(--oy);transition:opacity .28s ease,transform .38s cubic-bezier(.2,1.2,.4,1);pointer-events:none}
.cg-bubble.in{opacity:1;transform:translate(var(--x),var(--y)) scale(1)}
.cg-bubble.out{opacity:0;transform:translate(var(--x),var(--y)) scale(.96);transition-duration:.25s}
.cg-bubble-tail{position:absolute;width:12px;height:12px;background:inherit;border:inherit;border-width:0 0 1px 1px;transform:rotate(45deg)}
.cg-bubble-head{display:flex;align-items:baseline;gap:7px;font-size:clamp(15px,.8vw,19px);font-weight:800;margin-bottom:6px;white-space:nowrap;overflow:hidden}
.cg-bubble-head .who{color:var(--cg-speak)}
.cg-bubble-head .to{color:var(--cg-listen);overflow:hidden;text-overflow:ellipsis;font-weight:600}
.cg-bubble-head .arrow{color:var(--cg-muted);font-weight:500}
.cg-bubble.narrow .cg-bubble-head .meta{display:none}
.cg-bubble-head .meta{color:var(--cg-muted);font-weight:500;margin-left:auto;padding-left:8px;flex:none}
.cg-bubble-text{font-size:clamp(19px,1.1vw,26px);font-weight:500;line-height:1.38;color:var(--cg-ink);display:-webkit-box;-webkit-line-clamp:5;-webkit-box-orient:vertical;overflow:hidden}
@media (prefers-reduced-motion:reduce){.cg *{animation:none!important;transition-duration:.01ms!important}}`;

  let root, svg, gSpokes, gPackets, gRelations, layer, hubEl, hubBody, hubLabel, hubSub, ripple, bubble;
  let W = 0, H = 0, cx = 0, cy = 0, hx = 0, hy = 0, size = BASE, hubSize = HUB_BASE, labelW = 120;
  const nodes = new Map();          // id -> node record
  let order = [];
  let tween = null, raf = 0;
  const packets = [];
  let relations = [], relationTimer = 0;
  let timers = [], gen = 0, talkTimer = 0, lastSpeak = null;

  function injectCss() {
    if (document.getElementById("graph-css")) return;
    const s = el("style"); s.id = "graph-css"; s.textContent = CSS; document.head.appendChild(s);
  }

  function portrait(a) {
    if (window.Faces?.svg) { try { return window.Faces.svg(a); } catch (e) { console.warn("Faces.svg failed", e); } }
    return `<div class="cg-emoji">${esc(a.emoji || "👤")}</div>`;
  }

  function mount(container) {
    injectCss();
    if (root) root.remove();
    if (getComputedStyle(container).position === "static") container.style.position = "relative";
    root = el("div", "cg");
    root.appendChild(el("div", "cg-bg"));
    svg = sv("svg", { class: "cg-svg" });
    gSpokes = sv("g", {}); gPackets = sv("g", {});
    gRelations = sv("g", { class: "cg-relations" });
    svg.append(gSpokes, gPackets, gRelations);
    layer = el("div"); layer.style.cssText = "position:absolute;inset:0";
    hubEl = el("div", "cg-hub", `<div class="cg-hub-body">
      <svg viewBox="0 0 100 100" aria-hidden="true">
        <circle class="orbit" cx="50" cy="50" r="58" fill="none" stroke="var(--cg-speak)" stroke-width="1.2" stroke-dasharray="2 7" stroke-linecap="round"/>
        <circle class="orbit2" cx="50" cy="50" r="66" fill="none" stroke="var(--cg-listen)" stroke-width="1" stroke-dasharray="18 10 2 10"/>
        <path class="hex" d="${hexPath(50, 50, 46, 9)}"/>
      </svg>
      <div class="cg-hub-core"><div class="cg-hub-label"></div><div class="cg-hub-sub"></div></div></div>`);
    ripple = el("div", "cg-ripple"); hubEl.appendChild(ripple);
    hubBody = hubEl.querySelector(".cg-hub-body");
    hubLabel = hubEl.querySelector(".cg-hub-label"); hubSub = hubEl.querySelector(".cg-hub-sub");
    hubEl.title = "AGNTCY SLIM message bus";
    layer.appendChild(hubEl);
    root.append(svg, layer);
    container.appendChild(root);
    nodes.clear(); order = [];
    // Layout sizes, not getBoundingClientRect: the page may be CSS-zoomed on big screens.
    new ResizeObserver(() => { if (root.clientWidth !== W || root.clientHeight !== H) { W = root.clientWidth; H = root.clientHeight; layout(); } }).observe(root);
    W = root.clientWidth; H = root.clientHeight; layout(true);
  }

  // Rounded hexagon (pointy top) as a path.
  function hexPath(x, y, r, rr) {
    const pts = [...Array(6)].map((_, i) => { const t = Math.PI / 3 * i - Math.PI / 2; return [x + r * Math.cos(t), y + r * Math.sin(t)]; });
    let d = "";
    pts.forEach((p, i) => {
      const a = pts[(i + 5) % 6], b = pts[(i + 1) % 6], k = rr / r;
      const p1 = [p[0] + (a[0] - p[0]) * k, p[1] + (a[1] - p[1]) * k], p2 = [p[0] + (b[0] - p[0]) * k, p[1] + (b[1] - p[1]) * k];
      d += `${i ? "L" : "M"}${p1[0].toFixed(2)} ${p1[1].toFixed(2)}Q${p[0].toFixed(2)} ${p[1].toFixed(2)} ${p2[0].toFixed(2)} ${p2[1].toFixed(2)}`;
    });
    return d + "Z";
  }

  function makeNode(a) {
    const n = { id: a.id, a, x: cx, y: cy - 1, badge: null };
    n.el = el("div", "cg-node enter");
    n.el.innerHTML = `<div class="cg-typing"><span></span><span></span><span></span></div>
      <div class="cg-av"><div class="cg-pop"><div class="cg-glow gl"></div><div class="cg-glow gs"></div><div class="cg-ring"></div><div class="cg-face"></div></div></div>
      <div class="cg-dir"><svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 2.5 7 6 3 9.5" fill="none" stroke="#0b1020" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
      <div class="cg-label"></div><div class="cg-badge"><b></b></div>`;
    n.av = n.el.querySelector(".cg-av"); n.face = n.el.querySelector(".cg-face");
    n.label = n.el.querySelector(".cg-label"); n.badgeEl = n.el.querySelector(".cg-badge"); n.typ = n.el.querySelector(".cg-typing");
    n.dir = n.el.querySelector(".cg-dir");
    const g = sv("g", { class: "cg-spoke" });
    n.paths = ["base", "glow", "core", "flow"].map(c => { const p = sv("path", { class: c }); g.appendChild(p); return p; });
    n.spoke = g;
    gSpokes.appendChild(g);
    layer.appendChild(n.el);
    fill(n, a);
    return n;
  }

  function fill(n, a) {
    const key = `${a.id}|${a.color}|${a.emoji}|${a.short}`;
    n.a = a;
    n.el.style.setProperty("--c", a.color || "#8a96b8");
    n.el.title = a.title || a.short || a.id;
    if (n.key === key) return;
    n.key = key;
    n.face.innerHTML = portrait(a);
    n.label.innerHTML = `<i></i>${esc(a.short || a.id)}`;
  }

  function setAdvisors(list) {
    if (!root) return;
    clearRelations();
    list = Array.isArray(list) ? list.filter(a => a && a.id) : [];
    const keep = new Set(list.map(a => a.id));
    for (const [id, n] of nodes) if (!keep.has(id)) {
      nodes.delete(id); n.el.classList.add("leave"); n.spoke.style.transition = "opacity .5s"; n.spoke.style.opacity = 0;
      setTimeout(() => { n.el.remove(); n.spoke.remove(); }, 520);
    }
    const fresh = [];
    for (const a of list) { const n = nodes.get(a.id); if (n) fill(n, a); else { const m = makeNode(a); nodes.set(a.id, m); fresh.push(m); } }
    if (fresh.length) { void root.offsetWidth; fresh.forEach(n => n.el.classList.remove("enter")); }
    order = list.map(a => a.id);
    // A bubble or lighting that refers to a removed advisor would point at empty space.
    if (lastSpeak && [lastSpeak.speaker, ...lastSpeak.listeners].some(id => !nodes.has(id))) idle();
    layout();
  }

  // Equal arc-length points on an ellipse, starting at the top, clockwise.
  function ellipsePoints(n, rx, ry) {
    const M = 720, cum = [0];
    let px = 0, py = -ry;
    for (let i = 1; i <= M; i++) {
      const t = -Math.PI / 2 + 2 * Math.PI * i / M, x = rx * Math.cos(t), y = ry * Math.sin(t);
      cum.push(cum[i - 1] + Math.hypot(x - px, y - py)); px = x; py = y;
    }
    const P = cum[M], out = [];
    for (let k = 0, j = 0; k < n; k++) {
      const target = P * k / n;
      while (j < M && cum[j + 1] < target) j++;
      const f = (target - cum[j]) / ((cum[j + 1] - cum[j]) || 1), t = -Math.PI / 2 + 2 * Math.PI * (j + f) / M;
      out.push([rx * Math.cos(t), ry * Math.sin(t)]);
    }
    return { pts: out, P };
  }

  // Clearance the ring needs: the top node's label and the bottom node (scaled while
  // speaking) must stay GAP away from the hub's orbit (r .66) and its scaled sub-label (~.8).
  // A spoke leaving straight down puts the direction chip on the label: an active node drops its label below it.
  const chipDrop = s => Math.max(0, s * .56 + 4 + 13 - (s / 2 + 11));
  const ryNeed = (s, hub) => Math.max(s / 2 + 37 + chipDrop(s), s * .56 + 6) + GAP + hub * .8;
  const hubFor = (s, minDim) => clamp(Math.min(s * .85, minDim * .17), 44, 128);

  function layout(instant) {
    if (!root || !W || !H) return;
    cx = W / 2; cy = H / 2; hx = cx; hy = cy;
    const n = order.length, minDim = Math.min(W, H);
    let s = Math.min(n <= 5 ? 172 : 172 - (n - 5) * 6, minDim * (n <= 5 ? .24 : .16)), hub, pts;
    if (n === 1) {
      // A lone advisor and the hub stack as a centered vertical pair.
      s = Math.min(172, minDim * .26);
      let d, span;
      for (let it = 0; it < 12; it++) {
        hub = hubFor(s, minDim); d = s / 2 + 37 + chipDrop(s) + GAP + hub * .66; span = s * .56 + 6 + d + hub * .8;
        if (span <= H - 2 * PAD || s <= MIN_S) break;
        s = Math.max(MIN_S, s * .92);
      }
      const ny = cy - span / 2 + s * .56 + 6;
      hy = ny + d; pts = [[0, ny - cy]]; labelW = 190;
    } else {
      let rx, ry, gap = 0;
      for (let it = 0; it < 16; it++) {
        hub = hubFor(s, minDim);
        ry = Math.max(20, H / 2 - PAD - s / 2 - 37);
        rx = Math.max(20, Math.min(W / 2 - PAD - s / 2 - 40, ry * 1.5));
        const e = ellipsePoints(Math.max(n, 1), rx, ry); pts = e.pts; gap = e.P / Math.max(n, 1);
        // Every node (not only the axis ones) must clear the hub's orbit by GAP.
        const vFit = ry >= ryNeed(s, hub) && pts.every(([x, y]) => Math.hypot(x, y) - s * .56 - 6 >= hub * .7 + GAP);
        const aFit = n < 2 || gap >= s * 1.35;
        if ((vFit && aFit) || s <= MIN_S) break;
        s = Math.max(MIN_S, Math.min(vFit ? s : s * .92, aFit ? s : gap / 1.35));
      }
      labelW = clamp(gap - 10, s, 190);
    }
    // Keep the ring positions while shrinking councilors and their connection anchors.
    s *= .75;
    size = s; hubSize = hub;
    root.classList.toggle("tight", labelW < 84); // too crowded for every name: label only the active nodes
    hubEl.style.transform = `translate(${hx}px,${hy}px)`;
    hubBody.style.setProperty("--k", hubSize / HUB_BASE);
    const k = s / BASE;
    order.forEach((id, i) => {
      const nd = nodes.get(id); if (!nd || !pts[i]) return;
      nd.tx = cx + pts[i][0]; nd.ty = cy + pts[i][1];
      nd.av.style.setProperty("--k", k);
      nd.label.style.maxWidth = `${labelW}px`;
      const dx = hx - nd.tx, dy = hy - nd.ty, L = Math.hypot(dx, dy) || 1, rc = s * .56 + 4;
      const lh = Math.min(labelW, nd.label.offsetWidth || labelW) / 2;
      const drop = dy > 0 && Math.abs(rc * dx / L) < lh + 11 ? Math.max(0, rc * dy / L + 13 - (s / 2 + 11)) : 0;
      nd.el.style.setProperty("--drop", `${drop.toFixed(1)}px`);
      nd.label.style.transform = `translate(-50%,calc(${s / 2 + 11}px + var(--drop-on,0px)))`;
      // Badge (top-right) and typing dots (top-left) move to the bottom while the direction chip
      // is showing and would land on them.
      const cxp = rc * dx / L, cyp = rc * dy / L, by = -s * .5 - 2, ty = -s * .5 - 6, low = `${s * .5 - 24}px`;
      nd.el.style.setProperty("--by", `${by}px`); nd.el.style.setProperty("--ty", `${ty}px`);
      nd.el.style.setProperty("--by-on", Math.hypot(cxp - (s * .3 + 11), cyp - (by + 11)) < 32 ? low : `${by}px`);
      // The typing dots step out to the node's left side (not onto the name label below it).
      const tyHit = Math.hypot(cxp - (-s * .5 - 8), cyp - (ty + 11)) < 34;
      nd.el.style.setProperty("--ty-on", tyHit ? "-16px" : `${ty}px`);
      nd.el.style.setProperty("--tx-on", tyHit ? "-28px" : "0px");
      nd.badgeEl.style.transform = `translate(${s * .3}px,var(--by))`;
      nd.typ.style.transform = `translate(calc(${-s * .5 - 30}px + var(--tx,0px)),var(--ty))`;
    });
    const dur = instant || RM() || document.hidden ? 0 : 650;
    tween = { t0: performance.now(), dur, list: order.map(id => nodes.get(id)).filter(Boolean).map(nd => ({ nd, fx: nd.x, fy: nd.y })) };
    if (dur) kick(); else frame(performance.now()); // hidden tabs get no rAF: snap now
  }

  // Spoke from node edge to hub edge with a gentle clockwise bow.
  function spokeD(nd) {
    const dx = hx - nd.x, dy = hy - nd.y, L = Math.hypot(dx, dy) || 1, ux = dx / L, uy = dy / L;
    const r0 = size / 2 + 6, r1 = hubSize * .5 + 4;
    const x0 = nd.x + ux * r0, y0 = nd.y + uy * r0, x1 = hx - ux * r1, y1 = hy - uy * r1;
    // Direction chip sits where the spoke meets the node; its arrow follows the spoke.
    const rc = size * .56 + 4;
    nd.dir.style.transform = `translate(${(ux * rc).toFixed(1)}px,${(uy * rc).toFixed(1)}px)`;
    nd.dir.style.setProperty("--a", `${(Math.atan2(uy, ux) * 180 / Math.PI).toFixed(1)}deg`);
    const bow = Math.max(0, L - r0 - r1) * .07, mx = (x0 + x1) / 2 - uy * bow, my = (y0 + y1) / 2 + ux * bow;
    return `M${x0.toFixed(1)} ${y0.toFixed(1)}Q${mx.toFixed(1)} ${my.toFixed(1)} ${x1.toFixed(1)} ${y1.toFixed(1)}`;
  }

  function place(nd) {
    nd.el.style.transform = `translate(${nd.x.toFixed(2)}px,${nd.y.toFixed(2)}px)`;
    const d = spokeD(nd);
    for (const p of nd.paths) p.setAttribute("d", d);
    nd.len = null;
  }

  function kick() { if (!raf) raf = requestAnimationFrame(frame); }
  function frame(t) {
    raf = 0;
    let more = false;
    if (tween) {
      const p = tween.dur ? clamp((t - tween.t0) / tween.dur, 0, 1) : 1, e = ease(p);
      for (const { nd, fx, fy } of tween.list) { nd.x = fx + (nd.tx - fx) * e; nd.y = fy + (nd.ty - fy) * e; place(nd); }
      if (p >= 1) { tween = null; if (bubble && lastSpeak) placeBubble(bubble, lastSpeak.speaker); } else more = true;
    }
    placeRelations();
    for (const pk of packets) {
      const p = (t - pk.t0) / TRAVEL;
      if (p < 0 || p >= 1) { more = more || p < 0; continue; }
      const nd = pk.nd, path = nd.paths[2];
      if (nd.len == null) nd.len = path.getTotalLength();
      const q = ease(clamp(p, 0, 1)), at = path.getPointAtLength(nd.len * (pk.rev ? 1 - q : q));
      pk.g.setAttribute("transform", `translate(${at.x.toFixed(1)},${at.y.toFixed(1)})`);
      pk.g.style.opacity = p < .1 ? p * 10 : p > .9 ? (1 - p) * 10 : 1;
      more = true;
    }
    if (more) kick();
  }

  function packet(nd, rev, color, delay, done) {
    if (RM() || document.hidden) { later(() => done?.(), delay + TRAVEL); return; }
    const g = sv("g", { class: "cg-pk-g" });
    g.style.opacity = 0;
    g.append(sv("circle", { r: 8, fill: color, class: "cg-pk-halo" }), sv("circle", { r: 3.6, fill: color }), sv("circle", { r: 1.6, class: "cg-pk" }));
    gPackets.appendChild(g);
    const pk = { nd, rev, g, t0: performance.now() + delay };
    packets.push(pk);
    // Completion runs on a timer so a throttled rAF (hidden tab) never stalls the sequence.
    later(() => { g.remove(); const i = packets.indexOf(pk); if (i >= 0) packets.splice(i, 1); done?.(); }, delay + TRAVEL);
    kick();
  }

  function later(fn, ms) { const id = setTimeout(fn, ms); timers.push(id); return id; }
  function clearState() {
    timers.forEach(clearTimeout); timers = [];
    for (const pk of packets) pk.g.remove();
    packets.length = 0;
    for (const nd of nodes.values()) {
      nd.el.classList.remove("speaking", "listening", "listening-soft", "dim", "talking");
      nd.spoke.classList.remove("s", "l", "lb", "settled");
    }
    hubEl.classList.remove("active");
    clearTimeout(talkTimer);
  }

  function cssVar(name, fb) { return getComputedStyle(root).getPropertyValue(name).trim() || fb; }

  function speak({ speaker, to = [], text = "", meta = "", instant = false } = {}) {
    if (!root) return;
    const sp = nodes.get(speaker);
    clearState();
    gen++;
    if (!sp) { hideBubble(); return; }
    sp.el.classList.remove("typing");
    const targeted = (Array.isArray(to) ? to : [to]).filter(id => id !== speaker && nodes.has(id));
    const broadcast = !targeted.length;
    const listeners = broadcast ? order.filter(id => id !== speaker && nodes.has(id)) : [...new Set(targeted)];
    const fast = instant || RM();
    const speakC = cssVar("--cg-speak", "#22d3ee"), listenC = cssVar("--cg-listen", "#e879f9");
    sp.el.classList.add("speaking", "talking");
    const talkMs = fast ? 0 : clamp(900 + String(text).length * 38, 1200, 6500);
    if (talkMs) talkTimer = setTimeout(() => sp.el.classList.remove("talking"), talkMs); else sp.el.classList.remove("talking");
    for (const nd of nodes.values()) if (nd !== sp && !listeners.includes(nd.id)) nd.el.classList.add("dim");
    sp.spoke.classList.add("s");
    hubEl.classList.add("active");
    const lcls = broadcast ? "lb" : "l", ncls = broadcast ? "listening-soft" : "listening";
    lastSpeak = { speaker, listeners, broadcast };
    showBubble(sp, listeners, broadcast, text, meta, fast);
    const lit = [sp, ...listeners.map(id => nodes.get(id))];
    // Once delivered, the lines stay lit but stop flowing and the hub stops spinning.
    const settle = () => { for (const nd of lit) nd.spoke.classList.add("settled"); hubEl.classList.remove("active"); };
    if (fast) {
      for (const nd of lit.slice(1)) { nd.spoke.classList.add(lcls); nd.el.classList.add(ncls); }
      settle();
      return;
    }
    // Speaker -> hub, then every listener lights the moment the message reaches the hub;
    // the hub -> listener packets are decoration on top of an already lit state.
    const g = gen, stagger = Math.min(40, 240 / Math.max(1, listeners.length));
    packet(sp, false, speakC, 40, () => {
      if (g !== gen) return;
      pulseHub();
      lit.slice(1).forEach((nd, i) => later(() => {
        nd.spoke.classList.add(lcls); nd.el.classList.add(ncls);
        packet(nd, true, listenC, 0, i === lit.length - 2 ? () => { if (g === gen) settle(); } : null);
      }, i * stagger));
      if (!listeners.length) settle();
    });
  }

  /* ---------------- speech bubble ---------------- */
  function trimText(t, max = 210) {
    t = String(t || "").replace(/\s+/g, " ").trim();
    if (t.length <= max) return t;
    const cut = t.slice(0, max), sp = cut.lastIndexOf(" ");
    return `${(sp > max * .7 ? cut.slice(0, sp) : cut).replace(/[\s,.;:-]+$/, "")}…`;
  }

  function showBubble(sp, listeners, broadcast, text, meta, fast) {
    hideBubble();
    const body = trimText(text);
    if (!body) return;
    const names = !listeners.length ? "" : broadcast ? "the council" : listeners.map(id => nodes.get(id)?.a.short || id).join(", ");
    const b = el("div", "cg-bubble", `<div class="cg-bubble-tail"></div>
      <div class="cg-bubble-head"><span class="who">${esc(sp.a.short || sp.id)}</span>${names ? `<span class="arrow">to</span><span class="to">${esc(names)}</span>` : ""}${meta ? `<span class="meta">${esc(meta)}</span>` : ""}</div>
      <div class="cg-bubble-text">${esc(body)}</div>`);
    const bw = Math.round(clamp(W * .34, Math.min(260, W - 28), 540));
    b.style.width = `${bw}px`;
    if (bw < 320) b.classList.add("narrow"); // too tight for the round tag: keep the names readable
    layer.appendChild(b);
    bubble = b;
    placeBubble(b, sp.id);
    if (fast) b.classList.add("in");
    else { void b.offsetWidth; b.classList.add("in"); }
  }

  function hideBubble() {
    if (!bubble) return;
    const b = bubble; bubble = null;
    b.classList.remove("in"); b.classList.add("out");
    setTimeout(() => b.remove(), 300);
  }

  const overlap = (a, b) => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));

  function placeBubble(b, id) {
    const sp = nodes.get(id); if (!sp) return;
    const x = sp.tx ?? sp.x, y = sp.ty ?? sp.y;
    const bw = b.offsetWidth, bh = b.offsetHeight, r = size / 2 * 1.12 + 14, gap = 14; // gap matches the page gutter
    // Score bubble rects around the node (every 15deg): prefer outward, never over the
    // speaker, avoid the hub and the speaker's own spoke strongly and other nodes mildly.
    const out = Math.atan2(y - hy, x - hx) || 0;
    const hubR = { x: hx - hubSize * .66, y: hy - hubSize * .66, w: hubSize * 1.32, h: hubSize * 1.32 + 30 };
    const spoke = [];
    for (let t = .12; t < 1; t += .08) spoke.push([x + (hx - x) * t, y + (hy - y) * t]);
    const others = [];
    for (const nd of nodes.values()) {
      const nx = nd.tx ?? nd.x, ny = nd.ty ?? nd.y;
      others.push({ self: nd === sp, x: nx - size / 2 - 6, y: ny - size / 2 - 6, w: size + 12, h: size + LABEL_H + 14 });
    }
    let best = null;
    for (let k = 0; k < 24; k++) {
      const a = k * Math.PI / 12, dx = Math.cos(a), dy = Math.sin(a);
      const d = r + bw / 2 * Math.abs(dx) + bh / 2 * Math.abs(dy) + (dy > 0 ? LABEL_H * dy : 0);
      const c = { x: x + dx * d - bw / 2, y: y + dy * d - bh / 2 };
      const px = clamp(c.x, gap, W - bw - gap), py = clamp(c.y, gap, H - bh - gap);
      const rect = { x: px, y: py, w: bw, h: bh };
      const turn = Math.abs(Math.atan2(Math.sin(a - out), Math.cos(a - out)));
      let cost = turn * 900 + Math.abs(dy) * 300 + (Math.abs(px - c.x) + Math.abs(py - c.y)) + overlap(rect, hubR) * 8;
      for (const o of others) cost += overlap(rect, o) * (o.self ? 12 : 5);
      for (const [qx, qy] of spoke) if (qx > px - 4 && qx < px + bw + 4 && qy > py - 4 && qy < py + bh + 4) cost += 2500;
      if (!best || cost < best.cost) best = { cost, x: px, y: py };
    }
    const bx = best.x, by = best.y;
    // Tail: on the bubble edge facing the speaker.
    const dxL = bx - x, dxR = x - (bx + bw), dyT = by - y, dyB = y - (by + bh);
    const tail = b.querySelector(".cg-bubble-tail");
    const m = Math.max(dxL, dxR, dyT, dyB);
    let tx, ty, rot, ox, oy;
    if (m === dxL) { tx = -7; ty = clamp(y - by, 14, bh - 14) - 6; rot = 45; ox = 0; oy = ty + 6; }
    else if (m === dxR) { tx = bw - 5; ty = clamp(y - by, 14, bh - 14) - 6; rot = 225; ox = bw; oy = ty + 6; }
    else if (m === dyT) { tx = clamp(x - bx, 16, bw - 16) - 6; ty = -7; rot = 135; ox = tx + 6; oy = 0; }
    else { tx = clamp(x - bx, 16, bw - 16) - 6; ty = bh - 5; rot = 315; ox = tx + 6; oy = bh; }
    tail.style.transform = `translate(${tx}px,${ty}px) rotate(${rot}deg)`;
    tail.style.left = tail.style.top = "0";
    tail.style.display = m > 0 ? "" : "none";
    b.style.setProperty("--x", `${bx.toFixed(1)}px`); b.style.setProperty("--y", `${by.toFixed(1)}px`);
    b.style.setProperty("--ox", `${ox}px`); b.style.setProperty("--oy", `${oy}px`);
  }

  /* ---------------- small state APIs ---------------- */
  // Direct advisor-to-advisor links live independently of message-bus packets.
  function clearRelations() {
    clearTimeout(relationTimer); relationTimer = 0;
    relations = []; gRelations?.replaceChildren();
  }
  function placeRelations() {
    for (const edge of relations) {
      const a = nodes.get(edge.from), b = nodes.get(edge.to);
      if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
      const inset = Math.min(size * .6 + 8, len / 3);
      const x1 = a.x + dx / len * inset, y1 = a.y + dy / len * inset;
      const x2 = b.x - dx / len * inset, y2 = b.y - dy / len * inset;
      // Bow away from the hub so its label does not cover the connection.
      const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
      const sign = (mx - hx) * (-dy) + (my - hy) * dx >= 0 ? 1 : -1;
      const bend = Math.min(60, len * .16) * sign;
      const qx = mx - dy / len * bend, qy = my + dx / len * bend;
      for (const path of edge.paths) path.setAttribute("d", `M${x1} ${y1}Q${qx} ${qy} ${x2} ${y2}`);
      edge.label.setAttribute("x", (mx + qx) / 2);
      edge.label.setAttribute("y", (my + qy) / 2 - 8);
    }
  }
  function flashRelations(edges = []) {
    if (!root) return;
    clearRelations();
    const seen = new Set();
    for (const edge of edges) {
      const key = [edge.from, edge.to].sort().join("|");
      if (edge.from === edge.to || !nodes.has(edge.from) || !nodes.has(edge.to) || seen.has(key)) continue;
      seen.add(key);
      const group = sv("g", { class: `cg-relation ${edge.agree ? "agree" : "disagree"}` });
      const title = sv("title", {});
      title.textContent = `${nodes.get(edge.from).a.short} ${edge.agree ? "backs" : "does not back"} ${nodes.get(edge.to).a.short}’s plan`;
      const paths = [sv("path", { class: "relation-halo" }), sv("path", { class: "relation-line" })];
      const label = sv("text", { class: "relation-label", "text-anchor": "middle" });
      label.textContent = edge.agree ? "✓" : "×";
      group.append(title, ...paths, label); gRelations.append(group);
      relations.push({ ...edge, paths, label });
    }
    placeRelations();
    relationTimer = setTimeout(clearRelations, 1200);
  }

  function typing(id) {
    if (!root) return;
    for (const nd of nodes.values()) nd.el.classList.toggle("typing", nd.id === id);
  }

  function idle() {
    if (!root) return;
    clearRelations();
    gen++; clearState(); hideBubble(); lastSpeak = null;
    for (const nd of nodes.values()) nd.el.classList.remove("typing");
  }

  function setBadge(id, badge) {
    const nd = nodes.get(id); if (!nd) return;
    const b = nd.badgeEl;
    if (!badge) { b.classList.remove("on"); return; }
    b.className = `cg-badge ${badge.cls || ""}`;
    b.title = badge.title || "";
    b.firstElementChild.textContent = badge.text ?? "";
    void b.offsetWidth; // restart the pop
    b.classList.add("on");
  }

  function setHub({ label, sub } = {}) {
    if (!root) return;
    if (label != null) hubLabel.textContent = label;
    if (sub != null) { hubSub.textContent = sub; hubSub.title = sub; }
  }

  function pulseHub() {
    if (!root || RM()) return;
    ripple.animate([{ transform: "scale(.7)", opacity: .75 }, { transform: `scale(${hubSize / 80 * 1.9})`, opacity: 0 }], { duration: 900, easing: "cubic-bezier(.2,.7,.3,1)" });
    hubEl.querySelector(".cg-hub-core").animate([{ transform: "scale(1)" }, { transform: "scale(1.08)" }, { transform: "scale(1)" }], { duration: 380, easing: "ease-out" });
  }

  window.CouncilGraph = { mount, setAdvisors, speak, typing, idle, setBadge, setHub, pulseHub, flashRelations };
})();
