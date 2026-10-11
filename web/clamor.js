/* Clamoring: while the council debates, advisors shout short demands ("Sell the land!")
 * that follow the plan each one currently backs. Shouts are small bubbles over the
 * graph nodes plus quiet, overlapping clips in each advisor's own voice. Everything is
 * fire-and-forget: clamor never delays the council, the speech bubbles, or the narrator.
 * The phrase table and the audio come from /api/clamor (see src/sumer/clamor.py). */
(() => {
  "use strict";
  const KEY = "sumer-clamor";
  const WAVE_MS = 2100;     // one wave of shouts this often
  const STAGGER_MS = 520;   // shouts in a wave start this far apart, so they overlap
  const SHOW_MS = 2000;     // how long a shout bubble stays up
  const VOLUME = 0.22;      // well under the narrator (1.0)
  const MAX_AUDIO = 3;      // never more clips than this at once

  let on = false;
  try { on = localStorage.getItem(KEY) === "1"; } catch { /* storage blocked */ }
  let phrases = null, voices = [];
  let debating = false, speaker = "", wave = 0, timer = 0;
  const positions = new Map(); // advisor id -> { stances: [...], n: shouts so far }
  const lastShout = new Map(); // advisor id -> wave number of its last shout
  const failed = new Set();    // clip URLs the server could not voice; never retried
  const playing = new Set();

  fetch("/api/clamor").then(r => (r.ok ? r.json() : null)).then(d => {
    if (!d) return;
    phrases = d.phrases || null;
    voices = Array.isArray(d.voices) ? d.voices : [];
  }).catch(() => { /* clamor stays silent and bubble-less without the table */ });

  // app.js keeps the council state in a global `S` (a top-level const shared by classic scripts).
  const app = () => (typeof S !== "undefined" ? S : null);

  /* ---------------- stance from the plan ---------------- */
  // Each stance gets a salience score from how strongly the plan pushes that way;
  // an advisor alternates between its top two. Mirrors the game rules in game.py.
  // A preset persona leans toward the part of the plan it cares about (custom agents
  // follow the plan alone), so a shared plan still sounds like different people.
  const FOOD_PER_PERSON = 20, ACRES_PER_BUSHEL = 2, ACRES_PER_WORKER = 10;
  const CARES = { provider: ["feed"], farmer: ["plant"], merchant: ["buy_land", "sell_land", "keep_land"], priest: ["store"] };
  function stancesFor(plan, state, persona = "") {
    if (!plan) return [];
    const pop = Math.max(1, +state?.population || 100);
    const grain = Math.max(1, +state?.grain || 2800);
    const acres = +state?.acres || 1000;
    const price = +state?.land_price || 20;
    const land = +plan.land_delta || 0, feed = +plan.feed || 0, plant = +plan.plant || 0;
    const scored = [];
    const trade = Math.min(1, Math.abs(land) * price / grain);
    if (land > 0) scored.push(["buy_land", 0.35 + trade * 2]);
    else if (land < 0) scored.push(["sell_land", 0.35 + trade * 2]);
    else scored.push(["keep_land", 0.2]);
    const ration = feed / (pop * FOOD_PER_PERSON);
    if (ration < 0.5) scored.push(["starve", 1.6]);
    else if (ration < 0.95) scored.push(["ration", 0.6 + (1 - ration) * 2]);
    else scored.push(["feed", 0.5 + Math.min(0.4, (ration - 1) * 2)]);
    const capacity = Math.max(1, Math.min(acres + land, pop * ACRES_PER_WORKER));
    const sown = plant / capacity;
    if (sown >= 0.95) scored.push(["plant", 0.6]);
    else if (sown <= 0.6) scored.push(["fallow", 1.1 - sown]);
    const left = (grain - land * price - feed - Math.ceil(plant / ACRES_PER_BUSHEL)) / grain;
    if (left >= 0.25) scored.push(["store", 0.4 + left]);
    for (const row of scored) if (CARES[persona]?.includes(row[0])) row[1] += 0.3;
    return scored.sort((a, b) => b[1] - a[1]).slice(0, 2).map(([s]) => s);
  }

  /* ---------------- council events ---------------- */
  function event(kind, d = {}) {
    switch (kind) {
      case "game": case "end": case "halt":
        positions.clear(); lastShout.clear(); debating = false; speaker = ""; break;
      case "year_start":
        positions.clear(); debating = false; speaker = ""; break;
      case "turn": {
        if (!d.advisor) break;
        const hist = app()?.hist || [];
        const state = hist[(d.year || app()?.year || 1) - 1] || hist[hist.length - 1];
        const persona = (app()?.advisors || []).find(a => a.id === d.advisor)?.persona || "";
        const stances = stancesFor(d.proposal, state, persona);
        const prev = positions.get(d.advisor);
        if (stances.length) positions.set(d.advisor, { stances, n: prev?.n || 0 });
        speaker = d.advisor; debating = true;
        break;
      }
      case "year": debating = false; speaker = ""; break;
      // The crowd hushes while the human decides, and resumes on the next turn.
      case "scout_request": case "escalate": case "runoff":
        debating = false; speaker = "";
        document.querySelectorAll(".cg-shout").forEach(b => b.remove());
        break;
      default: break;
    }
    schedule();
  }

  /* ---------------- shouting ---------------- */
  function live() {
    const s = app();
    return !!(on && debating && phrases && positions.size && s?.running && !s.paused && !document.hidden);
  }
  function schedule() {
    if (live()) { if (!timer) timer = setTimeout(tick, 600); }
    else { clearTimeout(timer); timer = 0; }
  }
  function tick() {
    timer = 0;
    if (!live()) return;
    wave++;
    // Whoever has waited longest shouts next; the advisor holding the floor stays quiet.
    const ids = [...positions.keys()].filter(id => id !== speaker)
      .sort((a, b) => (lastShout.get(a) || 0) - (lastShout.get(b) || 0));
    const count = Math.min(ids.length, positions.size >= 7 ? 3 : 2);
    ids.slice(0, count).forEach((id, i) => {
      lastShout.set(id, wave);
      setTimeout(() => { if (live() && id !== speaker) shoutOne(id); }, i * STAGGER_MS + Math.random() * 160);
    });
    timer = setTimeout(tick, WAVE_MS);
  }
  function shoutOne(id) {
    const pos = positions.get(id);
    if (!pos) return;
    const stance = pos.stances[pos.n % pos.stances.length];
    const options = phrases[stance] || [];
    if (!options.length) return;
    const index = Math.floor(pos.n / pos.stances.length) % options.length;
    pos.n++;
    window.CouncilGraph?.shout(id, options[index], SHOW_MS);
    play(id, stance, index);
  }

  function voiceKey(id) {
    if (!voices.length) return "";
    const a = (app()?.advisors || []).find(x => x.id === id) || { id };
    for (const k of [a.voice_key, a.tts_voice, a.id, a.persona]) if (k && voices.includes(k)) return k;
    // A custom agent without a preset voice gets a stable pick from the advisor voices.
    let h = 0;
    for (const ch of String(id)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return voices[h % voices.length];
  }
  function play(id, stance, index) {
    // Shared app.js globals: the mute button and the narrator always win over the crowd.
    if ((typeof muted !== "undefined" && muted) || (typeof narrating !== "undefined" && narrating)) return;
    if (playing.size >= MAX_AUDIO) return;
    const narrator = document.getElementById("narrator");
    if (narrator && !narrator.paused) return; // the narrator always wins
    const key = voiceKey(id);
    if (!key) return;
    const url = `/api/clamor/${encodeURIComponent(key)}/${encodeURIComponent(stance)}/${index}`;
    if (failed.has(url)) return;
    const audio = new Audio(url);
    audio.volume = VOLUME;
    audio.playbackRate = 1.06;
    const done = () => playing.delete(audio);
    audio.addEventListener("ended", done);
    audio.addEventListener("error", () => { done(); failed.add(url); });
    playing.add(audio);
    audio.play().catch(done);
  }

  /* ---------------- toggle ---------------- */
  function set(value) {
    on = !!value;
    try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* storage blocked */ }
    const b = document.getElementById("clamorToggle");
    if (b) b.setAttribute("aria-pressed", String(on));
    if (!on) for (const a of playing) a.pause(), playing.delete(a);
    schedule();
  }
  const btn = document.getElementById("clamorToggle");
  if (btn) {
    btn.setAttribute("aria-pressed", String(on));
    btn.addEventListener("click", () => set(!on));
  }
  // Pause and tab switches stop the loop; resume picks it up again.
  document.addEventListener("visibilitychange", schedule);
  setInterval(schedule, 1000);

  window.Clamor = { event, set, get on() { return on; }, stancesFor };
})();
