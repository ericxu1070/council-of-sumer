/* Council of Sumer - chamber UI.
 * Events arrive over SSE (/api/events) and go through one client-side queue so the
 * council graph shows one speaker at a time, at least MIN_GAP ms apart; the transcript
 * keeps the full record. A replayed backlog (page reload) renders instantly. */
"use strict";

const MIN_GAP = 800;         // ms between visible messages (matches the server pace)
const READ_MIN = 2000, READ_MAX = 4200, READ_PER_CHAR = 14; // ms a speech holds the graph
const CATCH_UP = 6;          // queued turns beyond this shorten the hold to READ_MIN
const MIN_TYPING = 500;      // ms the "is typing" indicator shows before a speech
const HOLD_AFTER_YEAR = 6000; // ms to let the year's result sit before the next year starts
const FLASH_YEAR = 1900;     // ms the big "Year N" interstitial holds the stage
const AUTO_CONTINUE = 12;    // s before an untouched scout prompt lets the vote stand
const REPLAY_QUIET = 150;    // ms of silence that ends a backlog replay burst
const REPLAY_CAP = 2000;     // replay mode never lasts longer than this after connecting
const START_YEAR_BCE = 4005;

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = t => String(t ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = n => (n == null || Number.isNaN(+n)) ? "-" : Math.round(+n).toLocaleString("en-US");
const cap = s => s ? s[0].toUpperCase() + s.slice(1) : "";
const now = () => performance.now();
const reduced = matchMedia("(prefers-reduced-motion: reduce)");

/* ---------------- state ---------------- */
const S = {
  meta: null,
  runId: "",
  paused: false,
  scoutRequest: null,
  compareVote: null,
  advisors: [],
  years: 5,
  year: 0,            // year being decided
  completed: 0,       // years finished
  hist: [],           // hist[x] = state after x completed years (x = 0 is the start)
  roundBase: {},      // year -> 1 if rounds are 0-based, else 0
  plans: {},          // plan id -> author id
  planRows: [],        // current proposals and their approval votes
  lastPlain: {},      // advisor -> plan text last shown in the transcript this year
  runoff: null,       // {a, b, votes: {advisor: planId}, decided}
  stance: {},         // advisor -> "agree" | "disagree" | "vote-a" | "vote-b"
  running: false,
  waitingHuman: false,
  expanded: null,     // stat key with an open chart
  hood: false,
};

const FALLBACK_COLORS = ["#5f8f3a", "#7a4fa3", "#a33a32", "#b5862a", "#b2622f", "#2f8a7c", "#2f5ea8", "#6b5a45"];
const FALLBACK_EMOJI = { provider: "🍞", farmer: "🌾", merchant: "⚖️", priest: "🏺", villager: "🧑" };
// Setup belongs to the next council; the live council keeps its own roster.
const setup = { templates: [], voices: [], seats: [], serial: 0, starting: false, stopping: false, ready: false, max: 21, liveRunId: "" };
const safeColor = (color, i) => /^#[\da-f]{6}$/i.test(String(color || "")) ? color : FALLBACK_COLORS[i % FALLBACK_COLORS.length];

// Extra copies of a preset (custom-N ids) keep its color and emoji: map them back to the persona face.
function personaOf(a, id) {
  if (FALLBACK_EMOJI[id]) return id;
  if (a.persona) return a.persona;
  return setup.templates.find(t => FALLBACK_EMOJI[t.id] && t.color === a.color && t.emoji === a.emoji)?.id || "";
}
function normAdvisor(a, i) {
  const id = a.id || `advisor${i}`;
  const title = a.title || cap(id);
  return {
    ...a, id, title, persona: personaOf(a, id),
    short: a.short || (FALLBACK_EMOJI[id] ? cap(id) : title.split(" ").slice(-1)[0]),
    emoji: a.emoji || FALLBACK_EMOJI[id] || "👤",
    color: safeColor(a.color, i),
  };
}
function setAdvisors(list) {
  if (!Array.isArray(list) || !list.length) return;
  S.advisors = list.map(normAdvisor);
  G.setAdvisors(S.advisors);
  renderStances();
}
const adv = id => S.advisors.find(a => a.id === id) || normAdvisor({ id: id || "someone" }, 5);
const N = () => S.advisors.length || 5;
// Small portraits are static <img> data URIs: no id clashes, no per-face animation cost.
const FACE_URL = new Map();
function faceUrl(a) {
  const key = `${a.id}|${a.color}|${a.persona}|${window.Faces?.genderOf(a) || ""}`;
  if (!FACE_URL.has(key)) {
    let src = "";
    try { src = window.Faces ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(Faces.svg(a, { title: "" }))}` : ""; } catch (e) { console.warn("face failed", e); }
    FACE_URL.set(key, src);
  }
  return FACE_URL.get(key);
}
const avatar = (id, cls = "") => {
  const a = adv(id), src = faceUrl(a);
  return `<span class="av ${cls}" style="--c:${a.color}" title="${esc(a.title)}">${src ? `<img src="${src}" alt="" draggable="false">` : esc(a.emoji)}</span>`;
};
const nameList = ids => { const n = ids.map(i => adv(i).short); return n.length <= 1 ? n.join("") : `${n.slice(0, -1).join(", ")} and ${n[n.length - 1]}`; };

function planAuthor(id, fallback) {
  if (!id) return fallback || null;
  if (S.plans[id]) return S.plans[id];
  // Custom IDs contain hyphens; match the whole author prefix, longest first.
  const author = S.advisors.filter(a => String(id).startsWith(`${a.id}-`)).sort((a, b) => b.id.length - a.id.length)[0];
  return author?.id || fallback || null;
}
function planName(id, author) {
  if (id === "safe") return "The safe plan";
  const a = planAuthor(id, author);
  return a ? `${adv(a).short}'s plan` : "A plan";
}
function planText(p) {
  if (!p) return "";
  const parts = [];
  if (p.land_delta > 0) parts.push(`buy ${fmt(p.land_delta)} acres`);
  else if (p.land_delta < 0) parts.push(`sell ${fmt(-p.land_delta)} acres`);
  else parts.push("keep our land");
  if (p.feed != null) parts.push(`feed ${fmt(p.feed)} bushels`);
  if (p.plant != null) parts.push(`plant ${fmt(p.plant)} acres`);
  return cap(parts.join(", "));
}
// The river keeps the live proposal table visible while councilors speak.
function renderRiverPlans(rows = S.planRows) {
  S.planRows = rows.map(row => ({ ...S.planRows.find(p => p.id === row.id), ...row }));
  const board = $("#riverPlans");
  board.classList.toggle("hidden", !S.planRows.length);
  board.innerHTML = S.planRows.map(row => {
    const author = planAuthor(row.id, row.author);
    const supporters = [...new Set(row.accept || [])].filter(id => S.advisors.some(a => a.id === id));
    const request = row.plain || planText(row.plan);
    return `<article class="river-plan" data-plan-id="${esc(row.id)}" style="--c:${adv(author).color}">
      <div class="river-plan-head">${avatar(author, "mini")}<b>${esc(planName(row.id, author))}</b></div>
      <div class="river-plan-request">${esc(request || "Plan details pending")}</div>
      <div class="river-plan-support"><b>${supporters.length} of ${N()} for</b>${supporters.length ? supporters.map(id => `<span class="river-supporter" role="img" aria-label="${esc(adv(id).short)}">${avatar(id, "mini")}</span>`).join("") : '<span class="river-no-votes">No votes yet</span>'}</div>
    </article>`;
  }).join("");
}
function updateRiverTurn(d) {
  if (d.runoff_vote && S.runoff) return; // Runoff votes update after they are recorded.
  if (Array.isArray(d.support)) {
    renderRiverPlans(d.support);
  } else if (d.plan_id) {
    const rows = S.planRows.map(row => ({ ...row, accept: (row.accept || []).filter(id => id !== d.advisor) }));
    let row = rows.find(p => p.id === d.plan_id);
    if (!row) { row = { id: d.plan_id, author: planAuthor(d.plan_id, d.advisor), accept: [] }; rows.push(row); }
    if (d.plain) row.plain = d.plain;
    if (d.proposal) row.plan = d.proposal;
    for (const plan of rows) if ((d.accept || []).includes(plan.id)) plan.accept = [...new Set([...plan.accept, d.advisor])];
    renderRiverPlans(rows);
  }
}
const stripSafe = t => cap(String(t || "").replace(/^play it safe:\s*/i, ""));
// The narrator opens with "Year N." for listeners; on screen the card already says which year.
const stripYear = t => String(t || "").replace(/^Year \d+\.\s*/, "");
function dispRound(year, round) {
  if (!(year in S.roundBase)) S.roundBase[year] = round === 0 ? 1 : 0;
  return (round ?? 0) + S.roundBase[year];
}
const starvedOf = s => s?.starved ?? s?.last_report?.starved ?? 0;

/* ---------------- api ---------------- */
async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) } : {});
  if (!r.ok) {
    let msg = await r.text();
    try { msg = JSON.parse(msg).detail || msg; } catch { /* plain text */ }
    throw new Error(msg || `${r.status}`);
  }
  return r.json();
}
function toast(msg, info = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("info", info);
  t.classList.remove("hidden");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => t.classList.add("hidden"), info ? 3500 : 6000);
}

/* ---------------- council graph ---------------- */
// Thin wrapper over CouncilGraph. In replay (instant) mode only the last speak/idle of a
// burst reaches the graph, so a backlog never queues hundreds of bubbles and layouts.
const G = (() => {
  const CG = () => window.CouncilGraph;
  let pending = null, flushT = 0;
  const flush = () => { flushT = 0; const p = pending; pending = null; if (p) p(); };
  const defer = fn => { pending = fn; if (!flushT) flushT = setTimeout(flush, 0); };
  const run = fn => { pending = null; clearTimeout(flushT); flushT = 0; fn(); };
  return {
    mount: el => CG()?.mount(el),
    setAdvisors: list => CG()?.setAdvisors(list),
    speak: (args, instant) => (instant ? defer : run)(() => CG()?.speak({ ...args, instant })),
    idle: instant => (instant ? defer : run)(() => CG()?.idle()),
    typing: id => CG()?.typing(id),
    setBadge: (id, b) => CG()?.setBadge(id, b),
    setHub: h => CG()?.setHub(h),
    pulse: instant => { if (!instant) CG()?.pulseHub(); },
    relations: (edges, instant) => { if (!instant) CG()?.flashRelations(edges); },
  };
})();
// Keep transport details available under the hood; the main view is the city council.
const hubLabel = () => "";
function setHubSub() { G.setHub({ label: "", sub: "" }); }
const STANCE = {
  agree: ["agree", "✓", "Agrees with the leading plan"],
  disagree: ["disagree", "✕", "Does not back the leading plan"],
  "vote-a": ["vote-a", "A", "Votes for plan A"],
  "vote-b": ["vote-b", "B", "Votes for plan B"],
};
let shownStance = {};  // what the graph badges show now, so unchanged badges don't re-pop
function renderStances() {
  const next = {};
  for (const a of S.advisors) {
    const k = next[a.id] = S.stance[a.id] || "";
    if (shownStance[a.id] === k) continue;
    const st = STANCE[k];
    G.setBadge(a.id, st ? { cls: st[0], text: st[1], title: st[2] } : null);
  }
  shownStance = next;
}

/* ---------------- header + stage ---------------- */
function renderYear() {
  const steps = [];
  for (let i = 1; i <= S.years; i++) {
    steps.push(`<span class="${i <= S.completed ? "done" : i === S.year ? "now" : ""}"></span>`);
  }
  $("#yearSteps").innerHTML = steps.join("");
  // Each game year advances from the initial city date toward the end of its reign.
  $("#yearLabel").textContent = !S.year
    ? `${START_YEAR_BCE} BCE - ${START_YEAR_BCE - S.years} BCE`
    : `${START_YEAR_BCE - Math.min(S.year, S.years)} BCE`;
  $("#roundLabel").classList.toggle("hidden", !S.round || S.completed >= S.years);
  $("#roundLabel").textContent = S.round ? `Round ${S.round.number}${S.round.limit && S.round.number <= S.round.limit ? ` of ${S.round.limit}` : ""}` : "";
}
function setPhase(title) {
  const phase = /ready|gathers|conven|no king/i.test(title) ? "Ready"
    : /reign complete/i.test(title) ? "End"
    : /runoff|re-vote|voting|your move/i.test(title) ? "Voting"
    : /debate|discuss|year .* begins/i.test(title) ? "Discussion" : title;
  $("#phase").textContent = phase;
}
function voteDots(accept = []) {
  return `<span class="dots">${S.advisors.map(a => {
    const yes = accept.includes(a.id);
    return `<span class="dot${yes ? " yes" : ""}" style="--c:${a.color}" title="${esc(a.short)}: ${yes ? "agrees" : "not yet"}"></span>`;
  }).join("")}</span>`;
}

