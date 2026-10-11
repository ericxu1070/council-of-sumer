// Illustrated SVG face portraits. Hand-designed personas + deterministic procedural faces.
// API: Faces.svg(advisor, {size, title}) -> "<svg ...>"; Faces.PERSONAS.
(() => {
  "use strict";

  let seq = 0;
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // --- color helpers ---
  const hex = c => /^#[\da-f]{6}$/i.test(String(c || "")) ? c : "#7c8cff";
  const rgb = c => [1, 3, 5].map(i => parseInt(hex(c).slice(i, i + 2), 16));
  const mix = (a, b, t) => {
    const A = rgb(a), B = rgb(b);
    return "#" + A.map((v, i) => Math.round(v + (B[i] - v) * t).toString(16).padStart(2, "0")).join("");
  };
  const dark = (c, t = .25) => mix(c, "#1a0f14", t);
  const lite = (c, t = .25) => mix(c, "#ffffff", t);

  // --- deterministic RNG from id ---
  const hash = s => { let h = 2166136261; for (const ch of String(s)) { h ^= ch.codePointAt(0); h = Math.imul(h, 16777619); } return h >>> 0; };
  const rng = seed => () => { seed = (seed + 0x6D2B79F5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  const pick = (r, a) => a[Math.floor(r() * a.length)];

  const INK = "#221a26", GOLD = "#e8b83a", GOLD_D = "#a87a1c";

  // --- parts (viewBox 0 0 100 100; head center 50,47 rx18 ry21) ---
  const HEAD = `<ellipse cx="50" cy="47" rx="18" ry="21"/>`;

  function body(f) {
    const g = f.garment, gd = dark(g, .32);
    return `<path d="M43 58 L57 58 L58 76 L42 76 Z" fill="${dark(f.skin, .18)}"/>
<ellipse cx="50" cy="66" rx="8" ry="4" fill="${dark(f.skin, .32)}" opacity=".7"/>
<path d="M10 104 C11 86 27 76 42 73.5 L50 82 L58 73.5 C73 76 89 86 90 104 Z" fill="${g}"/>
<path d="M63 75.5 C76 79 88 88 90 104 L72 104 C71 92 68 82 63 75.5 Z" fill="${gd}" opacity=".55"/>
<path d="M42 73.5 L50 82 L58 73.5" fill="none" stroke="${lite(g, .35)}" stroke-width="1.6" stroke-linejoin="round"/>
${f.apron ? `<path d="M41 84 Q50 87 59 84 L62 104 L38 104 Z" fill="#f3ead8"/><path d="M41 84 Q50 87 59 84" fill="none" stroke="#d8cbb0" stroke-width="1.2"/>` : ""}
${f.necklace ? `<path d="M41 75 Q50 86 59 75" fill="none" stroke="${GOLD}" stroke-width="2"/><circle cx="50" cy="82.5" r="2.2" fill="${GOLD}" stroke="${GOLD_D}" stroke-width=".8"/>` : ""}`;
  }

  function ears(f) {
    const s = dark(f.skin, .2);
    return `<ellipse cx="32.3" cy="49" rx="3.6" ry="5.2" fill="${f.skin}"/><ellipse cx="67.7" cy="49" rx="3.6" ry="5.2" fill="${s}"/>
<path d="M32.6 46.5 Q31 49 32.8 51.5" fill="none" stroke="${s}" stroke-width="1.1" stroke-linecap="round"/>
${f.earring ? `<circle cx="67.6" cy="55.6" r="1.8" fill="none" stroke="${GOLD}" stroke-width="1.2"/>` : ""}`;
  }

  function head(f, id) {
    return `<g>${HEAD.replace("/>", ` fill="${f.skin}"/>`)}
<g clip-path="url(#${id}h)"><ellipse cx="69" cy="50" rx="10" ry="26" fill="${dark(f.skin, .22)}" opacity=".55"/>
<ellipse cx="50" cy="71" rx="16" ry="5" fill="${dark(f.skin, .25)}" opacity=".35"/>
${f.headShade ? `<ellipse cx="50" cy="${f.headShade}" rx="24" ry="7" fill="${dark(f.skin, .45)}" opacity=".5"/>` : ""}</g></g>`;
  }

  function hairBack(f) {
    const h = f.hair, hd = dark(h, .25);
    if (f.hairStyle === "long") return `<path d="M30 46 C27 28 39 21 50 21 C61 21 73 28 70 46 L73 73 C67 78 60 77 57 72 L43 72 C40 77 33 78 27 73 Z" fill="${hd}"/>`;
    if (f.hairStyle === "bun") return `<circle cx="50" cy="21" r="7.5" fill="${h}"/><path d="M45 19 Q50 15 55 19" fill="none" stroke="${hd}" stroke-width="1.2"/>`;
    return "";
  }

  function hairFront(f) {
    const h = f.hair, hd = dark(h, .25), hl = lite(h, .18);
    switch (f.hairStyle) {
      case "short": return `<path d="M31 46 C29 30 39 24 50 24 C61 24 71 30 69 46 C67 38 63 34 57 33 C50 36 41 36 35 38 C33 40 32 43 31 46 Z" fill="${h}"/><path d="M42 28 Q50 26 58 28" fill="none" stroke="${hl}" stroke-width="1.2" stroke-linecap="round" opacity=".7"/>`;
      case "side": case "long": case "bun": return `<path d="M31 48 C28 30 40 23 51 23 C63 23 72 31 69 48 C67 40 64 35 60 33 C52 38 43 32 38 34 C34 38 32 43 31 48 Z" fill="${h}"/><path d="M60 33 C55 30 48 30 44 32" fill="none" stroke="${hd}" stroke-width="1.2" stroke-linecap="round"/>`;
      case "slick": return `<path d="M31 45 C30 29 40 24 50 24 C61 24 70 29 69 45 C67 37 62 33 50 33 C40 33 34 37 31 45 Z" fill="${h}"/><path d="M38 30 Q50 27 62 31 M40 33 Q50 30 60 33" fill="none" stroke="${hl}" stroke-width="1" opacity=".55"/>`;
      case "crop": return `<path d="M32 41 C32 30 40 26 50 26 C60 26 68 30 68 41 C62 35 38 35 32 41 Z" fill="${h}"/>`;
      case "curly": {
        let s = "";
        for (let i = 0; i <= 10; i++) {
          const a = Math.PI * (1.05 + .9 * i / 10), x = 50 + 19 * Math.cos(a), y = 44 + 19 * Math.sin(a);
          s += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="5.4" fill="${i % 2 ? h : hd}"/>`;
        }
        return `<path d="M33 42 C34 30 42 27 50 27 C58 27 66 30 67 42 C60 36 40 36 33 42 Z" fill="${h}"/>${s}`;
      }
      default: return "";
    }
  }

  function headwear(f) {
    const c = f.hatColor, cd = dark(c, .25), cl = lite(c, .25);
    switch (f.headwear) {
      case "cap": return `<path d="M31 39 C31 24 40 18 50 18 C60 18 69 24 69 39 Z" fill="${c}"/><path d="M58 20 C65 24 69 30 69 39 L62 39 C62 30 60 24 58 20 Z" fill="${cd}" opacity=".6"/><path d="M30 36 Q50 32 70 36 L70 41 Q50 37 30 41 Z" fill="${cd}"/>`;
      case "band": return `<path d="M31 37 Q50 31 69 37 L69 41 Q50 35 31 41 Z" fill="${c}"/><circle cx="50" cy="34.2" r="2.6" fill="${GOLD}" stroke="${GOLD_D}" stroke-width=".8"/>`;
      case "turban": return `<path d="M28 40 C26 22 38 13 50 13 C62 13 74 22 72 40 C66 34 58 32 50 32 C42 32 34 34 28 40 Z" fill="${c}"/>
<path d="M30 33 C38 24 56 20 70 28 M29 37 C40 28 60 27 71 35 M34 22 C44 26 58 32 67 38" fill="none" stroke="${cd}" stroke-width="1.6" stroke-linecap="round"/>
<path d="M37 18 C44 15 52 15 58 16" fill="none" stroke="${cl}" stroke-width="1.6" stroke-linecap="round" opacity=".8"/>
<path d="M50 24.5 L53.5 29 L50 33.5 L46.5 29 Z" fill="#c8323c" stroke="${GOLD}" stroke-width="1.2"/>`;
      case "circlet": return `<path d="M31 37.5 Q50 32 69 37.5 L69 41.5 Q50 36 31 41.5 Z" fill="${GOLD}"/><path d="M31 41.5 Q50 36 69 41.5" fill="none" stroke="${GOLD_D}" stroke-width="1"/>
<path d="M44 34.8 L50 25 L56 34.8 Z" fill="${GOLD}" stroke="${GOLD_D}" stroke-width=".8" stroke-linejoin="round"/><circle cx="50" cy="31.5" r="2" fill="#2fb3a3"/><circle cx="38" cy="37" r="1.1" fill="#fff6" /><circle cx="62" cy="37" r="1.1" fill="#fff6"/>`;
      default: return "";
    }
  }

  function beard(f) {
    const b = f.beardColor, bd = dark(b, .3);
    switch (f.beard) {
      case "full": {
        let curls = "";
        for (const [y, xs] of [[68, [41, 46, 50, 54, 59]], [73, [43, 47.5, 52.5, 57]], [78, [46, 50, 54]]])
          for (const x of xs) curls += `<path d="M${x - 1.8} ${y} q1.8 -2.4 3.6 0" fill="none" stroke="${bd}" stroke-width="1" stroke-linecap="round"/>`;
        return `<path d="M32 49 C31 68 38 82 50 84 C62 82 69 68 68 49 C66 58 62 62 57 61.5 Q50 58.5 43 61.5 C38 62 34 58 32 49 Z" fill="${b}"/>${curls}
<path d="M42.5 61.5 Q50 57 57.5 61.5 Q50 60 42.5 61.5 Z" fill="${bd}"/>`;
      }
      case "goatee": return `<path d="M44.5 65.5 Q50 78 55.5 65.5 Q50 67.5 44.5 65.5 Z" fill="${b}"/><path d="M43 61.5 Q50 57.5 57 61.5 Q50 60.5 43 61.5 Z" fill="${b}"/>`;
      case "stubble": return `<path d="M33 52 C34 66 41 69 50 69 C59 69 66 66 67 52 C64 61 59 64 50 64 C41 64 36 61 33 52 Z" fill="${b}" opacity=".28"/>`;
      default: return "";
    }
  }

  function eyes(f, delay) {
    const one = (x, mirror) => {
      switch (f.eyes) {
        case "happy": return `<path d="M${x - 2.6} 50.2 Q${x} 46.6 ${x + 2.6} 50.2" fill="none" stroke="${INK}" stroke-width="1.8" stroke-linecap="round"/>`;
        case "lidded": return `<ellipse cx="${x}" cy="50" rx="2.5" ry="1.7" fill="${INK}"/><circle cx="${x + .8}" cy="49.6" r=".6" fill="#fff"/><path d="M${x - 3} 49.2 Q${x} 47.6 ${x + 3} 49.2" fill="none" stroke="${INK}" stroke-width="1.3" stroke-linecap="round"/>`;
        case "glow": return `<path d="M${x - 2.8} 49.5 Q${x} ${mirror ? 47.6 : 47.6} ${x + 2.8} 49.5 Q${x} 51.2 ${x - 2.8} 49.5 Z" fill="#ffd95a"/><circle cx="${x}" cy="49.4" r=".9" fill="${INK}"/>`;
        default: return `<ellipse cx="${x}" cy="49.5" rx="2.4" ry="2.9" fill="${INK}"/><circle cx="${x + .8}" cy="48.4" r=".9" fill="#fff"/>`;
      }
    };
    return `<g class="face-eyes" style="--blink-delay:${delay}s">${one(42.5)}${one(57.5, 1)}</g>`;
  }

  function brows(f) {
    const c = f.browColor, w = `fill="none" stroke="${c}" stroke-width="2" stroke-linecap="round"`;
    const P = {
      neutral: ["M39 43.5 Q42.5 41.8 46 43.2", "M54 43.2 Q57.5 41.8 61 43.5"],
      raised: ["M39 42 Q42.5 39.6 46 41.6", "M54 41.6 Q57.5 39.6 61 42"],
      soft: ["M39.5 43 Q42.5 42.2 45.5 43.6", "M54.5 43.6 Q57.5 42.2 60.5 43"],
      angled: ["M39 42 Q43 42.6 46.5 44.6", "M53.5 44.6 Q57 42.6 61 42"],
      smug: ["M39 43.6 Q42.5 42.6 46 43.6", "M54 41.8 Q57.5 38.8 61 41.4"],
    }[f.brows] || [];
    return P.map(d => `<path d="${d}" ${w}/>`).join("");
  }

  function mouth(f) {
    const LIP = f.beard === "full" ? "#d27c74" : f.lip || "#7a3438"; // lighter lips read on a dark beard
    const closed = {
      smile: `<path d="M44.5 61.8 Q50 66.2 55.5 61.8" fill="none" stroke="${LIP}" stroke-width="1.8" stroke-linecap="round"/>`,
      grin: `<path d="M43.8 61.2 Q50 69 56.2 61.2 Q50 63 43.8 61.2 Z" fill="${LIP}"/><path d="M45.5 62.1 Q50 63.4 54.5 62.1 L54 63.4 Q50 64.6 46 63.4 Z" fill="#fff"/>`,
      smirk: `<path d="M45 63 Q50.5 64.6 55.8 60.8" fill="none" stroke="${LIP}" stroke-width="1.8" stroke-linecap="round"/>`,
      flat: `<path d="M46 63 Q50 63.8 54 63" fill="none" stroke="${LIP}" stroke-width="1.8" stroke-linecap="round"/>`,
    }[f.mouth] || "";
    return `<g class="face-mouth">${closed}</g>
<g class="face-mouth-open" opacity="0"><path d="M45 61.6 Q50 60.6 55 61.6 Q54.2 68.4 50 68.4 Q45.8 68.4 45 61.6 Z" fill="#3a1820"/><ellipse cx="50" cy="66.6" rx="3" ry="1.4" fill="#d9677a"/><path d="M46.2 62 Q50 61.4 53.8 62 L53.6 63 Q50 62.6 46.4 63 Z" fill="#fff"/></g>`;
  }

  function extras(f) {
    let s = "";
    if (f.cheeks) s += `<circle cx="38" cy="56.5" r="3.4" fill="#ff6b7a" opacity=".22"/><circle cx="62" cy="56.5" r="3.4" fill="#ff6b7a" opacity=".22"/>`;
    if (f.freckles) for (const [x, y] of [[37, 55], [39.5, 57], [36.5, 58], [63, 55], [60.5, 57], [63.5, 58]]) s += `<circle cx="${x}" cy="${y}" r=".65" fill="${dark(f.skin, .45)}"/>`;
    return s;
  }
  const nose = f => `<path d="M50.6 51 Q52.6 55.6 49.4 57.2" fill="none" stroke="${dark(f.skin, .3)}" stroke-width="1.4" stroke-linecap="round"/>`;

  // --- hand-designed personas ---
  const PERSONAS = {
    provider: g => ({ // baker / caretaker: cream headscarf, apron, warm smile
      skin: "#e7b48e", hair: "#6a3f24", hairStyle: "side", eyes: "happy", brows: "soft", mouth: "grin", cheeks: 1, apron: 1,
      custom(f, id) {
        const c = "#f3ead8", cd = "#d6c7a8";
        return {
          back: `<path d="M29 44 C27 25 39 17 50 17 C61 17 73 25 71 44 L74 74 C68 80 61 78 58 73 L42 73 C39 78 32 80 26 74 Z" fill="${cd}"/>`,
          top: `<path d="M30 45 C28 25 39 18 50 18 C61 18 72 25 70 45 C67 35 59 31 50 31 C41 31 33 35 30 45 Z" fill="${c}"/>
<path d="M33 30 C40 25 60 25 67 30 M31 37 C40 30 60 30 69 37" fill="none" stroke="${cd}" stroke-width="1.4" stroke-linecap="round"/>
<path d="M66 27 Q74 22 76 27 Q72 30 67 31 Z" fill="${c}" stroke="${cd}" stroke-width="1"/>`,
        };
      },
    }),
    farmer: g => ({ // straw hat, sun-tanned, freckles, grin, wheat stalk
      skin: "#b97a50", hair: "#4a2c1a", hairStyle: "crop", eyes: "round", brows: "raised", mouth: "grin", beard: "stubble", beardColor: "#3a2416", freckles: 1, headShade: 31,
      custom() {
        const s = "#e6b85c", sd = "#b98a38", sl = "#f6d68c";
        let weave = "";
        for (let x = 40; x <= 60; x += 5) weave += `<path d="M${x} 16 L${x + (x - 50) * .25} 30" stroke="${sd}" stroke-width=".9" opacity=".7"/>`;
        return {
          top: `<ellipse cx="50" cy="31.5" rx="31" ry="7.5" fill="${sd}"/><ellipse cx="50" cy="30.5" rx="30" ry="6.5" fill="${s}"/>
<path d="M36 31 C35 19 41 13.5 50 13.5 C59 13.5 65 19 64 31 Z" fill="${s}"/><path d="M57 15 C62 18 65 23 64 31 L58 31 C59 24 59 19 57 15 Z" fill="${sd}" opacity=".55"/>${weave}
<path d="M36 26.5 Q50 29.5 64 26.5 L64 30.6 Q50 33.4 36 30.6 Z" fill="#8a4b2a"/>
<path d="M22 31 Q50 39 78 31" fill="none" stroke="${sl}" stroke-width="1" opacity=".8"/>`,
          front: `<path d="M55 63 L69 56.5" stroke="#d9b45a" stroke-width="1.3" stroke-linecap="round"/><path d="M68 57 l3.5 -2.6 M68.4 57.8 l3.8 -.6 M67.4 56.4 l1.6 -3.6" stroke="#e6c46a" stroke-width="1.3" stroke-linecap="round"/>`,
        };
      },
    }),
    merchant: g => ({ // turban, groomed goatee, knowing raised brow, earring
      skin: "#c88d60", hair: "#1f1612", hairStyle: "none", eyes: "round", brows: "smug", mouth: "smile", beard: "goatee", beardColor: "#1f1612", earring: 1,
      headwear: "turban", hatColor: "#f1e7d0",
    }),
    priest: g => ({ // gold circlet, curled royal beard, smug lidded eyes, smirk, gold chain
      skin: "#dba67a", hair: "#16110f", hairStyle: "slick", eyes: "lidded", brows: "smug", mouth: "smirk", beard: "full", beardColor: "#1d1512", necklace: 1, earring: 1,
      headwear: "circlet",
    }),
    villager: g => ({ // simple cap, open expression, practical clothing
      skin: "#cfa58a", hair: "#3c281b", hairStyle: "slick", eyes: "round",
      brows: "soft", mouth: "smile", headwear: "turban", hatColor: "#b7a484",
    }),
  };

  // --- procedural faces for custom ids ---
  const SKINS = ["#f6d3b3", "#eebb94", "#dba67a", "#c88d60", "#a8693f", "#8a532f", "#6b3e23", "#4f2c19"];
  const HAIRS = ["#1f1a17", "#3a2418", "#5b3a22", "#8a5a2b", "#b5763a", "#7b7b84"];
  const HATS = ["#efe6d2", "#3b4f8a", "#8a2f3b", "#2f6b5a", "#c99a2e", "#5b3f8a", "#b55a2e"];
  function procedural(id) {
    const r = rng(hash(id));
    const hairStyle = pick(r, ["short", "side", "curly", "long", "bun", "crop", "bald", "slick"]);
    const hair = pick(r, HAIRS);
    let headwear = pick(r, ["none", "none", "none", "cap", "band", "turban", "circlet"]);
    if (headwear === "turban" && (hairStyle === "long" || hairStyle === "bun")) headwear = "band";
    return {
      skin: pick(r, SKINS), hair, hairStyle, headwear, hatColor: pick(r, HATS),
      beard: pick(r, ["none", "none", "full", "goatee", "stubble"]), beardColor: hair,
      eyes: pick(r, ["round", "round", "round", "happy", "lidded"]),
      brows: pick(r, ["neutral", "raised", "soft", "angled", "smug"]),
      mouth: pick(r, ["smile", "smile", "grin", "smirk", "flat"]),
      cheeks: r() < .4, freckles: r() < .15, earring: r() < .2, necklace: r() < .2,
    };
  }

  function build(advisor) {
    const a = advisor || {}, pid = String(a.id || "someone").toLowerCase();
    const hint = String(a.persona || "").toLowerCase();
    const own = k => Object.hasOwn(PERSONAS, k) ? PERSONAS[k] : null;
    const color = hex(a.color), persona = own(pid) || own(hint);
    const f = Object.assign({ beard: "none", headwear: "none", hairStyle: "short", eyes: "round", brows: "neutral", mouth: "smile" },
      persona ? persona(color) : procedural(pid));
    // Later copies of a persona keep its design but get their own skin and hair.
    if (persona && !own(pid)) { const r = rng(hash(pid)); f.skin = pick(r, SKINS); f.hair = pick(r, HAIRS); if (f.beardColor) f.beardColor = f.hair; }
    f.garment = color;
    f.browColor = f.browColor || dark(f.hair === "#000" ? "#2a1d17" : f.hair, .15);
    f.beardColor = f.beardColor || f.hair;
    f.hatColor = f.hatColor || "#efe6d2";
    return { f, color, pid };
  }

  function svg(advisor, opts = {}) {
    const { f, color, pid } = build(advisor);
    const id = `fc${++seq}`, delay = ((hash(pid) % 4000) / 1000).toFixed(2);
    const c = f.custom ? f.custom(f, id) : {};
    const size = opts.size ? ` width="${+opts.size || 0}" height="${+opts.size || 0}"` : "";
    const label = opts.title ?? (advisor && (advisor.title || advisor.id)) ?? "";
    return `<svg class="face" viewBox="0 0 100 100"${size} xmlns="http://www.w3.org/2000/svg" role="img" aria-label="${esc(label)}">${label ? `<title>${esc(label)}</title>` : ""}
<defs><clipPath id="${id}c"><circle cx="50" cy="50" r="50"/></clipPath><clipPath id="${id}h">${HEAD}</clipPath>
<radialGradient id="${id}g" cx="50%" cy="38%" r="70%"><stop offset="0" stop-color="${mix(color, "#0b1020", .25)}"/><stop offset="1" stop-color="${mix(color, "#0b1020", .72)}"/></radialGradient></defs>
<g clip-path="url(#${id}c)"><rect width="100" height="100" fill="url(#${id}g)"/>
<circle cx="50" cy="44" r="34" fill="${lite(color, .3)}" opacity=".12"/>
<g transform="matrix(1.18 0 0 1.18 -9 -9)">${c.back || ""}${hairBack(f)}${body(f)}${ears(f)}${head(f, id)}${extras(f)}${nose(f)}${beard(f)}${eyes(f, delay)}${c.mask || ""}${brows(f)}${mouth(f)}
${hairFront(f)}${headwear(f)}${c.top || ""}${c.front || ""}</g>
<circle cx="50" cy="50" r="49.3" fill="none" stroke="#fff" stroke-opacity=".1" stroke-width="1.4"/></g></svg>`;
  }

  function injectCss() {
    if (typeof document === "undefined" || document.getElementById("faces-css")) return;
    const st = document.createElement("style");
    st.id = "faces-css";
    st.textContent = `
.face{display:block;width:100%;height:100%;overflow:visible}
.face .face-eyes{transform-box:fill-box;transform-origin:center;animation:face-blink 5.2s infinite;animation-delay:var(--blink-delay,0s)}
.face .face-mouth-open{opacity:0}
.talking .face .face-mouth{animation:face-talk-c .36s steps(1) infinite}
.talking .face .face-mouth-open{animation:face-talk-o .36s steps(1) infinite}
@keyframes face-blink{0%,94%,100%{transform:scaleY(1)}96.5%{transform:scaleY(.1)}}
@keyframes face-talk-o{0%{opacity:1}50%{opacity:0}}
@keyframes face-talk-c{0%{opacity:0}50%{opacity:1}}
@media (prefers-reduced-motion:reduce){.face .face-eyes{animation:none}.talking .face .face-mouth{animation:none;opacity:0}.talking .face .face-mouth-open{animation:none;opacity:1}}`;
    (document.head || document.documentElement).appendChild(st);
  }
  injectCss();

  window.Faces = { svg, PERSONAS: Object.fromEntries(Object.keys(PERSONAS).map(k => [k, k])) };
})();
