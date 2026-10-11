/* Council of Sumer - chamber UI.
 * Events arrive over SSE (/api/events) and go through one client-side queue so the
 * chat reads like a group chat: one speaker at a time, at least MIN_GAP ms apart.
 * A replayed backlog (page reload) renders instantly. */
"use strict";

const MIN_GAP = 1000;        // ms between visible messages
const MIN_TYPING = 650;      // ms the "is typing" indicator shows before a speech
const HOLD_AFTER_YEAR = 3000; // ms to let the year summary sit before the next year starts
const REPLAY_QUIET = 150;    // ms of silence that ends a backlog replay burst
const REPLAY_CAP = 2000;     // replay mode never lasts longer than this after connecting

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
  advisors: [],
  years: 5,
  year: 0,            // year being decided
  completed: 0,       // years finished
  hist: [],           // hist[x] = state after x completed years (x = 0 is the start)
  roundBase: {},      // year -> 1 if rounds are 0-based, else 0
  plans: {},          // plan id -> author id
  runoff: null,       // {a, b, votes: {advisor: planId}, decided}
  stance: {},         // advisor -> "agree" | "disagree" | "redline" | "vote-a" | "vote-b"
  running: false,
  waitingHuman: false,
  expanded: null,     // stat key with an open chart
  hood: false,
};

const FALLBACK_COLORS = ["#5f8f3a", "#7a4fa3", "#a33a32", "#b5862a", "#b2622f", "#2f8a7c", "#2f5ea8", "#6b5a45"];
const FALLBACK_EMOJI = { farmer: "🌾", priest: "🔮", general: "🛡️", merchant: "💰", builder: "🧱", healer: "🌿", diplomat: "🕊️" };

function normAdvisor(a, i) {
  const id = a.id || `advisor${i}`;
  const title = a.title || cap(id);
  return {
    ...a, id, title,
    short: a.short || (FALLBACK_EMOJI[id] ? cap(id) : title.split(" ").slice(-1)[0]),
    emoji: a.emoji || FALLBACK_EMOJI[id] || "👤",
    color: a.color || FALLBACK_COLORS[i % FALLBACK_COLORS.length],
    goal: a.goal || String(a.mandate || "").split(/(?<=\.)\s/)[0] || "",
  };
}
function setAdvisors(list) {
  if (!Array.isArray(list) || !list.length) return;
  S.advisors = list.map(normAdvisor);
  buildSeats();
}
const adv = id => S.advisors.find(a => a.id === id) || normAdvisor({ id: id || "someone" }, 7);
const N = () => S.advisors.length || 7;
const avatar = (id, cls = "") => { const a = adv(id); return `<span class="av ${cls}" style="--c:${a.color}" title="${esc(a.title)}">${a.emoji}</span>`; };
const nameList = ids => { const n = ids.map(i => adv(i).short); return n.length <= 1 ? n.join("") : `${n.slice(0, -1).join(", ")} and ${n[n.length - 1]}`; };