/* ---------------- spotlight: center-stage cards ---------------- */
let spotT = 0, spotKind = "";
function spotlight(kind, html, { cls = "", ms = 0, flash = false } = {}) {
  const el = $("#spot");
  clearTimeout(spotT);
  spotKind = kind;
  el.className = `spot${flash ? " flash" : ""}`;
  el.innerHTML = flash ? html : `<div class="spot-card ${cls}">${html}</div>`;
  void el.offsetWidth;
  el.classList.add("in");
  if (ms) spotT = setTimeout(() => hideSpot(kind), ms);
  return el;
}
function hideSpot(kind) {
  if (kind && kind !== spotKind) return;
  stopCountdown();
  const el = $("#spot");
  clearTimeout(spotT);
  spotKind = "";
  el.classList.remove("in");
  spotT = setTimeout(() => { if (!spotKind) { el.classList.add("hidden"); el.innerHTML = ""; } }, 350);
}
function deltaOf(cur, prev, dir) {
  if (cur == null || prev == null) return ["", ""];
  const diff = cur - prev;
  if (!diff) return ["no change", "flat"];
  return [`${diff > 0 ? "▲" : "▼"} ${fmt(Math.abs(diff))}`, dir === 0 ? "flat" : (diff > 0) === (dir > 0) ? "good" : "bad"];
}
function spotNums(list) {
  return `<div class="spot-nums">${list.map(([k, v, d = ["", ""]]) => `<div class="spot-num"><div class="k">${esc(k)}</div><div class="v">${fmt(v)}</div><div class="d ${d[1]}">${esc(d[0])}</div></div>`).join("")}</div>`;
}

/* ---------------- typing indicator ---------------- */
function typing(html) { $("#typing").innerHTML = html || ""; }
const bounce = `<span class="bounce" aria-hidden="true"><i></i><i></i><i></i></span>`;
function typingFor(id) { typing(`${avatar(id, "mini")}<span><b>${esc(adv(id).short)}</b> is typing</span>${bounce}`); }
function idleTyping() {
  if (S.paused) typing(`<span>The council is paused</span>`);
  else if (S.scoutRequest || S.waitingHuman) typing("");
  else if (narrating && speakingWho === "herald") typing(`<span>🔊 The herald is speaking</span>`);
  else if (narrating) typing(speakingWho && speakingWho !== "narrator"
    ? `${avatar(speakingWho, "mini")}<span>🔊 <b>${esc(adv(speakingWho).short)}</b> is speaking</span>`
    : `<span>🔊 The narrator is speaking</span>`);
  else if (S.running) typing(`<span>The council is thinking</span>${bounce}`);
  else typing("");
}

/* ---------------- feed ---------------- */
const feed = () => $("#feed");
function nearBottom() { const f = feed(); return f.scrollHeight - f.scrollTop - f.clientHeight < 140; }
// Follow new messages unless the viewer scrolled up by hand (programmatic smooth scrolls don't count).
let stick = true, userScroll = false;
function append(html, cls, { instant = false, latest = false } = {}) {
  const f = feed();
  const el = document.createElement("div");
  el.className = cls + (instant ? "" : " enter");
  el.innerHTML = html;
  if (latest) { $$(".msg.latest", f).forEach(m => m.classList.remove("latest")); el.classList.add("latest"); }
  f.appendChild(el);
  if (txCollapsed && !instant && !cls.includes("hood-only")) { unread++; renderUnread(); }
  if (stick || instant) scrollBottom(instant);
  else $("#jumpBtn").classList.remove("hidden");
  return el;
}
// The transcript can collapse to give the city panel room; it then counts unread items.
let txCollapsed = false, unread = 0;
function renderUnread() { $("#txCount").textContent = txCollapsed && unread ? `${unread} new` : ""; }
function setTranscript(open) {
  txCollapsed = !open;
  $("#side").classList.toggle("tx-collapsed", !open);
  $("#txToggle").setAttribute("aria-expanded", String(open));
  if (open) { unread = 0; scrollBottom(true); }
  renderUnread();
  try { localStorage.setItem("sumer-tx", open ? "1" : "0"); } catch { /* storage blocked */ }
}
// Back-to-back appends jump instead of chaining smooth scrolls (a later one can cut an
// earlier one short); a follow-up frame catches late layout.
let lastScroll = 0, scrollRaf = 0;
function scrollBottom(instant) {
  const f = feed(), t = now();
  const smooth = !instant && !reduced.matches && !document.hidden && t - lastScroll > 350;
  lastScroll = t;
  f.style.scrollBehavior = smooth ? "smooth" : "auto";
  f.scrollTop = f.scrollHeight;
  stick = true;
  $("#jumpBtn").classList.add("hidden");
  cancelAnimationFrame(scrollRaf);
  if (!smooth) scrollRaf = requestAnimationFrame(() => { if (stick) f.scrollTop = f.scrollHeight; });
}
// Before a run: the transcript introduces each advisor and its goal in one line.
const shortMandate = a => {
  const text = String(a.rulebook || a.mandate || "No instructions supplied.").replace(/^Goals and preferences:\s*/, "").replace(/\s+/g, " ").trim();
  return text.length <= 90 ? text : text.slice(0, 90).replace(/[\s,.;:]+\S*$/, "") + "…";
};
function welcome() {
  feed().innerHTML = `<div class="welcome">
    <div class="welcome-kicker">Meet the council</div>
    <div class="meet">${S.advisors.map(a => `<div class="meet-row">${avatar(a.id)}<div><b style="--c:${a.color}">${esc(a.title || a.short)}</b><span>${esc(shortMandate(a) || "Custom agent")}</span></div></div>`).join("")}</div>
    <p class="welcome-foot">Every advisor is its own AI agent. They debate over SLIM and vote. When they deadlock, you decide.</p></div>`;
  syncSetupLock();
}

function traceText(obj, depth = 0) {
  if (obj == null) return "";
  if (typeof obj !== "object") return String(obj);
  const pad = "  ".repeat(depth);
  return Object.entries(obj).map(([k, v]) => {
    if (v && typeof v === "object" && !Array.isArray(v)) return `${pad}${k}:\n${traceText(v, depth + 1)}`;
    if (Array.isArray(v)) return `${pad}${k}: ${v.map(x => typeof x === "object" ? JSON.stringify(x) : x).join(", ") || "-"}`;
    return `${pad}${k}: ${v}`;
  }).join("\n");
}

function renderPrivateBeliefs(trace) {
  const memory = trace?.private_memory_after;
  if (!memory) return "";
  const before = trace.private_memory_before?.revision ?? 0;
  const status = memory.revision > before ? "updated" : "unchanged";
  const entries = (memory.beliefs || []).map(b => `<li>
    <b>${esc(b.claim)}</b><span class="belief-confidence">Confidence: ${esc(b.confidence)}</span>
    <p>Evidence: ${esc(b.evidence)}</p><p>Uncertainty: ${esc(b.uncertainty)}</p></li>`).join("");
  return `<details class="private-beliefs hood-only"><summary>Private beliefs · revision ${esc(memory.revision)} · ${status}</summary>
    <p class="rulebook-note">Only this advisor receives these beliefs. Visible here for your inspection.</p>
    ${entries ? `<ul>${entries}</ul>` : `<p>No learned claims saved yet.</p>`}
    <details><summary>Starting rulebook · preserved</summary><pre>${esc(trace.rulebook || "No private starting information.")}</pre></details>
    </details>`;
}

function turnRelations(d) {
  const accepted = new Set(d.accept || []);
  if (d.runoff_vote && S.runoff) {
    return [S.runoff.a, S.runoff.b].map(row => ({
      from: d.advisor, to: planAuthor(row.id, row.author), agree: row.id === d.runoff_vote,
    }));
  }
  const acceptedAuthors = new Set([...accepted].map(id => planAuthor(id)).filter(Boolean));
  const knownAuthors = new Set(Object.values(S.plans));
  // Only show red for an addressed advisor with a known plan. Addressing someone
  // alone is not evidence of disagreement; absence from a cast approval vote is.
  const targets = new Set([...acceptedAuthors, ...(d.addressed || []).filter(id => knownAuthors.has(id))]);
  return [...targets].map(to => ({ from: d.advisor, to, agree: acceptedAuthors.has(to) }));
}

function renderTurn(d, instant) {
  const a = adv(d.advisor);
  if (!instant && spotKind === "year") hideSpot("year");
  const addressed = (d.addressed || []).filter(x => x !== d.advisor);
  // Backing someone else’s plan must retain its original author.
  for (const row of d.support || []) if (row.id && row.author) S.plans[row.id] = row.author;
  if (d.plan_id && !S.plans[d.plan_id]) S.plans[d.plan_id] = planAuthor(d.plan_id, d.advisor);
  updateRiverTurn(d);
  const meta = d.phase === "runoff" ? "Runoff" : d.round != null ? `Round ${dispRound(d.year, d.round)}` : "";
  G.speak({ speaker: d.advisor, to: addressed, text: d.speech || "" }, instant);
  G.relations(turnRelations(d), instant);
  const foot = [];
  if (d.phase === "runoff" || d.runoff_vote) {
    const side = S.runoff && d.runoff_vote === S.runoff.b.id ? "b" : "a";
    const pid = d.runoff_vote;
    if (pid) foot.push(`<span class="vote-chip runoff-pick" style="--k:var(--vote-${side})">Votes for ${side.toUpperCase()}: ${avatar(planAuthor(pid), "mini")}<b>${esc(planName(pid))}</b></span>`);
  } else {
    // A plan chip only when this advisor's plan is new or changed this year; repeats add noise.
    const plain = d.plain || planText(d.proposal);
    const fresh = plain && S.lastPlain[d.advisor] !== plain;
    if (plain) S.lastPlain[d.advisor] = plain;
    if (fresh) foot.push(`<span class="plan-chip"><span class="emoji">📜</span><span>${esc(plain)}</span></span>`);
    const acc = [...new Set((d.accept || []).map(id => planAuthor(id)).filter(Boolean))];
    if (acc.length) foot.push(`<span class="vote-chip hood-only" title="Plans ${esc(a.short)} would accept">👍 ${acc.map(x => x === d.advisor ? "own" : `${esc(adv(x).short)}'s`).join(", ")} plan${acc.length > 1 ? "s" : ""}</span>`);
  }
  const trace = d.trace && Object.keys(d.trace).length ? `<pre class="trace hood-only">${esc(traceText(d.trace))}</pre>` : "";
  append(`${avatar(d.advisor)}
    <div class="msg-body">
      <div class="msg-head"><span class="msg-name" style="--c:${a.color}">${esc(a.short)}</span>
        ${addressed.length ? `<span class="msg-to">to ${esc(nameList(addressed))}</span>` : ""}<span class="msg-meta hood-only">${esc(meta)}</span></div>
      <div class="speech">${esc(d.speech || "...")}</div>
      ${foot.length ? `<div class="msg-foot">${foot.join("")}</div>` : ""}
      ${d.thought ? `<div class="thought hood-only">💭 ${esc(d.thought)}</div>` : ""}
      ${renderPrivateBeliefs(d.trace)}
      ${trace}
    </div>`, "msg", { instant, latest: true });
  if (d.runoff_vote) addRunoffVote(d.advisor, d.runoff_vote, instant);
}

function renderPoll(d, instant) {
  const rows = (d.rows || []).slice();
  rows.forEach(r => { if (r.id && r.author) S.plans[r.id] = r.author; });
  renderRiverPlans(rows);
  rows.sort((x, y) => (y.accept?.length || 0) - (x.accept?.length || 0));
  const lead = rows[0];
  G.pulse(instant);
  // node stances on the leading plan
  if (lead) {
    S.stance = {};
    for (const a of S.advisors) S.stance[a.id] = lead.accept?.includes(a.id) ? "agree" : "disagree";
    renderStances();
    G.relations(S.advisors.map(a => ({ from: a.id, to: planAuthor(lead.id, lead.author), agree: lead.accept?.includes(a.id) })), instant);
    const n = lead.accept?.length || 0;
    S.leadHtml = `${voteDots(lead.accept)}<span><b>${n} of ${N()}</b> agree with ${esc(planName(lead.id, lead.author))}</span>`;
    setPhase($("#phase").textContent, S.leadHtml);
  }
  const top = rows.slice(0, 3);
  if (!top.length) return;
  const body = top.map((r, i) => {
    const au = planAuthor(r.id, r.author);
    return `<div class="poll-row${i === 0 ? " lead" : ""}">
      <div class="poll-plan">${avatar(au, "mini")}<span class="who">${esc(planName(r.id, au))}</span><span class="what">${esc(r.plain || "")}</span></div>
      ${voteDots(r.accept)}
      <span class="poll-count">${r.accept?.length || 0} of ${N()} agree</span></div>`;
  }).join("");
  const label = d.phase === "runoff" ? "Runoff count" : `Where the votes stand after round ${dispRound(d.year, d.round)}`;
  append(`<div class="poll-title">${esc(label)}</div>${body}`, "poll hood-only", { instant });
}

const OUTCOME = {
  consensus: ["🤝", "Decided together"],
  majority: ["🗳️", "Majority decision"],
  runoff: ["⚖️", "Decided by runoff vote"],
  escalated: ["👑", "You decided"],
};
const SOLE_RULER = ["👑", "Decreed"];
const outcomeOf = k => (k === "consensus" && N() === 1 ? SOLE_RULER : OUTCOME[k]);
function outcomeBadge(d) {
  const [ic, label] = outcomeOf(d.outcome) || ["📜", cap(d.outcome || "Decided")];
  const dissent = (d.dissent || []).filter(Boolean);
  const extra = d.outcome === "majority" && dissent.length ? ` (${nameList(dissent)} disagreed)` : "";
  return `<span class="outcome o-${esc(d.outcome)}"><span class="emoji">${ic}</span>${esc(label + extra)}</span>`;
}

function renderYear_(d, instant) {
  const author = d.adopted === "safe" ? null : planAuthor(d.adopted);
  const plain = stripSafe(d.plain || planText(d.plan));
  const pname = d.adopted === "safe" ? "The safe plan" : author ? `${adv(author).short}'s plan` : "The plan";
  const sp = d.speech;
  const summary = sp?.headline || stripYear(d.summary) || `The council adopted ${pname.toLowerCase().replace(/^the /, "the ")}.`;
  const sat = d.satisfaction && Object.keys(d.satisfaction).length
    ? `<div class="hood-only trace">How satisfied each advisor is (0 to 1):\n${Object.entries(d.satisfaction).map(([k, v]) => `  ${esc(adv(k).short)}: ${typeof v === "number" ? v.toFixed(2) : esc(v)}`).join("\n")}\nRounds this year: ${esc(d.rounds ?? "-")}</div>` : "";
  append(`<div class="card-kicker"><span>Year ${esc(d.year)} decision</span>${outcomeBadge(d)}</div>
    <div class="card-summary">${esc(summary)}</div>
    ${sp?.text ? `<div class="card-quote">${sp.speaker !== "narrator" ? `${avatar(sp.speaker, "mini")}<span>“${esc(sp.text)}”</span>` : `<span>${esc(sp.text)}</span>`}</div>` : ""}
    ${plain || author ? `<div class="card-plan"><span class="plan-chip">${author ? avatar(author, "mini") : `<span class="emoji">🛟</span>`}<span><b>${esc(pname)}:</b> ${esc(plain)}</span></span></div>` : ""}
    ${sat}`, `card k-${esc(d.outcome)}`, { instant });
}

const VERDICT = {
  Jefferson: ["A golden reign", "🏆"],
  fair: ["A fair reign", "🌤️"],
  "Henry VIII": ["A good reign, could be better", "🌥️"],
  Nero: ["A disastrous reign", "🔥"],
};
const endTitle = r => r.impeached ? ["The people overthrew the council", "🔥"] : VERDICT[r.verdict] || [r.verdict ? `Verdict: ${r.verdict}` : "The reign is over", "🏛️"];
function renderEnd(d, instant) {
  const r = d.rating || {};
  const [title, ic] = endTitle(r);
  const oc = d.outcomes || {};
  const words = { consensus: N() === 1 ? "decreed" : "decided together", majority: "by majority", runoff: "by runoff", escalated: "by you" };
  const tally = Object.entries(oc).filter(([, v]) => v).map(([k, v]) => `<span class="outcome o-${esc(k)}">${esc(v)} ${esc(words[k] || k)}</span>`).join("");
  const nums = [["People", r.population], ["Land (acres)", r.acres], ["Grain", r.grain], ["Starved in total", r.starved_total]].filter(x => x[1] != null);
  append(`<div class="card-kicker">The reign is over</div>
    <div class="end-verdict"><span class="emoji">${ic}</span> ${esc(title)}</div>
    ${r.verdict_reason ? `<div class="end-why">${esc(cap(r.verdict_reason))}.</div>` : ""}
    <div class="nums">${nums.map(([k, v]) => `<div class="num"><div class="k">${esc(k)}</div><div class="v">${fmt(v)}</div></div>`).join("")}</div>
    ${tally ? `<div class="end-tally">${tally}</div>` : ""}
    ${r.score != null ? `<div class="hood-only end-tally">Score ${esc(r.score)} · avg starved ${esc(r.avg_starved_pct)}% · ${esc(r.acres_per_person)} acres per person · verdict ${esc(r.verdict)}</div>` : ""}`,
  "card end", { instant });
}

function showYearSpot(d) {
  const author = d.adopted === "safe" ? null : planAuthor(d.adopted);
  const pname = d.adopted === "safe" ? "The safe plan" : author ? `${adv(author).short}'s plan` : "The plan";
  const plain = stripSafe(d.plain || planText(d.plan));
  const [ic, label] = outcomeOf(d.outcome) || ["📜", cap(d.outcome || "Decided")];
  const cur = d.state || {}, prev = S.hist[d.year - 1] || {}, r = d.report || {};
  const starved = r.starved ?? starvedOf(cur);
  spotlight("year", `<div class="spot-kicker">Year ${esc(d.year)} of ${esc(S.years)}</div>
    <div class="spot-title"><span class="emoji">${ic}</span>${esc(label)}</div>
    ${d.speech ? `<p class="spot-sub">${esc(d.speech.speaker !== "narrator" ? `“${d.speech.text}”` : d.speech.text)}<br><b>${esc(d.speech.headline)}</b></p>`
      : d.summary ? `<p class="spot-sub">${esc(stripYear(d.summary))}</p>` : ""}
    <div class="spot-plan">${author ? avatar(author) : `<span class="av safe-av">🛟</span>`}<span><b>${esc(pname)}</b><span>${esc(plain)}</span></span></div>
    ${spotNums([
      ["People", cur.population, deltaOf(cur.population, prev.population, 1)],
      ["Grain", cur.grain, deltaOf(cur.grain, prev.grain, 1)],
      ["Land", cur.acres, deltaOf(cur.acres, prev.acres, 1)],
      ["Starved", starved, starved ? ["this year", "bad"] : ["nobody", "good"]],
    ])}`, { cls: `k-${esc(d.outcome)}` });
}

function showEndSpot(d) {
  const r = d.rating || {};
  const [title, ic] = endTitle(r);
  const oc = d.outcomes || {};
  const words = { consensus: N() === 1 ? "decreed" : "together", majority: "by majority", runoff: "by runoff", escalated: "by you" };
  const tally = Object.entries(oc).filter(([, v]) => v).map(([k, v]) => `<span class="outcome o-${esc(k)}"><span class="emoji">${outcomeOf(k)?.[0] || "📜"}</span>${esc(v)} ${esc(words[k] || k)}</span>`).join("");
  const first = S.hist[0] || {};
  spotlight("end", `<div class="spot-kicker">The reign is over</div>
    <div class="spot-title"><span class="emoji">${ic}</span>${esc(title)}</div>
    ${r.verdict_reason ? `<p class="spot-sub">${esc(cap(r.verdict_reason))}.</p>` : ""}
    ${spotNums([
      ["People", r.population, deltaOf(r.population, first.population, 1)],
      ["Land", r.acres, deltaOf(r.acres, first.acres, 1)],
      ["Grain", r.grain, deltaOf(r.grain, first.grain, 1)],
      ["Starved", r.starved_total, r.starved_total ? ["in total", "bad"] : ["nobody", "good"]],
    ].filter(x => x[1] != null))}
    ${tally ? `<div class="spot-tally">${tally}</div>` : ""}
    <div class="spot-actions"><button class="btn primary" type="button" data-act="again">Convene a new council</button><button class="btn" type="button" data-act="close">Close</button></div>`);
  if (["Jefferson", "fair"].includes(r.verdict) && !r.impeached) confetti();
  $('#spot [data-act="again"]').onclick = () => { hideSpot("end"); start(); };
  $('#spot [data-act="close"]').onclick = () => hideSpot("end");
}

// A short burst over the end card for a good reign; skipped for reduced motion.
function confetti() {
  if (reduced.matches) return;
  const box = $("#spot"), colors = S.advisors.map(a => a.color).concat(["#dcc080", "#dfb66b", "#a4bd7d"]);
  for (let i = 0; i < 70; i++) {
    const p = document.createElement("i");
    p.className = "confetti";
    p.style.cssText = `left:${Math.random() * 100}%;background:${colors[i % colors.length]};--dx:${(Math.random() - .5) * 240}px;--r:${Math.random() * 720 - 360}deg;animation-delay:${Math.random() * .6}s;animation-duration:${2.4 + Math.random() * 1.6}s`;
    box.appendChild(p);
    setTimeout(() => p.remove(), 4800);
  }
}

function renderNews(d, instant) {
  const mine = d.injected;
  if (mine) {
    const labels = { plague: "Plague", rats: "Rats", good_harvest: "Good harvest", bad_harvest: "Bad harvest", low_price: "Cheap land", high_price: "Costly land" };
    append(`<div><b>${esc(labels[d.kind] || cap(d.kind || "Omen"))}</b></div>`, "note you", { instant });
    if (!instant) window.CityEffects?.effect(d.kind);
  } else append(`<span class="emoji">📯</span><div><b>Scout report</b>${esc(d.text || d.kind)}</div>`, "note", { instant });
}

/* ---------------- runoff ---------------- */
function showRunoff(d, instant) {
  const a = d.a || {}, b = d.b || {};
  if (a.id && a.author) S.plans[a.id] = a.author;
  if (b.id && b.author) S.plans[b.id] = b.author;
  S.runoff = { a, b, votes: {}, decided: false };
  renderRiverPlans([a, b].map(row => ({ ...row, accept: [] })));
  S.stance = {};
  renderStances();
  const side = (p, k) => {
    const au = planAuthor(p.id, p.author);
    return `<div class="ro-side ${k}" id="ro-${k}">
      <div class="ro-head"><span class="ro-letter">${k.toUpperCase()}</span>${avatar(au)}<span class="ro-title">${esc(planName(p.id, au))}</span><span class="ro-win">🏆 Winner</span><span class="ro-count">0</span></div>
      <div class="ro-plain">${esc(p.plain || "")}</div>
      <div class="ro-votes"></div></div>`;
  };
  const el = $("#runoff");
  el.className = "runoff";
  el.innerHTML = `${side(a, "a")}<div class="ro-vs">vs</div>${side(b, "b")}`;
  setPhase("Runoff vote", `<span>Every advisor picks <b>A</b> or <b>B</b></span>`);
  append(`<span class="emoji">⚖️</span><div><b>Runoff</b>No plan won a majority. Each advisor now votes for ${esc(planName(a.id, a.author))} or ${esc(planName(b.id, b.author))}.</div>`, "note", { instant });
}
function addRunoffVote(advisor, pid, instant) {
  const R = S.runoff;
  if (!R || R.votes[advisor]) return;
  const k = pid === R.b.id ? "b" : pid === R.a.id ? "a" : null;
  if (!k) return;
  R.votes[advisor] = pid;
  renderRiverPlans([R.a, R.b].map(row => ({ ...row, accept: Object.keys(R.votes).filter(id => R.votes[id] === row.id) })));
  const side = $(`#ro-${k}`);
  if (side) {
    $(".ro-votes", side).insertAdjacentHTML("beforeend", avatar(advisor, `ro-vote${instant ? " still" : ""}`));
    $(".ro-count", side).textContent = Object.values(R.votes).filter(v => v === pid).length;
  }
  S.stance[advisor] = `vote-${k}`;
  renderStances();
}
function runoffResult(d, instant) {
  const R = S.runoff;
  if (!R) return;
  for (const [advisor, pid] of Object.entries(d.votes || {})) addRunoffVote(advisor, pid, true);
  for (const k of ["a", "b"]) {
    const id = R[k].id, n = d.count?.[id];
    if (n != null) $(`#ro-${k} .ro-count`).textContent = n;
    $(`#ro-${k}`).classList.toggle("win", d.winner === id);
  }
  R.decided = true;
  $("#runoff").classList.add("decided");
  const ca = d.count?.[R.a.id] ?? Object.values(R.votes).filter(v => v === R.a.id).length;
  const cb = d.count?.[R.b.id] ?? Object.values(R.votes).filter(v => v === R.b.id).length;
  const msg = d.winner ? `${planName(d.winner)} wins, ${Math.max(ca, cb)} to ${Math.min(ca, cb)}` : `Tied ${ca} to ${cb}. No winner.`;
  setPhase("Runoff vote", `<span><b>${esc(msg)}</b></span>`);
  append(`<span class="emoji">🏁</span><div><b>Runoff result</b>${esc(msg)}.</div>`, "note", { instant });
}
function hideRunoff() { $("#runoff").classList.add("hidden"); S.runoff = null; }

/* ---------------- escalation ---------------- */
function showEscalation(d) {
  S.waitingHuman = true;
  const opts = (d.options || []).slice();
  if (!opts.some(o => o.id === "safe")) opts.push({ id: "safe", plain: "Play it safe: feed everyone and plant as much as we can", accept: [] });
  $("#escOptions").innerHTML = opts.map((o, i) => {
    const safe = o.id === "safe";
    const au = safe ? null : planAuthor(o.id, o.author);
    const why = [];
    if (!safe) why.push(`${voteDots(o.accept)} <span>${(o.accept || []).length} of ${N()} agree</span>`);
    if (!safe && o.note) why.push(`<span>${esc(o.note)}</span>`);
    return `<button class="esc-opt${safe ? " safe" : ""}" type="button" data-choice="${esc(o.id)}">
      ${safe ? `<span class="av safe-av">🛟</span>` : avatar(au)}
      <span><span class="who">${esc(safe ? "Play it safe" : planName(o.id, au))}</span><br><span class="what">${esc(stripSafe(o.plain || planText(o.plan)))}</span></span>
      ${why.length ? `<span class="why">${why.join("")}</span>` : ""}<kbd>${i + 1}</kbd></button>`;
  }).join("");
  $$("#escOptions .esc-opt").forEach(btn => btn.onclick = () => choose(btn));
  $("#escalation").classList.remove("hidden");
  setPhase("Deadlock", `<span>The council is stuck. <b>You decide.</b></span>`);
  G.idle(); setHubSub("waiting for you");
  idleTyping();
}
async function choose(btn) {
  const all = $$("#escOptions .esc-opt");
  all.forEach(b => b.disabled = true);
  btn.classList.add("chosen");
  const label = $(".who", btn).textContent;
  // Show the ruling before the POST resolves: the server may stream the year result first.
  const note = append(`<span class="emoji">👑</span><div><b>You decided</b>${btn.dataset.choice === "safe" ? "You chose to play it safe." : `You chose ${esc(label)}.`}</div>`, "note you", {});
  try {
    await api("/api/escalation", { run_id: S.runId, choice: btn.dataset.choice });
  } catch (e) {
    note.remove();
    btn.classList.remove("chosen");
    all.forEach(b => b.disabled = false);
    toast(`Could not send your choice: ${e.message}`);
    return;
  }
  setTimeout(() => closeEscalation(), reduced.matches ? 0 : 600);
  if ($("#phase").textContent === "Deadlock") setPhase("Decision", `<span>You chose <b>${esc(label)}</b></span>`);
  S.waitingHuman = false;
  idleTyping();
}
function closeEscalation() { $("#escalation").classList.add("hidden"); S.waitingHuman = false; }

/* ---------------- city stats + charts ---------------- */
const STATS = [
  { key: "population", label: "People", color: "#9fb9bb", dir: 1, get: s => s.population },
  { key: "grain", label: "Grain", unit: "bushels", color: "#dfb66b", dir: 1, get: s => s.grain },
  { key: "acres", label: "Land", unit: "acres", color: "#a4bd7d", dir: 1, get: s => s.acres },
  { key: "land_price", label: "Land price", unit: "bu / acre", color: "#c99a78", dir: 0, get: s => s.land_price },
  { key: "starved", label: "Starved", unit: "last year", color: "#e4947d", dir: -1, get: starvedOf },
];
function buildStats() {
  $("#stats").innerHTML = STATS.map(st => `<div class="stat" id="stat-${st.key}" style="--sc:${st.color}">
    <button class="stat-btn" type="button" aria-controls="chart-${st.key}" aria-expanded="false">
      <span class="stat-top"><span class="stat-k">${esc(st.label)}</span><span class="delta"></span><span class="chev" aria-hidden="true">▾</span></span>
      <span class="stat-main"><span class="stat-v">-</span>${st.unit ? `<span class="stat-unit">${esc(st.unit)}</span>` : ""}</span>
    </button>
    <div class="chart hidden" id="chart-${st.key}"></div></div>`).join("");
  $$("#stats .stat-btn").forEach(b => b.onclick = () => toggleStat(b.parentElement.id.slice(5)));
}
function points(st) {
  const out = [];
  S.hist.forEach((s, x) => { if (s) { const v = st.get(s); if (v != null && !Number.isNaN(+v)) out.push([x, +v]); } });
  return out;
}
function renderStats() {
  const last = S.hist.length - 1;
  window.CityView?.render(S.hist[last] || null);
  $("#cityWhen").textContent = last <= 0 ? "at the start" : `after year ${last}`;
  $("#side").classList.toggle("no-city", !S.hist.length);
  for (const st of STATS) {
    const el = $(`#stat-${st.key}`);
    const pts = points(st);
    const cur = pts[pts.length - 1], prev = pts[pts.length - 2];
    $(".stat-v", el).textContent = cur ? fmt(cur[1]) : "-";
    const d = $(".delta", el);
    if (!cur || !prev || cur[0] === prev[0]) { d.textContent = ""; d.className = "delta"; }
    else {
      const diff = cur[1] - prev[1];
      const good = st.dir === 0 || diff === 0 ? "flat" : (diff > 0) === (st.dir > 0) ? "good" : "bad";
      d.textContent = diff === 0 ? "" : `${diff > 0 ? "▲" : "▼"} ${fmt(Math.abs(diff))}`;
      d.className = `delta ${good}`;
      d.title = "Change since last year";
    }
    if (S.expanded === st.key) drawChart(st);
  }
}
function toggleStat(key) {
  S.expanded = S.expanded === key ? null : key;
  for (const st of STATS) {
    const el = $(`#stat-${st.key}`);
    const open = S.expanded === st.key;
    el.classList.toggle("open", open);
    $(".stat-btn", el).setAttribute("aria-expanded", String(open));
    $(".chart", el).classList.toggle("hidden", !open);
    if (open) {
      drawChart(st);
      const c = $("#stats"), bottom = el.offsetTop - c.offsetTop + el.offsetHeight;
      if (bottom > c.scrollTop + c.clientHeight) c.scrollTop = bottom - c.clientHeight;
    }
  }
}
function niceTicks(lo, hi, count = 3) {
  if (lo === hi) { const pad = Math.max(1, Math.abs(lo) * 0.1); lo -= pad; hi += pad; }
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || raw;
  const start = Math.floor(lo / step) * step, end = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = start; v <= end + step / 2; v += step) ticks.push(+v.toFixed(6));
  return ticks;
}
function drawChart(st) {
  const box = $(`#chart-${st.key}`);
  const pts = points(st);
  const W = Math.max(200, box.clientWidth || 260), H = Math.round(Math.min(200, Math.max(140, W * 0.62)));
  const pl = 44, pr = 14, pt = 14, pb = 26;
  const xMax = Math.max(S.years, pts.length ? pts[pts.length - 1][0] : 0);
  const vals = pts.map(p => p[1]);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (st.key === "starved" || lo >= 0 && lo < (hi - lo)) lo = Math.min(0, lo);
  const ticks = niceTicks(lo, hi);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const X = x => pl + (x / xMax) * (W - pl - pr);
  const Y = v => pt + (1 - (v - y0) / (y1 - y0 || 1)) * (H - pt - pb);
  const xl = x => x === 0 ? "Start" : `Yr ${x}`;
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(st.label)} by year">`;
  for (const t of ticks) svg += `<line class="grid" x1="${pl}" x2="${W - pr}" y1="${Y(t)}" y2="${Y(t)}"/><text class="axis-t" x="${pl - 8}" y="${Y(t) + 4}" text-anchor="end">${fmt(t)}</text>`;
  for (let x = 0; x <= xMax; x++) svg += `<text class="axis-t" x="${X(x)}" y="${H - 6}" text-anchor="middle">${xl(x)}</text>`;
  if (pts.length > 1) {
    const d = pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
    svg += `<path class="area" d="${d}L${X(pts[pts.length - 1][0]).toFixed(1)},${Y(y0)}L${X(pts[0][0]).toFixed(1)},${Y(y0)}Z"/><path class="line" d="${d}"/>`;
  }
  svg += `<line class="cross hidden-cross" x1="0" x2="0" y1="${pt}" y2="${H - pb}" style="display:none"/>`;
  pts.forEach((p, i) => {
    const last = i === pts.length - 1;
    if (last) svg += `<circle class="halo" cx="${X(p[0])}" cy="${Y(p[1])}" r="9"/>`;
    svg += `<circle class="pt${last ? " now" : ""}" cx="${X(p[0])}" cy="${Y(p[1])}" r="${last ? 5 : 4}"/>`;
  });
  const half = (W - pl - pr) / xMax / 2;
  pts.forEach(p => { svg += `<rect class="hit" data-x="${p[0]}" data-v="${p[1]}" x="${X(p[0]) - half}" y="0" width="${half * 2}" height="${H}"/>`; });
  svg += `</svg>`;
  box.innerHTML = svg + `<div class="chart-tip hidden"></div>` + (pts.length < 2 ? `<div class="chart-empty">A new point appears after each year.</div>` : "");
  const tip = $(".chart-tip", box), cross = $(".cross", box);
  const show = r => {
    const x = +r.dataset.x, v = +r.dataset.v;
    const sx = X(x) * box.clientWidth / W, sy = Y(v) * box.clientWidth / W;
    tip.innerHTML = `${fmt(v)}${st.unit ? ` ${esc(st.unit)}` : ""}<small>${x === 0 ? "At the start" : `After year ${x}`}</small>`;
    tip.style.left = `${Math.min(Math.max(sx, 50), box.clientWidth - 50)}px`;
    tip.style.top = `${sy}px`;
    tip.classList.remove("hidden");
    cross.setAttribute("x1", X(x)); cross.setAttribute("x2", X(x)); cross.style.display = "";
  };
  const hide = () => { tip.classList.add("hidden"); cross.style.display = "none"; };
  $$(".hit", box).forEach(r => { r.addEventListener("pointerenter", () => show(r)); r.addEventListener("pointerdown", () => show(r)); });
  $("svg", box).addEventListener("pointerleave", hide);
}
let resizeT;
addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => { const st = STATS.find(s => s.key === S.expanded); if (st) drawChart(st); }, 120); });