function planAuthor(id, fallback) {
  if (!id) return fallback || null;
  if (S.plans[id]) return S.plans[id];
  const head = String(id).split("-")[0];
  return S.advisors.some(a => a.id === head) ? head : fallback || null;
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
const stripSafe = t => cap(String(t || "").replace(/^play it safe:\s*/i, ""));
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

/* ---------------- seats ---------------- */
function buildSeats() {
  const el = $("#seats");
  el.style.setProperty("--n", S.advisors.length);
  el.innerHTML = S.advisors.map(a => `<div class="seat" id="seat-${a.id}" style="--c:${a.color}" title="${esc(a.title)}${a.red_line ? ` - red line: ${esc(a.red_line)}` : ""}">
    <span class="to-tag">spoken to</span>
    <span class="av">${a.emoji}<span class="badge hidden"></span></span>
    <span class="seat-name">${esc(a.short)}</span>
    <span class="seat-goal">${esc(a.goal)}</span></div>`).join("");
  renderStances();
}
function spotlight(speaker, addressed = []) {
  const el = $("#seats");
  el.classList.toggle("active", !!speaker);
  for (const a of S.advisors) {
    const s = $(`#seat-${a.id}`);
    if (!s) continue;
    s.classList.toggle("speaking", a.id === speaker);
    s.classList.toggle("addressed", addressed.includes(a.id) && a.id !== speaker);
  }
}
const STANCE = {
  agree: ["agree", "✓", "Agrees with the leading plan"],
  disagree: ["disagree", "✕", "Does not back the leading plan"],
  redline: ["redline", "!", "Red line: refuses the leading plan"],
  "vote-a": ["vote-a", "A", "Votes for plan A"],
  "vote-b": ["vote-b", "B", "Votes for plan B"],
};
function renderStances() {
  for (const a of S.advisors) {
    const b = $(`#seat-${a.id} .badge`);
    if (!b) continue;
    const st = STANCE[S.stance[a.id]];
    b.classList.toggle("hidden", !st);
    if (st) { b.className = `badge ${st[0]}`; b.textContent = st[1]; b.title = st[2]; }
  }
}

/* ---------------- header + stage ---------------- */
function renderYear() {
  const steps = [];
  for (let i = 1; i <= S.years; i++) {
    steps.push(`<span class="${i <= S.completed ? "done" : i === S.year ? "now" : ""}"></span>`);
  }
  $("#yearSteps").innerHTML = steps.join("");
  $("#yearLabel").textContent = !S.year ? "Ready" : S.completed >= S.years ? `All ${S.years} years done` : `Year ${S.year} of ${S.years}`;
}
function setPhase(title, sub = "") {
  $("#phase").textContent = title;
  $("#phaseSub").innerHTML = sub;
}
function voteDots(accept = [], veto = []) {
  return `<span class="dots">${S.advisors.map(a => {
    const cls = veto.includes(a.id) ? "no" : accept.includes(a.id) ? "yes" : "";
    const t = veto.includes(a.id) ? "red line" : accept.includes(a.id) ? "agrees" : "not yet";
    return `<span class="dot ${cls}" title="${esc(a.short)}: ${t}">${cls ? a.emoji : ""}</span>`;
  }).join("")}</span>`;
}

/* ---------------- typing indicator ---------------- */
function typing(html) { $("#typing").innerHTML = html || ""; }
const bounce = `<span class="bounce" aria-hidden="true"><i></i><i></i><i></i></span>`;
function typingFor(id) { typing(`${avatar(id, "mini")}<span><b>${esc(adv(id).short)}</b> is typing</span>${bounce}`); }
function idleTyping() {
  if (S.waitingHuman) typing(`<span>Waiting for <b>your decision</b></span>`);
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
  if (stick || instant) scrollBottom(instant);
  else $("#jumpBtn").classList.remove("hidden");
  return el;
}
function scrollBottom(instant) {
  const f = feed();
  f.style.scrollBehavior = instant || reduced.matches ? "auto" : "smooth";
  f.scrollTop = f.scrollHeight;
  stick = true;
  $("#jumpBtn").classList.add("hidden");
}
function welcome() {
  feed().innerHTML = `<div class="welcome">
    <div class="welcome-mark" aria-hidden="true">🏛️</div>
    <h1>Seven advisors. One city. No king.</h1>
    <p>Each advisor wants something different. Watch them argue, vote, and decide together how to run ancient Sumer for five years. When they are stuck, you decide.</p>
    <button class="btn primary big" id="welcomeStart" type="button">Convene the council</button></div>`;
  $("#welcomeStart").onclick = start;
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

function renderTurn(d, instant) {
  const a = adv(d.advisor);
  const addressed = (d.addressed || []).filter(x => x !== d.advisor);
  if (d.plan_id) S.plans[d.plan_id] = d.advisor;
  const foot = [];
  if (d.phase === "runoff" || d.runoff_vote) {
    const side = S.runoff && d.runoff_vote === S.runoff.b.id ? "b" : "a";
    const pid = d.runoff_vote;
    if (pid) foot.push(`<span class="vote-chip runoff-pick" style="--k:var(--${side === "a" ? "lapis" : "clay"})">Votes for ${side.toUpperCase()}: ${avatar(planAuthor(pid), "mini")}<b>${esc(planName(pid))}</b></span>`);
  } else {
    const plain = d.plain || planText(d.proposal);
    if (plain) foot.push(`<span class="plan-chip"><span class="emoji">📜</span><span>${esc(plain)}</span></span>`);
    const acc = [...new Set((d.accept || []).map(id => planAuthor(id)).filter(Boolean))];
    const vet = [...new Set((d.veto || []).map(id => planAuthor(id)).filter(Boolean))];
    if (acc.length) foot.push(`<span class="vote-chip" title="Plans ${esc(a.short)} would accept">Backs ${acc.map(x => `<span class="mini" title="${esc(adv(x).short)}'s plan">${adv(x).emoji}</span>`).join("")}</span>`);
    if (vet.length) foot.push(`<span class="vote-chip veto" title="Plans that cross ${esc(a.short)}'s red line">Red line ${vet.map(x => `<span class="mini" title="${esc(adv(x).short)}'s plan">${adv(x).emoji}</span>`).join("")}</span>`);
  }
  const meta = d.phase === "runoff" ? "Runoff" : d.round != null ? `Round ${dispRound(d.year, d.round)}` : "";
  const src = d.source ? `<span class="tag hood-only">${esc(d.source === "llm" ? "LLM" : d.source)}</span>` : "";
  const think = d.thought ? `<button class="think-btn" type="button" aria-expanded="${S.hood}" title="What ${esc(a.short)} privately thought">💭</button>` : "";
  const trace = d.trace && Object.keys(d.trace).length ? `<pre class="trace hood-only">${esc(traceText(d.trace))}</pre>` : "";
  const el = append(`${avatar(d.advisor)}
    <div class="msg-body">
      <div class="msg-head"><span class="msg-name" style="--c:${a.color}">${esc(a.short)}</span>
        ${addressed.length ? `<span class="msg-to">to ${esc(nameList(addressed))}</span>` : ""}${src}${think}<span class="msg-meta">${esc(meta)}</span></div>
      <div class="speech">${esc(d.speech || "...")}</div>
      ${foot.length ? `<div class="msg-foot">${foot.join("")}</div>` : ""}
      ${d.thought ? `<div class="thought"${S.hood ? "" : " hidden"}>💭 ${esc(d.thought)}</div>` : ""}
      ${trace}
    </div>`, "msg", { instant, latest: true });
  const btn = $(".think-btn", el);
  if (btn) btn.onclick = () => { const t = $(".thought", el); t.hidden = !t.hidden; btn.setAttribute("aria-expanded", String(!t.hidden)); };
  spotlight(d.advisor, addressed);
  if (d.runoff_vote) addRunoffVote(d.advisor, d.runoff_vote, instant);
}

function renderPoll(d, instant) {
  const rows = (d.rows || []).slice();
  rows.forEach(r => { if (r.id && r.author) S.plans[r.id] = r.author; });
  rows.sort((x, y) => (y.accept?.length || 0) - (x.accept?.length || 0) || (x.veto?.length || 0) - (y.veto?.length || 0));
  const lead = rows[0];
  // seat stances on the leading plan
  if (lead) {
    S.stance = {};
    for (const a of S.advisors) S.stance[a.id] = lead.veto?.includes(a.id) ? "redline" : lead.accept?.includes(a.id) ? "agree" : "disagree";
    renderStances();
    const n = lead.accept?.length || 0;
    const vetoNote = lead.veto?.length ? ` <span class="vote-chip veto">red line for ${esc(nameList(lead.veto))}</span>` : "";
    S.leadHtml = `${voteDots(lead.accept, lead.veto)}<span><b>${n} of ${N()}</b> agree with ${esc(planName(lead.id, lead.author))}</span>${vetoNote}`;
    setPhase($("#phase").textContent, S.leadHtml);
  }
  const top = rows.slice(0, 3);
  if (!top.length) return;
  const body = top.map((r, i) => {
    const au = planAuthor(r.id, r.author);
    return `<div class="poll-row${i === 0 ? " lead" : ""}">
      <div class="poll-plan">${avatar(au, "mini")}<span class="who">${esc(planName(r.id, au))}</span><span class="what">${esc(r.plain || "")}</span></div>
      ${voteDots(r.accept, r.veto)}
      <span class="poll-count">${r.accept?.length || 0} of ${N()} agree</span></div>`;
  }).join("");
  const label = d.phase === "runoff" ? "Runoff count" : `Where the votes stand after round ${dispRound(d.year, d.round)}`;
  append(`<div class="poll-title">${esc(label)}</div>${body}`, "poll", { instant });
}

const OUTCOME = {
  consensus: ["🤝", "Decided together"],
  majority: ["🗳️", "Majority decision"],
  runoff: ["⚖️", "Decided by runoff vote"],
  escalated: ["👑", "You decided"],
};
function outcomeBadge(d) {
  const [ic, label] = OUTCOME[d.outcome] || ["📜", cap(d.outcome || "Decided")];
  const dissent = (d.dissent || []).filter(Boolean);
  const extra = d.outcome === "majority" && dissent.length ? ` (${nameList(dissent)} disagreed)` : "";
  return `<span class="outcome o-${esc(d.outcome)}"><span class="emoji">${ic}</span>${esc(label + extra)}</span>`;
}

function renderYear_(d, instant) {
  const r = d.report || d.state?.last_report || {};
  const author = d.adopted === "safe" ? null : planAuthor(d.adopted);
  const plain = stripSafe(d.plain || planText(d.plan));
  const pname = d.adopted === "safe" ? "The safe plan" : author ? `${adv(author).short}'s plan` : "The plan";
  const summary = d.summary || `The council adopted ${pname.toLowerCase().replace(/^the /, "the ")}.`;
  const nums = [
    ["People now", d.state?.population],
    ["Harvest", r.harvest, "bushels"],
    ["Starved", r.starved ?? starvedOf(d.state)],
    ["Plague deaths", r.plague_deaths || null],
    ["Rats ate", r.rats, "bushels"],
    ["Newcomers", r.immigrants],
  ].filter(x => x[1] != null).slice(0, 5);
  const sat = d.satisfaction && Object.keys(d.satisfaction).length
    ? `<div class="hood-only trace">How satisfied each advisor is (0 to 1):\n${Object.entries(d.satisfaction).map(([k, v]) => `  ${adv(k).short}: ${typeof v === "number" ? v.toFixed(2) : esc(v)}`).join("\n")}\nRounds this year: ${esc(d.rounds ?? "-")}</div>` : "";
  append(`<div class="card-kicker"><span>Year ${esc(d.year)} decision</span>${outcomeBadge(d)}</div>
    <div class="card-summary">${esc(summary)}</div>
    ${plain || author ? `<div class="card-plan"><span class="plan-chip">${author ? avatar(author, "mini") : `<span class="emoji">🛟</span>`}<span><b>${esc(pname)}:</b> ${esc(plain)}</span></span></div>` : ""}
    <div class="nums">${nums.map(([k, v, u]) => `<div class="num"><div class="k">${esc(k)}</div><div class="v">${fmt(v)}</div>${u ? `<div class="k">${u}</div>` : ""}</div>`).join("")}</div>
    ${sat}`, `card k-${esc(d.outcome)}`, { instant });
}

function renderEnd(d, instant) {
  const r = d.rating || {};
  const V = {
    Jefferson: ["A golden reign", "🏆"],
    fair: ["A fair reign", "🌤️"],
    "Henry VIII": ["A harsh reign", "⚠️"],
    Nero: ["A disastrous reign", "🔥"],
  };
  const [title, ic] = r.impeached ? ["The people overthrew the council", "🔥"] : V[r.verdict] || [r.verdict ? `Verdict: ${r.verdict}` : "The reign is over", "🏛️"];
  const oc = d.outcomes || {};
  const words = { consensus: "decided together", majority: "by majority", runoff: "by runoff", escalated: "by you" };
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

function renderNews(d, instant) {
  const mine = d.injected;
  append(`<span class="emoji">📯</span><div><b>${mine ? "Your scout reports" : "Scout report"}</b>${esc(d.text || d.kind)}</div>`, `note${mine ? " you" : ""}`, { instant });
}

/* ---------------- runoff ---------------- */
function showRunoff(d, instant) {
  const a = d.a || {}, b = d.b || {};
  if (a.id && a.author) S.plans[a.id] = a.author;
  if (b.id && b.author) S.plans[b.id] = b.author;
  S.runoff = { a, b, votes: {}, decided: false };
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
  setPhase("Runoff vote", `<span>${esc(planName(a.id, a.author))} <b>vs</b> ${esc(planName(b.id, b.author))}</span>${d.reason ? `<span class="hood-only tag">${esc(d.reason)}</span>` : ""}`);
  append(`<span class="emoji">⚖️</span><div><b>Runoff</b>No plan won a majority. Each advisor now votes for ${esc(planName(a.id, a.author))} or ${esc(planName(b.id, b.author))}.</div>`, "note", { instant });
}
function addRunoffVote(advisor, pid, instant) {
  const R = S.runoff;
  if (!R || R.votes[advisor]) return;
  const k = pid === R.b.id ? "b" : pid === R.a.id ? "a" : null;
  if (!k) return;
  R.votes[advisor] = pid;
  const side = $(`#ro-${k}`);
  if (side) {
    const a = adv(advisor);
    $(".ro-votes", side).insertAdjacentHTML("beforeend", `<span class="ro-vote" style="${instant ? "animation:none" : ""}" title="${esc(a.short)}">${a.emoji}</span>`);
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
  if (!opts.some(o => o.id === "safe")) opts.push({ id: "safe", plain: "Play it safe: feed everyone and plant as much as we can", accept: [], veto: [], note: "Agreed in advance as the fallback" });
  $("#escReason").textContent = d.reason || "No plan has enough support, and some plans cross an advisor's red line.";
  $("#escOptions").innerHTML = opts.map(o => {
    const safe = o.id === "safe";
    const au = safe ? null : planAuthor(o.id, o.author);
    const why = [];
    if (!safe) why.push(`${voteDots(o.accept, o.veto)} <span>${(o.accept || []).length} of ${N()} agree</span>`);
    if (o.veto?.length) why.push(`<span class="vote-chip veto">Red line for ${esc(nameList(o.veto))}</span>`);
    if (o.note) why.push(`<span>${esc(o.note)}</span>`);
    return `<button class="esc-opt${safe ? " safe" : ""}" type="button" data-choice="${esc(o.id)}">
      ${safe ? `<span class="av" style="--c:var(--ok)">🛟</span>` : avatar(au)}
      <span><span class="who">${esc(safe ? "Play it safe" : planName(o.id, au))}</span><br><span class="what">${esc(stripSafe(o.plain || planText(o.plan)))}</span></span>
      ${why.length ? `<span class="why">${why.join("")}</span>` : ""}</button>`;
  }).join("");
  $("#escPositions").innerHTML = (d.positions || []).map(p => {
    const id = p.advisor || p.id;
    const vetoes = (p.veto || []).map(v => planName(v));
    return `<div class="pos">${avatar(id)}<div><b>${esc(adv(id).short)}</b>${vetoes.length ? ` <span class="vote-chip veto">red line: ${esc(vetoes.join(", "))}</span>` : ""}<br><i>${esc(p.speech || p.thought || p.position || "")}</i>${p.thought && p.speech ? `<div class="hood-only thought">💭 ${esc(p.thought)}</div>` : ""}</div></div>`;
  }).join("") || "<i>No positions reported.</i>";
  $(".esc-positions").classList.toggle("hidden", !(d.positions || []).length);
  $$("#escOptions .esc-opt").forEach(btn => btn.onclick = () => choose(btn));
  $("#escalation").classList.remove("hidden");
  setPhase("Your call", `<span>The council is stuck. <b>You decide.</b></span>`);
  spotlight(null);
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
  if ($("#phase").textContent === "Your call") setPhase("Decision", `<span>You chose <b>${esc(label)}</b></span>`);
  S.waitingHuman = false;
  idleTyping();
}
function closeEscalation() { $("#escalation").classList.add("hidden"); S.waitingHuman = false; }

/* ---------------- city stats + charts ---------------- */
const STATS = [
  { key: "population", label: "People", color: "#1f4e8c", dir: 1, get: s => s.population },
  { key: "grain", label: "Grain", unit: "bushels", color: "#9b7015", dir: 1, get: s => s.grain },
  { key: "acres", label: "Land", unit: "acres", color: "#4c7a2e", dir: 1, get: s => s.acres },
  { key: "land_price", label: "Land price", unit: "bushels per acre", color: "#b0502a", dir: 0, get: s => s.land_price },
  { key: "starved", label: "Starved last year", color: "#a3302a", dir: -1, get: starvedOf },
];
function buildStats() {
  $("#stats").innerHTML = STATS.map(st => `<div class="stat" id="stat-${st.key}" style="--sc:${st.color}" aria-expanded="false">
    <button class="stat-btn" type="button" aria-controls="chart-${st.key}">
      <span class="stat-top"><span class="stat-k">${esc(st.label)}</span><span class="chev" aria-hidden="true">▾</span></span>
      <span class="stat-main"><span class="stat-v">-</span>${st.unit ? `<span class="stat-unit">${esc(st.unit)}</span>` : ""}<span class="delta"></span></span>
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
  $("#cityWhen").textContent = last <= 0 ? "at the start" : `after year ${last}`;
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
      d.textContent = diff === 0 ? "= same" : `${diff > 0 ? "▲" : "▼"} ${fmt(Math.abs(diff))}`;
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
    el.setAttribute("aria-expanded", String(open));
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
let narrating = false, muted = false, unlocked = false;
try { muted = localStorage.getItem("sumer-muted") === "1"; } catch { /* storage blocked */ }
const SILENT = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
function unlockAudio() {
  if (unlocked || narrating) return;
  unlocked = true;
  narrator.src = SILENT;
  narrator.play().then(() => narrator.pause()).catch(() => { unlocked = false; });
}
function narrate(url) { if (!url || muted) return; N_Q.push(url); playNext(); }
function playNext() {
  if (narrating || muted) return;
  const url = N_Q.shift();
  if (!url) return;
  narrating = true;
  narrator.src = url;
  narrator.play().catch(() => { narrating = false; playNext(); });
}
narrator.addEventListener("ended", () => { narrating = false; playNext(); });
narrator.addEventListener("error", () => { if (narrating) { narrating = false; playNext(); } });
function stopNarration() { N_Q.length = 0; narrator.pause(); narrating = false; }
function renderMute() {
  const b = $("#muteBtn");
  b.textContent = muted ? "🔇" : "🔊";
  b.setAttribute("aria-pressed", String(muted));
  b.title = b.ariaLabel = muted ? "Unmute the narrator" : "Mute the narrator";
}

/* ---------------- event queue ---------------- */
const PACED = new Set(["turn", "news", "tally", "runoff", "runoff_result", "year", "escalate", "end"]);
const Q = [];
let pumping = false, lastShown = 0, holdUntil = 0, gen = 0, wake = null;
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
  if (ev.kind === "game") { Q.length = 0; gen++; wake?.(); }
  Q.push(ev);
  pump();
}
async function pump() {
  if (pumping) return;
  pumping = true;
  try {
    while (Q.length) {
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
      $("#seats").classList.add("active");
      spotlight(d.advisor, (d.addressed || []).filter(x => x !== d.advisor));
      await sleepUntil(Math.max(lastShown + MIN_GAP, holdUntil, now() + MIN_TYPING));
    } else {
      if (kind === "escalate" || kind === "year" || kind === "end") typing("");
      await sleepUntil(Math.max(lastShown + MIN_GAP, holdUntil));
    }
  } else if (!instant && (kind === "year_start" || kind === "round")) {
    await sleepUntil(holdUntil);
  }
  if (g !== gen && kind !== "game") return;
  render(kind, d, instant);
  if (PACED.has(kind) && !instant) lastShown = now();
  if (kind === "year" && !instant) holdUntil = now() + HOLD_AFTER_YEAR;
  idleTyping();
}

function resetGame(d) {
  stopNarration();
  closeEscalation();
  hideRunoff();
  Object.assign(S, { leadHtml: "", year: 0, completed: 0, hist: [], roundBase: {}, plans: {}, stance: {}, running: true, waitingHuman: false });
  S.years = d.years || S.meta?.years || 5;
  if (d.advisors) setAdvisors(d.advisors);
  else renderStances();
  lastShown = 0; holdUntil = 0;
  feed().innerHTML = "";
  spotlight(null);
  recordState(0, d.state);
  renderYear();
}

function render(kind, d, instant) {
  switch (kind) {
    case "game":
      resetGame(d);
      setPhase("The council gathers", `<span>${N()} advisors, ${S.years} years, no king to break ties.</span>`);
      append(`<span class="emoji">🏛️</span><div><b>The council convenes</b>${N()} advisors will run the city for ${S.years} years. Each has a different goal. They must agree, or ask you.</div>`, "note", { instant });
      break;
    case "year_start":
      S.year = d.year; S.stance = {}; S.leadHtml = ""; renderStances(); hideRunoff();
      if (d.state) recordState(d.year - 1, d.state);
      renderYear();
      setPhase(`Year ${d.year} begins`, `<span>The council is discussing</span>`);
      { const s = d.state || S.hist[d.year - 1] || {};
        append(`Year ${esc(d.year)}<small>${fmt(s.population)} people · ${fmt(s.grain)} bushels · ${fmt(s.acres)} acres</small>`, "divider year", { instant }); }
      break;
    case "round": {
      if (d.year && d.year !== S.year) { S.year = d.year; renderYear(); }
      if (d.phase === "runoff") { setPhase("Runoff vote", `<span>The top two plans go head to head</span>`); break; }
      const r = dispRound(d.year, d.round);
      const max = d.max_rounds || S.meta?.max_rounds;
      setPhase(`Discussing - round ${r}`, S.leadHtml || `<span>The council is discussing${max ? ` · up to ${max} rounds to agree` : ""}</span>`);
      append(`Round ${r}${max ? ` of ${max}` : ""}`, "divider", { instant });
      break;
    }
    case "turn": renderTurn(d, instant); break;
    case "tally": renderPoll(d, instant); break;
    case "news": renderNews(d, instant); break;
    case "wire":
      append(`📡 ${esc(d.transport || "wire")}${d.topic ? ` · ${esc(d.topic)}` : ""}${d.recipients ? ` · to ${d.recipients.length} agents` : ""}${d.bytes_out ? ` · ${(d.bytes_out / 1024).toFixed(1)} kB` : ""}${d.replies != null ? ` · ${d.replies} replies` : ""}${d.elapsed_s != null ? ` in ${d.elapsed_s}s` : ""}`, "wire hood-only", { instant: true });
      break;
    case "runoff": showRunoff(d, instant); break;
    case "runoff_result": runoffResult(d, instant); break;
    case "escalate": showEscalation(d); break;
    case "escalation_timeout":
      closeEscalation();
      append(`<span class="emoji">🛟</span><div><b>Selection timed out</b>The council remains paused until a valid choice is made.</div>`, "note you", { instant });
      break;
    case "year":
      closeEscalation();
      S.year = d.year; S.completed = Math.max(S.completed, d.year);
      if (d.state) recordState(d.year, d.state);
      renderYear();
      spotlight(null);
      setPhase("Decision", outcomeBadge(d));
      renderYear_(d, instant);
      if (!instant) narrate(d.narration_url);
      hideRunoff();
      break;
    case "end":
      S.running = false; S.completed = d.years?.length || 0; renderYear();
      closeEscalation(); spotlight(null);
      if (d.status && !["completed", "overthrown", "depopulated"].includes(d.status)) {
        setPhase(cap(d.status.replaceAll("_", " ")), "<span>The run retains its recorded city and decisions.</span>");
        append(`<div class="card-kicker">${esc(d.status.replaceAll("_", " "))}</div>`, "card", { instant });
        break;
      }
      setPhase("Reign complete", `<span>${esc(d.rating?.verdict_reason ? cap(d.rating.verdict_reason) : "The council has finished its rule.")}</span>`);
      renderEnd(d, instant);
      break;
    case "technical_failure":
    case "stopped":
    case "error":
      append(`<span class="emoji">⚠️</span><div><b>Something went wrong</b>${esc(d.message)}</div>`, "note error", { instant });
      if (!instant) toast(d.message);
      break;
    default: break;
  }
}

/* ---------------- SSE ---------------- */
let es;
function armReplay() {
  clearTimeout(replayQuiet);
  replayQuiet = setTimeout(() => { replay = false; }, REPLAY_QUIET);
}
function connect(id) {
  if (!id) return;
  es?.close();
  if (S.runId !== id) seen.clear();
  S.runId = id;
  es = new EventSource(`/api/events?run_id=${encodeURIComponent(id)}`);
  replay = true;
  armReplay();
  clearTimeout(replayCapT);
  replayCapT = setTimeout(() => { replay = false; }, REPLAY_CAP);
  es.onmessage = m => {
    if (replay) armReplay();
    try { const ev = JSON.parse(m.data); onEvent(ev); if (ev.kind === "end") es.close(); } catch (e) { console.error("bad event", e); }
  };
  es.onerror = () => { es.close(); setTimeout(() => connect(id), 1500); };
}

/* ---------------- controls ---------------- */
function openDrawer(open) {
  $("#drawer").classList.toggle("open", open);
  $("#drawer").setAttribute("aria-hidden", String(!open));
  $("#drawerBack").classList.toggle("hidden", !open);
  $("#setupBtn").setAttribute("aria-expanded", String(open));
  if (open) $("#seed").focus({ preventScroll: true });
}
async function start() {
  unlockAudio();
  openDrawer(false);
  const body = { seed: +$("#seed").value || 0, reveal_news: $("#reveal").value === "1" };
  const rounds = +$("#rounds").value;
  if (rounds) body.max_rounds = rounds;
  try { const result = await api("/api/game", body); connect(result.run_id); } catch (e) { toast(`Could not start: ${e.message}`); }
}
function setHood(on) {
  S.hood = on;
  document.body.classList.toggle("hood", on);
  $("#hoodToggle").setAttribute("aria-pressed", String(on));
  $$(".msg").forEach(m => {
    const t = $(".thought", m), b = $(".think-btn", m);
    if (t) t.hidden = !on;
    if (b) b.setAttribute("aria-expanded", String(on));
  });
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
  $("#setupBtn").onclick = () => openDrawer(!$("#drawer").classList.contains("open"));
  $("#drawerClose").onclick = () => openDrawer(false);
  $("#drawerBack").onclick = () => openDrawer(false);
  $("#startBtn").onclick = start;
  $("#stopBtn").onclick = () => api("/api/stop", {}).then(() => { S.running = false; idleTyping(); toast("Stopped.", true); }).catch(e => toast(e.message));
  $("#hoodToggle").onclick = () => setHood(!S.hood);
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
  for (const t of ["wheel", "touchmove", "keydown"]) feed().addEventListener(t, () => { userScroll = true; }, { passive: true });
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
  $$("[data-news]").forEach(b => b.onclick = async () => {
    b.disabled = true;
    try { await api("/api/news", { run_id: S.runId, kind: b.dataset.news }); toast("Scout sent. The report reaches the council next round.", true); }
    catch (e) { toast(`Scout could not leave: ${e.message}`); }
    finally { setTimeout(() => { b.disabled = false; }, 1500); }
  });
  addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    openDrawer(false);
    $("#evidence").classList.add("hidden");
  });
  // Browsers block audio until a user gesture: any click unlocks the narrator.
  addEventListener("pointerdown", unlockAudio, { once: true, capture: true });
}

(async function init() {
  wire();
  buildStats();
  renderMute();
  renderYear();
  welcome();
  try { if (localStorage.getItem("sumer-hood") === "1") setHood(true); } catch { /* storage blocked */ }
  try {
    S.meta = await api("/api/meta");
  } catch (e) {
    S.meta = {};
    toast(`Server not reachable: ${e.message}`);
  }
  const m = S.meta;
  setAdvisors(m.advisors);
  if (!S.advisors.length) setAdvisors(Object.keys(FALLBACK_EMOJI).map(id => ({ id })));
  $("#chipTransport").textContent = m.transport === "slim" ? "AGNTCY SLIM" : m.transport ? String(m.transport) : "-";
  $("#chipLlm").textContent = m.llm === "bayleaf" || m.llm === "llm" ? String(m.model || "LLM").split("/").pop() : m.llm ? "offline policy" : "-";
  if (m.max_rounds) {
    const sel = $("#rounds");
    if (![...sel.options].some(o => +o.value === +m.max_rounds)) sel.insertAdjacentHTML("beforeend", `<option>${m.max_rounds}</option>`);
    sel.value = String(m.max_rounds);
  }
  if (S.meta?.active_run) connect(S.meta.active_run);
})();