function recordState(x, state) {
  if (!state || x < 0) return;
  S.hist[x] = state;
  renderStats();
}

/* ---------------- narration ---------------- */
const narrator = $("#narrator");
const N_Q = [];
let narrating = false, muted = false, unlocked = false, speakingWho = "";
try { muted = localStorage.getItem("sumer-muted") === "1"; } catch { /* storage blocked */ }
const SILENT = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
function unlockAudio() {
  if (unlocked || narrating) return;
  unlocked = true;
  narrator.src = SILENT;
  narrator.play().then(() => narrator.pause()).catch(() => { unlocked = false; });
}
// Clips play in order: the year's winner states the plan, then the narrator reads the headline.
function narrate(url, who = "narrator") { if (!url || muted) return; N_Q.push({ url, who }); playNext(); }
function playNext() {
  if (narrating || muted) return;
  const { url, who } = N_Q.shift() || {};
  if (!url) return;
  narrating = true; speakingWho = who;
  idleTyping();
  narrator.src = url;
  watchNarration();
  narrator.play().catch(() => { narrating = false; clearTimeout(narrateWatch); playNext(); });
}
// A stalled clip fires neither "ended" nor "error": skip it after NARRATE_STALL ms without progress.
const NARRATE_STALL = 15000;
let narrateWatch = 0;
function watchNarration() {
  clearTimeout(narrateWatch);
  if (narrating) narrateWatch = setTimeout(() => { if (!narrating) return; narrator.pause(); narrating = false; idleTyping(); playNext(); }, NARRATE_STALL);
}
narrator.addEventListener("timeupdate", watchNarration);
narrator.addEventListener("ended", () => { narrating = false; clearTimeout(narrateWatch); idleTyping(); playNext(); });
narrator.addEventListener("error", () => { if (narrating) { narrating = false; clearTimeout(narrateWatch); idleTyping(); playNext(); } });
function stopNarration() { N_Q.length = 0; narrator.pause(); narrating = false; clearTimeout(narrateWatch); }
function renderMute() {
  const b = $("#muteBtn");
  b.textContent = muted ? "🔇" : "🔊";
  b.setAttribute("aria-pressed", String(muted));
  b.title = b.ariaLabel = muted ? "Unmute voices" : "Mute voices";
}

/* ---------------- event queue ---------------- */
const PACED = new Set(["turn", "news", "tally", "runoff", "runoff_result", "year", "escalate", "end"]);
const Q = [];
let pumping = false, nextAt = 0, holdUntil = 0, gen = 0, wake = null;
let replay = false, replayQuiet = 0, replayCapT = 0;

function sleepUntil(t) {
  const ms = t - now();
  if (ms <= 0) return Promise.resolve();
  return new Promise(r => { const id = setTimeout(r, ms); wake = () => { clearTimeout(id); r(); }; });
}
const seen = new Set();
function onEvent(ev) {
  if (!ev || !ev.kind) return;
  if (ev.run_id && ev.run_id !== S.runId) return;
  if (ev.sequence != null && seen.has(ev.sequence)) return;
  if (ev.sequence != null) seen.add(ev.sequence);
  ev.instant = replay;
  if (ev.kind === "pause") { setPaused(ev.data.paused); return; }
  if (ev.kind === "end") setPaused(false);
  if (ev.kind === "game") { Q.length = 0; gen++; wake?.(); }
  Q.push(ev);
  pump();
}
async function pump() {
  if (pumping) return;
  pumping = true;
  try {
    while (Q.length) {
      if (S.paused && !Q[0].instant && Q[0].kind !== "game") break;
      const ev = Q.shift();
      try { await process(ev, gen); } catch (e) { console.error("render failed", ev.kind, e); }
    }
  } finally { pumping = false; }
  if (!Q.length) idleTyping();
}
async function process(ev, g) {
  const { kind, data: d = {}, instant } = ev;
  if (!instant && PACED.has(kind)) {
    if (kind === "turn") {
      typingFor(d.advisor);
      G.typing(d.advisor);
      await sleepUntil(Math.max(nextAt, holdUntil, now() + MIN_TYPING));
    } else {
      if (kind === "escalate" || kind === "year" || kind === "end") typing("");
      await sleepUntil(Math.max(nextAt, holdUntil));
    }
  } else if (!instant && (kind === "year_start" || kind === "round")) {
    await sleepUntil(Math.max(nextAt, holdUntil)); // the last speech finishes before the phase moves on
  }
  if (g !== gen && kind !== "game") return;
  if (S.paused && !instant && kind !== "game") { Q.unshift(ev); return; }
  render(kind, d, instant);
  if (PACED.has(kind) && !instant) nextAt = now() + (kind === "turn" ? readHold(d.speech) : MIN_GAP);
  if (kind === "year" && !instant) holdUntil = now() + HOLD_AFTER_YEAR;
  idleTyping();
}

// A speech stays on the graph long enough to read (the bubble shows up to ~230 chars);
// a long backlog falls back to the minimum so the council catches up.
function readHold(text) {
  if (Q.filter(e => e.kind === "turn").length > CATCH_UP) return READ_MIN;
  return Math.min(READ_MAX, Math.max(READ_MIN, 1000 + Math.min(String(text || "").length, 230) * READ_PER_CHAR));
}
function resetGame(d) {
  stopNarration();
  hideSpot();
  closeEscalation();
  S.scoutRequest = null; S.compareVote = null;
  window.CityEffects?.clear();
  hideRunoff();
  Object.assign(S, { leadHtml: "", cmpHtml: "", year: 0, round: null, completed: 0, hist: [], roundBase: {}, plans: {}, lastPlain: {}, stance: {}, running: true, waitingHuman: false });
  renderRiverPlans([]);
  S.years = d.years || S.meta?.years || 5;
  if (d.advisors) setAdvisors(d.advisors);
  else renderStances();
  nextAt = 0; holdUntil = 0;
  feed().innerHTML = "";
  unread = 0; renderUnread();
  G.idle(); setHubSub();
  recordState(0, d.state);
  renderYear();
  syncSetupLock();
}

// Runs that end early: plain words for the audience; the journal keeps the details.
const HALTED = {
  technical_failure: ["The council went quiet", "An advisor's model call failed. Convene to start again."],
  deadlock: ["Deadlock unresolved", "No decision was recorded for this year."],
  stopped: ["Council stopped", ""],
  lost: ["Council stopped", "The chamber restarted. Convene to start again."],
};
function render(kind, d, instant) {
  window.Clamor?.event(kind, d);
  switch (kind) {
    case "game":
      resetGame(d);
      setPhase("The council gathers", `<span>${N()} advisors · ${S.years} years · no king</span>`);
      break;
    case "year_start":
      S.year = d.year; S.round = null; S.stance = {}; S.plans = {}; S.leadHtml = ""; S.cmpHtml = ""; S.lastPlain = {}; renderStances(); hideRunoff(); hideOmenTally();
      renderRiverPlans([]);
      G.idle(instant);
      if (d.state) recordState(d.year - 1, d.state);
      renderYear();
      setPhase("The council debates", "");
      if (!instant) {
        spotlight("flash", `<div class="flash-year"><div class="big">Year ${esc(d.year)}</div><div class="of">of ${esc(S.years)}</div></div>`, { flash: true, ms: FLASH_YEAR });
        holdUntil = Math.max(holdUntil, now() + FLASH_YEAR);
      }
      setHubSub();
      { const s = d.state || S.hist[d.year - 1] || {};
        append(`Year ${esc(d.year)}<small>${fmt(s.population)} people · ${fmt(s.grain)} bushels · ${fmt(s.acres)} acres</small>`, "divider year", { instant }); }
      break;
    case "round": {
      if (d.year) S.year = d.year;
      const r = dispRound(d.year, d.round);
      setHubSub();
      const max = d.max_rounds || S.meta?.max_rounds;
      S.round = { number: r, limit: max };
      renderYear();
      if (d.phase === "runoff") {
        if (d.revote && S.runoff) {
          S.runoff.votes = {}; S.runoff.decided = false;
          renderRiverPlans([S.runoff.a, S.runoff.b].map(row => ({ ...row, accept: [] })));
          $("#runoff").classList.remove("decided");
          for (const side of ["a", "b"]) {
            $(`#ro-${side}`).classList.remove("win");
            $(`#ro-${side} .ro-votes`).innerHTML = "";
            $(`#ro-${side} .ro-count`).textContent = "0";
          }
          S.stance = {}; renderStances();
        }
        setPhase(d.revote ? "Runoff re-vote" : "Runoff vote", `<span>Every advisor picks <b>A</b> or <b>B</b></span>`); break; }
      setPhase(d.revote ? "Re-vote after the scout" : "The council debates", S.leadHtml || "");
      append(d.revote ? `Re-vote after scout · round ${r}` : `Round ${r}${max ? ` of ${max}` : ""}`, "divider", { instant });
      break;
    }
    case "turn": renderTurn(d, instant); if (d.phase !== "runoff") updateOmenTally(d.support); break;
    case "agent_skipped":
      typing("");
      hideSpot();
      delete S.stance[d.advisor];
      renderStances();
      append(`<span class="emoji">⏭</span><div><b>${esc(adv(d.advisor).short)} skipped this turn</b>Two attempts failed. The remaining advisors continue.</div>`, "note", { instant });
      break;
    case "tally": renderPoll(d, instant); compareVotes(d.rows, instant); updateOmenTally(d.rows); break;
    case "scout_request": showScout(d); break;
    case "scout_choice":
      if (S.scoutRequest?.request_id === d.request_id) { S.scoutRequest = null; hideSpot("scout"); }
      if (d.choice !== "skip") S.compareVote = d.rows;
      if (d.choice !== "skip" && d.phase !== "runoff") showOmenTally(d.choice, d.rows);
      if (d.announcement) {
        append(`<span class="emoji">${d.announcement.mood === "dread" ? "🌑" : "🎉"}</span><div><b>The herald</b><i>${esc(d.announcement.text)}</i></div>`, `note herald ${d.announcement.mood}`, { instant });
        if (!instant) narrate(d.announcement.url, "herald");
      }
      break;
    case "news": renderNews(d, instant); break;
    case "wire":
      G.pulse(instant);
      append(`📡 ${esc(d.transport || "wire")}${d.topic ? ` · ${esc(d.topic)}` : ""}${d.recipients ? ` · to ${d.recipients.length} agents` : ""}${d.bytes_out ? ` · ${(d.bytes_out / 1024).toFixed(1)} kB` : ""}${d.replies != null ? ` · ${d.replies} replies` : ""}${d.elapsed_s != null ? ` in ${d.elapsed_s}s` : ""}`, "wire hood-only", { instant: true });
      break;
    case "runoff": hideOmenTally(); showRunoff(d, instant); break;
    case "runoff_result":
      runoffResult(d, instant);
      compareVotes(Object.entries(d.count).map(([id]) => ({ id, accept: Object.entries(d.votes).filter(([, vote]) => vote === id).map(([who]) => who) })), instant);
      break;
    case "escalate": hideOmenTally(); showEscalation(d); break;
    case "escalation_timeout":
      closeEscalation();
      append(`<span class="emoji">🛟</span><div><b>Selection timed out</b>The council remains paused until a valid choice is made.</div>`, "note you", { instant });
      break;
    case "human_resolution":
      if (instant) append(`<span class="emoji">👑</span><div><b>Recorded human choice</b>${esc(planName(d.candidate_id))}.</div>`, "note", { instant });
      break;
    case "year":
      closeEscalation(); hideOmenTally();
      S.year = d.year; S.completed = Math.max(S.completed, d.year);
      if (d.state) recordState(d.year, d.state);
      renderYear();
      G.idle(instant); setHubSub();
      setPhase("Decision", outcomeBadge(d));
      renderYear_(d, instant);
      if (!instant) {
        const report = d.report || d.state?.last_report || {};
        const effects = [];
        if (report.plague_deaths > 0) effects.push("plague");
        if (report.rats > 0) effects.push("rats");
        window.CityEffects?.effect(effects);
        showYearSpot(d);
        narrate(d.speech_url, d.speech?.speaker); narrate(d.headline_url); narrate(d.narration_url);
      }
      hideRunoff();
      break;
    case "end":
      setup.liveRunId = "";
      S.scoutRequest = null; hideSpot(); hideOmenTally(); setPaused(false);
      S.running = false; S.round = null; S.completed = d.years?.length || 0; renderYear(); syncSetupLock();
      closeEscalation(); G.idle(instant);
      setHubSub(HALTED[d.status] ? "stopped" : d.status && d.status !== "completed" ? d.status.replaceAll("_", " ") : "reign complete");
      if (d.status && !["completed", "overthrown", "depopulated"].includes(d.status)) {
        const [title, sub] = HALTED[d.status] || [cap(d.status.replaceAll("_", " ")), ""];
        setPhase(title, sub ? `<span>${esc(sub)}</span>` : "");
        break;
      }
      setPhase("Reign complete", `<span>${esc(endTitle(d.rating || {})[0])}</span>`);
      renderEnd(d, instant);
      showEndSpot(d);
      break;
    case "observability_error":
    case "technical_failure":
    case "error":
      append(`<span class="emoji">⚠️</span><div><b>Something went wrong</b>${esc(d.message)}</div>`, "note error", { instant });
      if (!instant) toast(d.message);
      break;
    case "stopped":
      append(`<span class="emoji">⏹</span><div><b>Council stopped</b>${esc(d.message)}</div>`, "note", { instant });
      break;
    default: break;
  }
}

// Clear the stage when a run ends without its "end" event (Stop, or a restarted server).
function haltRun(status) {
  window.Clamor?.event("halt");
  stopNarration();
  setup.liveRunId = "";
  S.running = false;
  S.scoutRequest = null;
  Q.length = 0; gen++; wake?.(); // drop speeches still queued for display
  setPaused(false);
  closeEscalation();
  hideSpot();
  hideRunoff(); hideOmenTally();
  G.idle();
  S.stance = {}; renderStances();
  S.year = 0; S.round = null; renderYear();
  const [title, sub] = HALTED[status];
  setPhase(title, sub ? `<span>${esc(sub)}</span>` : "");
  syncSetupLock(); idleTyping();
}

/* ---------------- SSE ---------------- */
let es, connLost = false;
function armReplay() {
  clearTimeout(replayQuiet);
  replayQuiet = setTimeout(() => { replay = false; }, REPLAY_QUIET);
}
function connect(id) {
  if (!id) return;
  es?.close();
  if (S.runId !== id) seen.clear();
  S.runId = id;
  S.paused = !!(S.meta?.active_run === id && S.meta?.paused);
  S.running = true;
  setup.liveRunId = id;
  syncSetupLock();
  es = new EventSource(`/api/events?run_id=${encodeURIComponent(id)}`);
  replay = true;
  armReplay();
  clearTimeout(replayCapT);
  replayCapT = setTimeout(() => { replay = false; }, REPLAY_CAP);
  es.onmessage = m => {
    if (replay) armReplay();
    try { const ev = JSON.parse(m.data); onEvent(ev); if (ev.kind === "end") es.close(); } catch (e) { console.error("bad event", e); }
  };
  es.onopen = () => {
    if (!connLost) return;
    connLost = false;
    // A restarted server has forgotten the live run: show it as stopped rather than waiting forever.
    api("/api/meta").then(m => {
      if (S.runId !== id || m.active_run === id) return toast("Reconnected to the chamber", true);
      es.close(); haltRun("lost");
    }).catch(() => {});
  };
  es.onerror = () => {
    es.close();
    if (!connLost && S.running) { connLost = true; toast("Lost the connection to the chamber. Reconnecting…"); }
    setTimeout(() => connect(id), 1500);
  };
}

function voteSummary(rows) {
  return [...(rows || [])].sort((a, b) => (b.accept?.length || 0) - (a.accept?.length || 0))
    .filter(row => row.accept?.length).slice(0, 3)
    .map(row => `<div class="cmp-row"><span>${esc(planName(row.id, row.author))}</span><b>${row.accept.length} of ${N()}</b></div>`).join("") || "No votes";
}
function compareVotes(rows, instant) {
  if (!S.compareVote) return;
  const before = S.compareVote;
  // Plan ids change every round: compare whose plans each advisor backs.
  const votes = values => Object.fromEntries(S.advisors.map(a => [
    a.id, values.filter(row => row.accept?.includes(a.id)).map(row => planAuthor(row.id, row.author)).sort(),
  ]));
  const oldVotes = votes(before), newVotes = votes(rows);
  const changed = S.advisors.filter(a => JSON.stringify(oldVotes[a.id]) !== JSON.stringify(newVotes[a.id])).map(a => a.id);
  const verdict = changed.length ? `<b>${esc(nameList(changed))}</b> changed ${changed.length === 1 ? "their vote" : "votes"}.` : "Nobody changed their vote.";
  S.cmpHtml = `<p class="spot-sub cmp-verdict">${verdict}</p>
    <div class="cmp"><div class="cmp-col"><h4>Before the scout</h4>${voteSummary(before)}</div><div class="cmp-arrow">→</div><div class="cmp-col"><h4>After</h4>${voteSummary(rows)}</div></div>`;
  append(`<span class="emoji">🔁</span><div><b>Re-vote</b>${changed.length ? `${esc(nameList(changed))} changed their votes.` : "Nobody changed their vote."}</div>`, "note", { instant });
  S.compareVote = null;
}
// Angular relief marks match the map and remain decorative beside text labels.
const SCOUT_ICON = {
  plague: '<path d="M9 3h6l4 4v6l-4 4H9l-4-4V7zM12 0v3M12 17v4M1 10h4M19 10h4M3 1l4 4M17 15l4 4M3 19l4-4M17 5l4-4M9 8h1M14 12h1"/>',
  rats: '<path d="M4 15v-5l4-4h7l5 5v6H8l-4-2H1v-4M9 6V3h4v3M15 10h1M9 17v3M17 17v3M20 13h3"/>',
  good_harvest: '<path d="M12 22V3M12 7 6 3v5l6 4 6-4V3zM12 15l-6-4v5l6 4 6-4v-5z"/>',
  bad_harvest: '<path d="M12 22v-8l-4-4V3M8 7 3 3v5l5 4M12 14l5-3V6l-5 3M17 18l5 5M22 18l-5 5"/>',
  low_price: '<path d="M3 3v18h18M6 6l6 6 3-3 6 7M16 16h5v-5"/>',
  high_price: '<path d="M3 3v18h18M6 16l6-6 3 3 6-7M16 6h5v5"/>',
};
function scoutIcon(id) {
  return `<svg class="scout-mark" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="miter">${SCOUT_ICON[id] || '<path d="M5 19V5h14v14zM8 9h8M8 13h6"/>'}</svg>`;
}
const SCOUT_LABEL = { plague: "Plague", rats: "Rats", good_harvest: "Good harvest", bad_harvest: "Bad harvest", low_price: "Cheap land", high_price: "Costly land" };
// Where the vote stands: the leading plan(s), a majority status line and the runners-up.
function voteStanding(rows) {
  const ranked = [...(rows || [])].filter(r => r.id).sort((x, y) => (y.accept?.length || 0) - (x.accept?.length || 0));
  const most = ranked[0]?.accept?.length || 0, total = N(), { majority } = votingThresholds(total);
  const leaders = ranked.filter(r => (r.accept?.length || 0) === most);
  const status = leaders.length > 1 ? ["tie", `Tied with ${nameList(leaders.slice(1).map(r => planAuthor(r.id, r.author)))}`]
    : most === total ? ["all", "Every advisor agrees"]
    : most >= majority ? ["major", "Holds a majority"]
    : ["short", `${majority - most} short of a majority`];
  const rest = ranked.filter(r => r.accept?.length && !leaders.includes(r)).slice(0, 3);
  return { ranked, most, total, leaders, status, rest };
}
function standingFoot({ status, rest }) {
  return `<div class="standing-foot"><span class="standing-status ${status[0]}">${esc(status[1])}</span>${rest.length
    ? `<span class="standing-rest">Also backed: ${rest.map(r => `${esc(planName(r.id, r.author))} <b>${r.accept.length}</b>`).join(" · ")}</span>` : ""}</div>`;
}
function scoutPlans(rows) {
  if (!rows.length) return "";
  const st = voteStanding(rows), { most, leaders } = st;
  if (!most) return `<div class="scout-plans" id="omenStanding" aria-live="polite"><div class="scout-plan-heading">No plan has a vote yet</div></div>`;
  return `<div class="scout-plans" id="omenStanding" aria-live="polite" aria-label="Plans the council voted on">
    <div class="scout-plan-heading">${leaders.length > 1 ? "Plans tied for most support" : "Plan with most support"}</div>
    ${leaders.map(row => {
      const author = planAuthor(row.id, row.author);
      return `<div class="spot-plan">${author ? avatar(author) : ""}<span>
        <b>${esc(planName(row.id, author))}</b>
        <span>${esc(stripSafe(row.plain || (row.plan ? planText(row.plan) : "")))}</span>
        <small class="scout-plan-support"><span class="standing-num"><b>${most}</b> of ${N()} councilors support this plan</span>${voteDots(row.accept)}</small>
      </span></div>`;
    }).join("")}
    ${standingFoot(st)}
  </div>`;
}
// After an omen is sent, a small tablet on the map keeps the count live through the re-vote.
function omenTallyEl() {
  let el = $("#omenTally");
  if (!el) {
    el = document.createElement("section");
    el.id = "omenTally";
    el.className = "omen-tally hidden";
    el.setAttribute("aria-live", "polite");
    el.setAttribute("aria-label", "Re-vote after the omen");
    $("#graph").before(el); // under the graph, so speech bubbles stay readable over it
  }
  return el;
}
function omenTallyBody(rows) {
  const st = voteStanding(rows), lead = st.leaders[0];
  if (!lead || !st.most) return `<p class="standing-empty">No plan has a vote yet.</p>`;
  const au = planAuthor(lead.id, lead.author);
  return `<div class="omen-tally-lead">${avatar(au)}<b class="omen-tally-name">${esc(planName(lead.id, au))}</b>
    <span class="standing-num"><b>${st.most}</b> of ${st.total}</span>${voteDots(lead.accept)}</div>${standingFoot(st)}`;
}
function showOmenTally(choice, rows) {
  const el = omenTallyEl();
  el.innerHTML = `<div class="omen-tally-head">${scoutIcon(choice)}<span>${esc(SCOUT_LABEL[choice] || cap(choice))} sent · re-vote</span></div><div class="omen-tally-body">${omenTallyBody(rows)}</div>`;
  el.classList.remove("hidden");
}
function updateOmenTally(rows) {
  const el = $("#omenTally");
  if (!el || el.classList.contains("hidden") || !rows?.length) return;
  $(".omen-tally-body", el).innerHTML = omenTallyBody(rows);
}
function hideOmenTally() { $("#omenTally")?.classList.add("hidden"); }
function showScout(d) {
  S.scoutRequest = d;
  hideOmenTally();
  const rows = [...(d.rows || [])].sort((x, y) => (y.accept?.length || 0) - (x.accept?.length || 0));
  rows.forEach(r => { if (r.id && r.author) S.plans[r.id] = r.author; });
  const events = (d.options || []).filter(o => o.id !== "skip");
  S.cmpHtml = "";
  spotlight("scout", `<div class="spot-kicker">Omens</div>
    ${scoutPlans(rows)}
    <div class="scout-grid">${events.map((o, i) => `<button class="scout-ev" type="button" data-scout="${esc(o.id)}">${scoutIcon(o.id)}<b>${esc(SCOUT_LABEL[o.id] || o.label)}</b><kbd>${i + 1}</kbd></button>`).join("")}</div>
    <div class="spot-actions"><button class="btn primary" type="button" data-scout="skip" title="Let the vote stand (Enter)">Press the seal</button></div>`, { cls: "scout" });
  const btns = $$("#spot [data-scout]");
  btns.forEach(b => {
    b.disabled = S.paused;
    b.onclick = async () => {
      btns.forEach(x => x.disabled = true);
      try {
        await api("/api/scout", { run_id: S.runId, request_id: d.request_id, choice: b.dataset.scout });
        if (S.scoutRequest?.request_id === d.request_id) hideSpot("scout");
      } catch (e) {
        // Another open tab may have answered first; an automatic skip losing that race is not an error.
        if (b.dataset.auto) { hideSpot("scout"); return; }
        toast(e.message); btns.forEach(x => x.disabled = S.paused);
      }
    };
  });
  setPhase("Voting");
  idleTyping();
  startCountdown();
}
// A hands-free demo keeps moving: an untouched scout prompt lets the vote stand after
// AUTO_CONTINUE seconds. Any hover, key, or pause hands control back to the presenter.
let countT = 0;
function startCountdown() {
  stopCountdown();
  const skip = $('#spot [data-scout="skip"]');
  if (!skip || S.paused || !$("#autoContinue").checked) return;
  let left = AUTO_CONTINUE;
  const label = skip.textContent;
  const tick = () => { skip.textContent = `${label} · ${left}`; };
  skip.style.setProperty("--count", `${AUTO_CONTINUE}s`);
  skip.classList.add("counting");
  tick();
  countT = setInterval(() => {
    if (--left > 0) return tick();
    stopCountdown();
    if (!skip.disabled) { skip.dataset.auto = "1"; skip.click(); }
  }, 1000);
  stopCountdown.reset = () => { skip.textContent = label; skip.classList.remove("counting"); };
  $("#spot .spot-card").addEventListener("pointerenter", stopCountdown, { once: true });
}
function stopCountdown() {
  clearInterval(countT); countT = 0;
  stopCountdown.reset?.(); stopCountdown.reset = null;
}
function setPaused(paused) {
  S.paused = paused;
  const button = $("#pauseBtn");
  button.textContent = paused ? "Resume" : "Pause";
  document.body.classList.toggle("paused", paused);
  button.setAttribute("aria-pressed", String(paused));
  $$("#escOptions button").forEach(b => b.disabled = paused);
  if (paused) { narrator.pause(); wake?.(); }
  else { if (narrating && !muted) narrator.play().catch(() => {}); pump(); }
  if (spotKind === "scout") {
    $$("#spot [data-scout]").forEach(b => b.disabled = paused);
    if (paused) stopCountdown();
  }
  idleTyping();
}

/* ---------------- controls ---------------- */
// Fictional Sumerian-themed names, shared across every role. Draw without replacement.
const COUNCIL_NAMES = [
  "Enlil-bani", "Nin-aya", "Ur-Nammu", "En-hedu", "Lugal-ani", "Nin-lil", "Ur-Nanshe", "Ama-ningal",
  "Enki-edu", "Nin-sun", "Ur-Shara", "Geme-Inanna", "Lugal-esh", "Nin-azu", "Ur-Bau", "Ama-gesh",
  "En-men", "Nin-ili", "Ur-Dumuzi", "Geme-Nanshe", "Lugal-zi", "Nin-ezen", "Ur-Ningirsu", "Ama-kala",
  "En-shara", "Nin-me", "Ur-Utu", "Geme-Bau", "Lugal-kala", "Nin-dara", "Ur-Enki", "Ama-sun",
  "En-azu", "Nin-gesh", "Ur-Nanna", "Geme-Ningal", "Lugal-mesh", "Nin-bala", "Ur-Enlil", "Ama-zi",
  "En-nam", "Nin-kala", "Ur-Girra", "Geme-Shara", "Lugal-edu", "Nin-tura", "Ur-Isimud", "Ama-lil",
  "En-dara", "Nin-zida", "Ur-Ninurta", "Geme-Utu", "Lugal-bani", "Nin-saga", "Ur-Nisaba", "Ama-esh",
  "En-tura", "Nin-nam", "Ur-Zababa", "Geme-Nisaba", "Lugal-zida", "Nin-edu", "Ur-Hendursag", "Ama-bala",
];
const usedCouncilNames = new Set();
function drawCouncilName() {
  const available = COUNCIL_NAMES.filter(name => !usedCouncilNames.has(name));
  // Extend the pool after repeated council resets; never recycle a used name.
  if (!available.length) {
    for (const name of COUNCIL_NAMES) {
      for (const place of ["Ur", "Uruk", "Eridu", "Lagash", "Nippur", "Kish", "Umma", "Girsu"]) {
        const extended = `${name}-${place}`;
        if (!usedCouncilNames.has(extended)) available.push(extended);
      }
    }
  }
  const name = available.length
    ? available[Math.floor(Math.random() * available.length)]
    : `Ur-ki-${usedCouncilNames.size + 1}`;
  usedCouncilNames.add(name);
  return name;
}
function newVoter(i) {
  const serial = ++setup.serial, voice = setup.voices[(serial - 1) % setup.voices.length]?.id;
  const preset = setup.templates[i % setup.templates.length]?.id || "provider";
  return { rulebook: S.meta?.example_rulebooks?.[preset] || "", name: drawCouncilName(), preset, custom: { id: `custom-${serial}`, short: "", mandate: "", ...(voice ? { tts_voice: voice } : {}) } };
}
function votingThresholds(n) {
  const majority = Math.floor(n / 2) + 1;
  return { majority, strong: Math.max(majority, Math.ceil(2 * n / 3)), runoff: Math.max(1, majority - 1) };
}
function loadExampleRulebooks() {
  if (!setup.ready || setup.starting || setup.stopping || setup.liveRunId) return;
  if (!S.meta?.example_rulebooks) {
    $("#exampleRulebookStatus").textContent = "Examples are unavailable. Reload after the server is ready.";
    return;
  }
  if (!resizeVoters()) return;
  let loaded = 0;
  for (const seat of setup.seats) {
    const example = S.meta?.example_rulebooks?.[seat.preset];
    if (seat.preset !== "custom" && !seat.rulebook?.trim() && typeof example === "string") {
      seat.rulebook = example;
      loaded++;
    }
  }
  renderVoters();
  $("#exampleRulebookStatus").textContent = loaded
    ? `Loaded ${loaded} example rulebook${loaded === 1 ? "" : "s"}. You can edit each one below.`
    : "No empty preset rulebooks to fill. Existing notes and custom advisors are kept.";
}
function setupError(message = "") {
  $("#setupError").textContent = message;
  $("#setupError").classList.toggle("hidden", !message);
}
function syncSetupLock() {
  const locked = setup.starting || setup.stopping || !!setup.liveRunId;
  $("#councilSetup").disabled = locked || !setup.ready;
  for (const id of ["seed", "rounds", "startBtn", "conveneBtn"]) {
    const el = document.getElementById(id);
    if (el) el.disabled = locked || !setup.ready;
  }
  $("#setupLocked").classList.toggle("hidden", !locked);
  $("#conveneBtn").classList.toggle("hidden", locked || !setup.ready);
  $("#pauseBtn").disabled = !setup.liveRunId;
  $("#stopBtn").disabled = !setup.liveRunId || setup.stopping;
}
function renderVoters() {
  const n = setup.seats.length, rules = votingThresholds(n);
  const roster = configuredAdvisors();
  $("#agentCount").value = n;
  $("#votingRules").textContent = `Majority: ${rules.majority} of ${n}`;
  previewCouncil();
  $("#voterList").innerHTML = setup.seats.map((seat, i) => {
    const custom = seat.preset === "custom", a = setup.templates.find(a => a.id === seat.preset);
    return `<section class="voter-card" data-slot="${i}">
      <div class="voter-row">${avatar(roster[i].id)}<select data-voter="preset" aria-label="Voter ${i + 1}">${setup.templates.map(a => `<option value="${esc(a.id)}"${a.id === seat.preset ? " selected" : ""}>${esc(a.short)}</option>`).join("")}<option value="custom"${custom ? " selected" : ""}>Custom agent</option></select></div>
      <div class="voter-name">${esc(roster[i].title || roster[i].short)}</div>
      <label class="voter-bias"><span>Bias · flexibility and belief confidence</span><input type="number" data-voter="bias" min="0" max="1" step="0.05" value="${esc(seat.bias ?? a?.bias ?? 0)}" required aria-label="Voter ${i + 1} bias" aria-describedby="bias-note-${i}"></label>
      ${custom ? `<input type="text" data-voter="short" value="${esc(seat.custom.short)}" maxlength="30" required placeholder="Role, e.g. Canal Keeper" aria-label="Voter ${i + 1} role">
      <textarea data-voter="mandate" rows="2" maxlength="2000" required placeholder="What does this agent want?" aria-label="Voter ${i + 1} mandate">${esc(seat.custom.mandate)}</textarea>
      ${setup.voices.length ? `<label class="voter-voice"><span>Voice</span><select data-voter="tts_voice" aria-label="Voter ${i + 1} voice">${setup.voices.map(v => `<option value="${esc(v.id)}"${v.id === seat.custom.tts_voice ? " selected" : ""}>${esc(v.label)}</option>`).join("")}</select></label>` : ""}` : ""}
      <p class="rulebook-note" id="bias-note-${i}">0: tentative and flexible. 1: strongly trusts its notes and resists compromise, but can revise with compelling evidence.</p>
      <label class="field private-rulebook"><span>Private rulebook</span><textarea data-voter="rulebook" rows="4" maxlength="8000" aria-label="Voter ${i + 1} private rulebook" aria-describedby="rulebook-note-${i}" placeholder="Enter this decision maker's starting information (optional)">${esc(seat.rulebook || "")}</textarea></label>
      <p class="rulebook-note" id="rulebook-note-${i}">Edit this councilor’s goals, preferences, omen responses, and starting knowledge. Its learned beliefs are saved separately. Blank means no default instructions.</p>
    </section>`;
  }).join("");
  syncSetupLock();
}
function configuredAdvisors() {
  const seenRoles = {};
  return setup.seats.map(seat => {
    const knowledge = { rulebook: seat.rulebook || "" };
    seat.name ||= drawCouncilName();
    if (seat.preset === "custom") {
      const title = `${seat.name} the ${seat.custom.short.trim() || "Councilor"}`;
      return { ...seat.custom, bias: seat.bias ?? 0, short: seat.name, title, mandate: seat.custom.mandate.trim(), ...knowledge };
    }
    const template = setup.templates.find(a => a.id === seat.preset);
    const bias = seat.bias ?? template.bias ?? 0;
    const number = seenRoles[template.id] = (seenRoles[template.id] || 0) + 1;
    const name = { short: seat.name, title: `${seat.name} the ${template.title || template.short}` };
    if (number === 1) return { id: template.id, ...name, bias, ...knowledge };
    // Additional copies are independent voters with the selected persona's mandate.
    return { id: seat.custom.id, ...name, bias, mandate: template.mandate, voice: template.voice, color: template.color, emoji: template.emoji, tts_voice: template.tts_voice, ...knowledge };
  });
}
function previewCouncil() {
  if (S.runId || !setup.ready) return;
  setAdvisors(configuredAdvisors().map((a, i) => {
    const seat = setup.seats[i];
    const template = seat.preset === "custom" ? {} : setup.templates.find(t => t.id === seat.preset);
    return { ...template, ...a, short: a.short || (seat.preset === "custom" ? "Custom" : template?.short), title: a.title || a.short || template?.title || "Custom agent" };
  }));
  setPhase(`${N()} advisor${N() === 1 ? "" : "s"}. One city. No king.`, "");
  setHubSub();
  welcome();
}
function resizeVoters() {
  const input = $("#agentCount"), n = Number(input.value);
  if (!Number.isInteger(n) || n < 1 || n > setup.max) {
    setupError(`Choose a whole number from 1 to ${setup.max}.`);
    return false;
  }
  setup.seats = Array.from({ length: n }, (_, i) => setup.seats[i] || newVoter(i));
  setupError();
  renderVoters();
  return true;
}
function validateVoters() {
  if (!resizeVoters()) { openDrawer(true); $("#agentCount").focus(); return false; }
  for (const [i, seat] of setup.seats.entries()) {
    if (seat.bias != null && (!Number.isFinite(seat.bias) || seat.bias < 0 || seat.bias > 1)) {
      setupError(`Voter ${i + 1}: choose a bias from 0 to 1.`);
      openDrawer(true);
      $(`[data-slot="${i}"] [data-voter="bias"]`).focus();
      return false;
    }
    if (seat.preset === "custom" && (!seat.custom.short.trim() || !seat.custom.mandate.trim())) {
      setupError(`Voter ${i + 1}: enter a role and instructions for your custom agent.`);
      openDrawer(true);
      $(`[data-slot="${i}"] [data-voter="${seat.custom.short.trim() ? "mandate" : "short"}"]`).focus();
      return false;
    }
  }
  return true;
}
function openDrawer(open) {
  $("#drawer").classList.toggle("open", open);
  $("#drawer").setAttribute("aria-hidden", String(!open));
  $("#drawer").inert = !open;
  $("#drawerBack").classList.toggle("hidden", !open);
  $("#setupBtn").setAttribute("aria-expanded", String(open));
  if (open) $("#seed").focus({ preventScroll: true });
  if (open && setup.liveRunId && !setup.starting) {
    api("/api/meta").then(m => { setup.liveRunId = m.active_run || ""; syncSetupLock(); }).catch(() => {});
  }
}
async function start() {
  if (!setup.ready || setup.starting || setup.liveRunId) return;
  if (!validateVoters()) return;
  unlockAudio();
  const body = { seed: +$("#seed").value || 0, advisors: configuredAdvisors() };
  const rounds = +$("#rounds").value;
  if (rounds) body.max_rounds = rounds;
  setup.starting = true;
  setupError(); syncSetupLock();
  try {
    const result = await api("/api/game", body);
    connect(result.run_id); openDrawer(false);
  } catch (e) { setupError(`Could not start: ${e.message}`); openDrawer(true); }
  finally { setup.starting = false; syncSetupLock(); }
}
function setHood(on) {
  S.hood = on;
  document.body.classList.toggle("hood", on);
  G.setHub({ label: hubLabel() });
  $("#hoodToggle").setAttribute("aria-pressed", String(on));
  try { localStorage.setItem("sumer-hood", on ? "1" : "0"); } catch { /* storage blocked */ }
}
async function openEvidence() {
  $("#evidence").classList.remove("hidden");
  try {
    const b = await api("/api/bench");
    if (b.missing || !b.rows) { $("#benchMeta").textContent = "No results yet. Run: uv run sumer bench"; return; }
    const cols = [["policy", "Who decides"], ["score", "Score"], ["survived", "Reigns survived"], ["starved", "Avg starved"], ["population", "Avg people"], ["fairness", "Fairness"]];
    $("#benchTable thead").innerHTML = `<tr>${cols.map(c => `<th>${c[1]}</th>`).join("")}</tr>`;
    $("#benchTable tbody").innerHTML = b.rows.map(r => `<tr class="${r.key === "council" ? "ours" : ""}">${cols.map(c => `<td>${esc(r[c[0]])}</td>`).join("")}</tr>`).join("");
    const c = b.rows.find(r => r.key === "council");
    $("#benchMeta").textContent = `${b.seeds} games with the same luck for everyone.${c?.consensus != null ? ` The council decided ${c.consensus} years together, ${c.majority} by majority and asked a human ${c.escalated} times.` : ""} Fairness is how well the least happy advisor is served (0 to 1).`;
    const info = b.info || [];
    const ic = Object.keys(info[0] || {});
    $("#infoTable thead").innerHTML = `<tr>${ic.map(k => `<th>${esc(cap(k))}</th>`).join("")}</tr>`;
    $("#infoTable tbody").innerHTML = info.map(r => `<tr>${ic.map(k => `<td>${esc(r[k])}</td>`).join("")}</tr>`).join("");
    $("#lessons").innerHTML = (b.lessons || []).map(l => `<li>${esc(l)}</li>`).join("");
  } catch (e) { $("#benchMeta").textContent = `Could not load results: ${e.message}`; }
}

function wire() {
  $("#agentCount").onchange = resizeVoters;
  $("#loadExampleRulebooks").onclick = loadExampleRulebooks;
  $("#defaultVoters").onclick = () => {
    if (!resizeVoters()) return;
    setup.seats = setup.seats.map((_, i) => newVoter(i));
    renderVoters();
    $("#exampleRulebookStatus").textContent = "Default rulebooks loaded. Edit each councilor’s instructions below.";
  };
  $("#voterList").addEventListener("input", e => {
    const key = e.target.dataset.voter, card = e.target.closest("[data-slot]");
    if (!key || !card) return;
    const seat = setup.seats[Number(card.dataset.slot)];
    if (key === "rulebook") { seat.rulebook = e.target.value; setupError(); return; }
    if (key === "preset") {
      const previousDefault = S.meta?.example_rulebooks?.[seat.preset];
      if (seat.rulebook === previousDefault) seat.rulebook = S.meta?.example_rulebooks?.[e.target.value] || "";
      seat.preset = e.target.value; delete seat.bias;
    }
    else if (key === "bias") seat.bias = e.target.value === "" ? NaN : Number(e.target.value);
    else seat.custom[key] = e.target.value;
    if (key === "preset") renderVoters();
    else previewCouncil();
    setupError();
  });
  $("#setupBtn").onclick = () => openDrawer(!$("#drawer").classList.contains("open"));
  $("#drawerClose").onclick = () => openDrawer(false);
  $("#drawerBack").onclick = () => openDrawer(false);
  $("#startBtn").onclick = start;
  $("#conveneBtn").onclick = start;
  $("#stopBtn").onclick = async () => {
    if (!setup.liveRunId || setup.stopping) return;
    setup.stopping = true;
    $("#stopBtn").textContent = "Stopping…";
    syncSetupLock();
    try {
      await api("/api/stop", {});
      haltRun("stopped");
    } catch (e) { toast(e.message); }
    finally {
      setup.stopping = false;
      $("#stopBtn").textContent = "Stop";
      syncSetupLock();
    }
  };
  $("#pauseBtn").onclick = async () => {
    const button = $("#pauseBtn"); button.disabled = true;
    try {
      const result = await api("/api/pause", { run_id: S.runId, paused: !S.paused });
      setPaused(result.paused);
    } catch (e) { toast(e.message); }
    finally { syncSetupLock(); }
  };
  $("#hoodToggle").onclick = () => setHood(!S.hood);
  $("#autoContinue").onchange = e => {
    try { localStorage.setItem("sumer-auto", e.target.checked ? "1" : "0"); } catch { /* storage blocked */ }
    if (!e.target.checked) stopCountdown();
  };
  $("#muteBtn").onclick = () => {
    muted = !muted;
    if (muted) stopNarration();
    try { localStorage.setItem("sumer-muted", muted ? "1" : "0"); } catch { /* storage blocked */ }
    renderMute();
  };
  $("#evidenceBtn").onclick = openEvidence;
  $("#evidenceClose").onclick = () => $("#evidence").classList.add("hidden");
  $("#evidence").onclick = e => { if (e.target.id === "evidence") $("#evidence").classList.add("hidden"); };
  $("#jumpBtn").onclick = () => scrollBottom(false);
  $("#txToggle").onclick = () => setTranscript(txCollapsed);
  for (const t of ["wheel", "touchmove", "keydown"]) feed().addEventListener(t, () => { userScroll = true; }, { passive: true });
  // Keep following the newest entry when the panel itself changes size.
  new ResizeObserver(() => { if (stick && !txCollapsed) { feed().style.scrollBehavior = "auto"; feed().scrollTop = feed().scrollHeight; } }).observe(feed());
  feed().addEventListener("scroll", () => {
    if (userScroll) { stick = nearBottom(); userScroll = false; }
    if (nearBottom()) $("#jumpBtn").classList.add("hidden");
  });
  let paceT;
  $("#pace").oninput = e => {
    $("#paceVal").textContent = `${(+e.target.value).toFixed(1)} s`;
    clearTimeout(paceT);
    paceT = setTimeout(() => api("/api/pace", { pace_s: +e.target.value }).catch(err => toast(err.message)), 200);
  };
  addEventListener("keydown", e => {
    if (e.key === "Escape") {
      openDrawer(false);
      $("#evidence").classList.add("hidden");
      if (spotKind === "year" || spotKind === "end") hideSpot();
      return;
    }
    // Presenter keys work from anywhere except text fields.
    if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest?.("input, textarea, select")) return;
    const key = e.key.toLowerCase();
    if (key === " ") {
      if (e.target.closest?.("button, summary, a")) return; // Space presses the focused control
      e.preventDefault(); if (!$("#pauseBtn").disabled) $("#pauseBtn").click(); return;
    }
    if (key === "m") return $("#muteBtn").click();
    if (key === "h") return $("#hoodToggle").click();
    if (key === "f") return (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen?.())?.catch(() => {});
    if (S.waitingHuman && /^[1-9]$/.test(e.key)) {
      const b = $$("#escOptions .esc-opt")[+e.key - 1];
      if (b && !b.disabled) { e.preventDefault(); b.click(); }
      return;
    }
    // The presenter drives scout choices from the keyboard: 1-6 send a scout, Enter continues.
    if (spotKind !== "scout" || e.target.closest?.(".drawer") || ["Shift", "Control", "Alt", "Meta"].includes(e.key)) return;
    stopCountdown();
    if (e.key === "Enter" && e.target.closest?.("#spot button")) return;
    const b = e.key === "Enter" ? $('#spot [data-scout="skip"]') : /^[1-9]$/.test(e.key) ? $$("#spot .scout-ev")[+e.key - 1] : null;
    if (b && !b.disabled) { e.preventDefault(); b.click(); }
  });
  $("#spot").addEventListener("click", e => { if (spotKind === "year" && !e.target.closest("button")) hideSpot("year"); });
  // Browsers block audio until a user gesture: any click unlocks the narrator.
  addEventListener("pointerdown", unlockAudio, { once: true, capture: true });
}

// Screens larger than 1080p at 1x (4K TVs, 1440p projectors) show the 1080p layout scaled up,
// so faces and text keep their size relative to the room.
function fitZoom() {
  const z = Math.min(innerWidth / 1920, innerHeight / 1080);
  const zoom = z > 1.1 ? +z.toFixed(3) : 1;
  document.documentElement.style.zoom = zoom === 1 ? "" : zoom;
  document.documentElement.style.setProperty("--z", zoom);
}
fitZoom();
addEventListener("resize", fitZoom);

(async function init() {
  window.CityView?.mount();
  G.mount($("#graph"));
  wire();
  buildStats();
  renderStats();
  renderMute();
  renderYear();
  welcome();
  try { if (localStorage.getItem("sumer-hood") === "1") setHood(true); } catch { /* storage blocked */ }
  try { if (localStorage.getItem("sumer-tx") === "0") setTranscript(false); } catch { /* storage blocked */ }
  try { $("#autoContinue").checked = localStorage.getItem("sumer-auto") !== "0"; } catch { /* storage blocked */ }
  try {
    S.meta = await api("/api/meta");
  } catch (e) {
    S.meta = {};
    toast(`Server not reachable: ${e.message}`);
  }
  const m = S.meta;
  setup.templates = (m.advisors?.length ? m.advisors : Object.keys(FALLBACK_EMOJI).map(id => ({ id, mandate: "" }))).map(normAdvisor);
  setup.max = Number.isInteger(m.max_advisors) ? Math.min(21, Math.max(1, m.max_advisors)) : 21;
  $("#agentCount").max = setup.max;
  setup.voices = Array.isArray(m.voices) ? m.voices.filter(v => v?.id && v?.label) : [];
  setup.seats = setup.templates.slice(0, setup.max).map((_, i) => newVoter(i));
  setup.ready = true;
  setup.liveRunId = m.active_run || "";
  renderVoters();
  $("#chipTransport").textContent = m.transport === "slim" ? "AGNTCY SLIM" : m.transport ? String(m.transport) : "-";
  if (!S.runId) setHubSub();
  $("#chipLlm").textContent = m.llm === "bayleaf" || m.llm === "llm" ? String(m.model || "LLM").split("/").pop() : m.llm ? String(m.llm) : "-";
  if (m.max_rounds) {
    const sel = $("#rounds");
    if (![...sel.options].some(o => +o.value === +m.max_rounds)) sel.insertAdjacentHTML("beforeend", `<option>${m.max_rounds}</option>`);
    sel.value = String(m.max_rounds);
  }
  setPaused(!!m.active_run && !!m.paused);
  if (m.active_run) connect(m.active_run);
  // Evidence appears only when a benchmark for the current rules exists.
  api("/api/bench").then(b => $("#evidenceBtn").classList.toggle("hidden", !b?.rows?.length)).catch(() => {});
})();
