"use strict";

/* ── Utility ─────────────────────────────────────────────────────────────── */

function esc(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function pct(v, decimals = 1) {
  if (v == null) return "–";
  return (v * 100).toFixed(decimals) + "%";
}

function dec(v, d = 2) {
  if (v == null || v === 0) return "–";
  return Number(v).toFixed(d);
}

function money(v) {
  if (v == null) return "–";
  return "$" + Number(v).toLocaleString(undefined, {maximumFractionDigits: 0});
}

function relTime(isoStr) {
  if (!isoStr) return "–";
  const diff = Math.floor((Date.now() - new Date(isoStr + "Z").getTime()) / 1000);
  if (diff < 0)    return "in the future";
  if (diff < 60)   return diff + "s ago";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400)return Math.floor(diff / 3600) + "h ago";
  return Math.floor(diff / 86400) + "d ago";
}

function shortDate(isoStr) {
  if (!isoStr) return "";
  try { return new Date(isoStr).toLocaleDateString(); } catch { return ""; }
}

async function apiFetch(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

/* ── Tab switching ──────────────────────────────────────────────────────── */

const panes = {
  arb:      document.getElementById("tab-arb"),
  markets:  document.getElementById("tab-markets"),
  settings: document.getElementById("tab-settings"),
};

document.querySelectorAll(".nav-tab, .tab-link").forEach(btn => {
  btn.addEventListener("click", e => {
    e.preventDefault();
    const tab = btn.dataset.tab;
    if (!panes[tab]) return;
    Object.values(panes).forEach(p => p.classList.remove("active"));
    document.querySelectorAll(".nav-tab").forEach(b => b.classList.remove("active"));
    panes[tab].classList.add("active");
    document.querySelector(`.nav-tab[data-tab="${tab}"]`)?.classList.add("active");
    if (tab === "arb")      loadArb();
    if (tab === "markets")  loadMarkets();
    if (tab === "settings") loadConfig();
  });
});

/* ── Stats bar ──────────────────────────────────────────────────────────── */

async function loadStats() {
  try {
    const s = await apiFetch("/api/stats");
    document.getElementById("stat-markets").textContent  = s.pm_markets ?? "–";
    document.getElementById("stat-events").textContent   = s.odds_events ?? "–";
    document.getElementById("stat-matched").textContent  = s.matched_markets ?? "–";
    document.getElementById("stat-arbs").textContent     = s.arb_opportunities ?? "–";
    document.getElementById("last-fetch").textContent    =
      s.last_fetch ? "Updated " + relTime(s.last_fetch) : "Never fetched";
    const apiEl = document.getElementById("stat-api");
    if (s.api_remaining != null) {
      apiEl.textContent = s.api_remaining + " API req left";
      apiEl.style.color = s.api_remaining < 50 ? "var(--red)" : "var(--text-dim)";
    }
  } catch {}
}

/* ── Arb table ──────────────────────────────────────────────────────────── */

let currentArb = [];

async function loadArb() {
  const body = document.getElementById("arb-body");
  body.innerHTML = `<tr><td colspan="11" class="loading"><span class="spinner"></span>Loading…</td></tr>`;

  const edge     = parseFloat(document.getElementById("f-edge").value) || 0;
  const sport    = document.getElementById("f-sport").value.trim();
  const bk       = document.getElementById("f-bk").value.trim();
  const arbOnly  = document.getElementById("f-arb-only").checked;

  let url = `/api/arb?min_edge=${edge}&limit=200`;
  if (sport)   url += `&sport=${encodeURIComponent(sport)}`;
  if (bk)      url += `&bookmaker=${encodeURIComponent(bk)}`;
  if (arbOnly) url += `&only_arb=true`;

  try {
    const rows = await apiFetch(url);
    currentArb = rows;
    renderArbTable(rows);
    loadStats();

    const banner = document.getElementById("no-key-banner");
    banner.classList.toggle("hidden", rows.length > 0 || !checkNoKey());
  } catch (e) {
    body.innerHTML = `<tr><td colspan="11" class="empty">Error: ${esc(e.message)}</td></tr>`;
  }
}

async function checkNoKey() {
  try {
    const cfg = await apiFetch("/api/config");
    return !cfg.odds_api_key_set;
  } catch { return false; }
}

function edgeClass(pct) {
  const v = Math.abs(pct);
  if (v >= 5)  return "edge-high";
  if (v >= 2)  return "edge-mid";
  return "edge-low";
}

function confDot(score) {
  const cls = score >= 0.65 ? "high" : score >= 0.45 ? "mid" : "low";
  return `<span class="conf ${cls}" title="Match confidence: ${(score*100).toFixed(0)}%"></span>`;
}

function renderArbTable(rows) {
  const body = document.getElementById("arb-body");
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="11" class="empty">
      No opportunities found. Try lowering the minimum edge or run a fetch.
    </td></tr>`;
    return;
  }

  body.innerHTML = rows.map((r, i) => {
    const isArb      = !!r.is_arb;
    const yesEdgePct = ((r.book_yes_implied - r.pm_yes_price) * 100).toFixed(1);
    const noEdgePct  = ((r.book_no_implied  - r.pm_no_price)  * 100).toFixed(1);
    const maxEdgePct = (r.max_edge * 100).toFixed(1);
    const edgeCls    = edgeClass(parseFloat(maxEdgePct));

    const pmYesPct   = (r.pm_yes_price * 100).toFixed(1);
    const pmNoPct    = (r.pm_no_price  * 100).toFixed(1);
    const bkYesPct   = (r.book_yes_implied * 100).toFixed(1);
    const bkNoPct    = (r.book_no_implied  * 100).toFixed(1);

    const pmUrl  = `https://polymarket.com/event/${esc(r.slug || "")}`;
    const orient = r.pm_yes_is_home
      ? `YES=${esc(r.home_team)}, NO=${esc(r.away_team)}`
      : `YES=${esc(r.away_team)}, NO=${esc(r.home_team)}`;

    const arbBadge = isArb
      ? `<span class="arb-badge true">✓ ${r.arb_return_pct.toFixed(2)}%</span>`
      : `<span class="arb-badge soft">edge</span>`;

    const manualTag = r.is_manual ? `<span class="tag" style="margin-left:4px">manual</span>` : "";

    return `<tr class="${isArb ? "is-arb" : ""}" data-idx="${i}">
      <td class="ev-cell">
        <div class="ev-teams" title="${esc(r.home_team)} vs ${esc(r.away_team)}">
          ${esc(r.home_team)} <span style="color:var(--text-dim)">vs</span> ${esc(r.away_team)}
        </div>
        <div class="ev-meta">${esc(r.bookmaker)} · ${esc(r.competition)} · ${shortDate(r.commence_time)}</div>
        <div class="ev-meta" style="color:var(--text-dim);font-size:.68rem">${orient}${manualTag}</div>
      </td>
      <td>
        <div class="q-text">
          <a class="q-link" href="${pmUrl}" target="_blank" title="${esc(r.question)}">${esc(r.question)}</a>
        </div>
        <div class="ev-meta">${money(r.volume)} vol · ends ${shortDate(r.end_date)}</div>
      </td>
      <td class="n pm-pct">${pmYesPct}%</td>
      <td class="n book-pct">${bkYesPct}%
        <div style="font-size:.68rem;color:var(--text-dim)">${dec(r.book_yes_decimal)}×</div>
      </td>
      <td class="n pm-pct">${pmNoPct}%</td>
      <td class="n book-pct">${bkNoPct}%
        <div style="font-size:.68rem;color:var(--text-dim)">${dec(r.book_no_decimal)}×</div>
      </td>
      <td class="n"><span class="${edgeCls}">${maxEdgePct}%</span></td>
      <td class="n">${arbBadge}</td>
      <td><span class="strategy">${esc(r.best_strategy)}</span></td>
      <td class="n">${confDot(r.match_score || 0)}${((r.match_score || 0)*100).toFixed(0)}%</td>
      <td><button class="btn-calc" data-idx="${i}">Calc</button></td>
    </tr>`;
  }).join("");

  // Calc button listeners
  body.querySelectorAll(".btn-calc").forEach(btn => {
    btn.addEventListener("click", () => openCalc(parseInt(btn.dataset.idx)));
  });
}

/* ── Bet calculator ─────────────────────────────────────────────────────── */

function openCalc(idx) {
  const r = currentArb[idx];
  if (!r) return;

  document.getElementById("calc-panel").classList.remove("hidden");
  document.getElementById("calc-title").textContent = "Bet Calculator";
  document.getElementById("calc-event").textContent =
    `${r.home_team} vs ${r.away_team} (${r.bookmaker})\n${r.question}`;

  function render() {
    const stake = parseFloat(document.getElementById("calc-stake").value) || 1000;
    renderCalcResults(r, stake);
  }
  document.getElementById("calc-stake").oninput = render;
  render();
}

function renderCalcResults(r, stake) {
  const out = document.getElementById("calc-results");

  // Strategy A: PM YES + Book NO
  const cost_a   = r.pm_yes_price + r.book_no_implied;
  const is_a     = cost_a < 1.0;
  const ret_a    = is_a ? (1 / cost_a - 1) * 100 : 0;
  const pm_a     = (r.book_no_implied / cost_a) * stake;
  const book_a   = (r.pm_yes_price    / cost_a) * stake;
  const profit_a = is_a ? stake * (1 / cost_a - 1) : 0;

  // Strategy B: PM NO + Book YES
  const cost_b   = r.pm_no_price + r.book_yes_implied;
  const is_b     = cost_b < 1.0;
  const ret_b    = is_b ? (1 / cost_b - 1) * 100 : 0;
  const pm_b     = (r.book_yes_implied / cost_b) * stake;
  const book_b   = (r.pm_no_price      / cost_b) * stake;
  const profit_b = is_b ? stake * (1 / cost_b - 1) : 0;

  const yesEdge = ((r.book_yes_implied - r.pm_yes_price) * 100).toFixed(1);
  const noEdge  = ((r.book_no_implied  - r.pm_no_price)  * 100).toFixed(1);

  out.innerHTML = `
    <div class="calc-row">
      <h4>Price snapshot</h4>
      <div class="calc-line"><span class="lbl">PM YES price</span>
        <span class="val">${(r.pm_yes_price*100).toFixed(1)}¢</span></div>
      <div class="calc-line"><span class="lbl">Book YES implied</span>
        <span class="val">${(r.book_yes_implied*100).toFixed(1)}% (${dec(r.book_yes_decimal)}×)</span></div>
      <div class="calc-line"><span class="lbl">YES edge (book−PM)</span>
        <span class="val ${parseFloat(yesEdge)>0?'green':''}">${yesEdge}%</span></div>
      <div class="calc-line"><span class="lbl">PM NO price</span>
        <span class="val">${(r.pm_no_price*100).toFixed(1)}¢</span></div>
      <div class="calc-line"><span class="lbl">Book NO implied</span>
        <span class="val">${(r.book_no_implied*100).toFixed(1)}% (${dec(r.book_no_decimal)}×)</span></div>
      <div class="calc-line"><span class="lbl">NO edge (book−PM)</span>
        <span class="val ${parseFloat(noEdge)>0?'green':''}">${noEdge}%</span></div>
    </div>

    <div class="calc-row">
      <h4>Strategy A — BUY PM YES + BET Book NO&nbsp;${is_a?"✓":""}</h4>
      <div class="calc-line"><span class="lbl">Total implied cost</span>
        <span class="val ${is_a?'green':'yellow'}">${(cost_a*100).toFixed(2)}%</span></div>
      <div class="calc-line"><span class="lbl">Stake on PM YES</span>
        <span class="val">$${pm_a.toFixed(2)}</span></div>
      <div class="calc-line"><span class="lbl">Stake on Book NO</span>
        <span class="val">$${book_a.toFixed(2)}</span></div>
      <div class="calc-line"><span class="lbl">Guaranteed profit</span>
        <span class="val ${is_a?'green':''}">
          ${is_a ? "$"+profit_a.toFixed(2)+" ("+ret_a.toFixed(2)+"%)" : "Not a true arb"}
        </span></div>
    </div>

    <div class="calc-row">
      <h4>Strategy B — BUY PM NO + BET Book YES&nbsp;${is_b?"✓":""}</h4>
      <div class="calc-line"><span class="lbl">Total implied cost</span>
        <span class="val ${is_b?'green':'yellow'}">${(cost_b*100).toFixed(2)}%</span></div>
      <div class="calc-line"><span class="lbl">Stake on PM NO</span>
        <span class="val">$${pm_b.toFixed(2)}</span></div>
      <div class="calc-line"><span class="lbl">Stake on Book YES</span>
        <span class="val">$${book_b.toFixed(2)}</span></div>
      <div class="calc-line"><span class="lbl">Guaranteed profit</span>
        <span class="val ${is_b?'green':''}">
          ${is_b ? "$"+profit_b.toFixed(2)+" ("+ret_b.toFixed(2)+"%)" : "Not a true arb"}
        </span></div>
    </div>
  `;
}

document.getElementById("calc-close").addEventListener("click", () => {
  document.getElementById("calc-panel").classList.add("hidden");
});

/* ── Markets tab ────────────────────────────────────────────────────────── */

async function loadMarkets() {
  const body = document.getElementById("markets-body");
  body.innerHTML = `<tr><td colspan="8" class="loading"><span class="spinner"></span>Loading…</td></tr>`;

  const q           = document.getElementById("m-search").value.trim();
  const matchedOnly = document.getElementById("m-matched-only").checked;

  let url = `/api/markets?limit=200`;
  if (q)           url += `&q=${encodeURIComponent(q)}`;
  if (matchedOnly) url += `&matched_only=true`;

  try {
    const rows = await apiFetch(url);
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="8" class="empty">No markets found.</td></tr>`;
      return;
    }

    body.innerHTML = rows.map(r => {
      const matched = !!r.event_id;
      const pmUrl   = `https://polymarket.com/event/${esc(r.slug || "")}`;
      const ev = matched
        ? `<span class="ev-teams">${esc(r.home_team)} vs ${esc(r.away_team)}</span>
           <div class="ev-meta">${esc(r.bookmaker)} · ${esc(r.competition)}</div>`
        : `<span style="color:var(--text-dim);font-size:.78rem">No match</span>`;

      const conf = matched
        ? `${confDot(r.match_score || 0)}${((r.match_score||0)*100).toFixed(0)}%
           ${r.is_manual ? '<span class="tag">manual</span>' : ""}`
        : "–";

      return `<tr>
        <td class="q-text" style="max-width:320px">
          <a class="q-link" href="${pmUrl}" target="_blank" title="${esc(r.question)}">${esc(r.question)}</a>
        </td>
        <td class="n pm-pct">${(r.yes_price*100).toFixed(1)}%</td>
        <td class="n pm-pct">${(r.no_price*100).toFixed(1)}%</td>
        <td class="n">${money(r.volume)}</td>
        <td>${ev}</td>
        <td class="n">${conf}</td>
        <td><span class="tag">${esc(r.category || "–")}</span></td>
        <td>${matched ? `<button class="btn-unmatch" data-id="${esc(r.id)}" title="Remove match">✕</button>` : ""}</td>
      </tr>`;
    }).join("");

    body.querySelectorAll(".btn-unmatch").forEach(btn => {
      btn.addEventListener("click", async () => {
        await apiFetch(`/api/markets/${btn.dataset.id}/unmatch`, { method: "POST" });
        loadMarkets();
      });
    });
  } catch (e) {
    body.innerHTML = `<tr><td colspan="8" class="empty">Error: ${esc(e.message)}</td></tr>`;
  }
}

/* ── Settings tab ───────────────────────────────────────────────────────── */

async function loadConfig() {
  try {
    const cfg = await apiFetch("/api/config");
    const keyInput = document.getElementById("cfg-key");
    const keyHint  = document.getElementById("cfg-key-hint");
    if (cfg.odds_api_key_set) {
      keyInput.placeholder = cfg.odds_api_key_masked || "••••••••";
      keyHint.textContent  = "Key is set (leave blank to keep current)";
    }
    document.getElementById("cfg-books").value    = cfg.bookmakers    || "pinnacle";
    document.getElementById("cfg-interval").value = cfg.fetch_interval || "10";
    document.getElementById("cfg-minvol").value   = cfg.min_volume     || "5000";
    document.getElementById("cfg-thresh").value   = cfg.match_threshold || "0.38";
  } catch {}
}

document.getElementById("btn-save").addEventListener("click", async () => {
  const msg = document.getElementById("save-msg");
  msg.className = "save-msg hidden";

  const body = {};
  const key  = document.getElementById("cfg-key").value.trim();
  if (key) body.odds_api_key = key;

  body.bookmakers      = document.getElementById("cfg-books").value.trim()    || "pinnacle";
  body.fetch_interval  = document.getElementById("cfg-interval").value.trim() || "10";
  body.min_volume      = document.getElementById("cfg-minvol").value.trim()   || "5000";
  body.match_threshold = document.getElementById("cfg-thresh").value.trim()   || "0.38";

  try {
    await apiFetch("/api/config", {
      method: "POST",
      body: JSON.stringify(body),
    });
    msg.textContent = "✓ Settings saved. Click 'Fetch now' to apply.";
    msg.className = "save-msg ok";
    document.getElementById("cfg-key").value = "";
    await loadConfig();
  } catch (e) {
    msg.textContent = "Error: " + e.message;
    msg.className = "save-msg err";
  }
  msg.classList.remove("hidden");
});

/* ── Fetch now button ───────────────────────────────────────────────────── */

async function triggerFetch(btn) {
  const orig = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>Running…`;
  try {
    await apiFetch("/api/fetch", { method: "POST" });
    // Poll stats until last_fetch changes
    let attempts = 0;
    const poll = setInterval(async () => {
      await loadStats();
      attempts++;
      if (attempts > 30) clearInterval(poll); // give up after 5 min
    }, 10_000);
    setTimeout(() => { loadArb(); clearInterval(poll); }, 15_000);
  } catch (e) {
    alert("Fetch error: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

document.getElementById("btn-fetch").addEventListener("click", function() {
  triggerFetch(this);
});

/* ── Apply filter buttons ────────────────────────────────────────────────── */

document.getElementById("btn-apply").addEventListener("click", loadArb);
document.getElementById("m-apply").addEventListener("click", loadMarkets);

// Also apply on Enter in filter inputs
["f-edge","f-sport","f-bk"].forEach(id => {
  document.getElementById(id)?.addEventListener("keydown", e => {
    if (e.key === "Enter") loadArb();
  });
});
document.getElementById("m-search")?.addEventListener("keydown", e => {
  if (e.key === "Enter") loadMarkets();
});

/* ── Auto-refresh ────────────────────────────────────────────────────────── */

setInterval(loadStats, 30_000);

/* ── Init ────────────────────────────────────────────────────────────────── */

(async () => {
  await loadStats();
  await loadArb();

  // Show no-key banner if needed
  try {
    const cfg = await apiFetch("/api/config");
    if (!cfg.odds_api_key_set) {
      document.getElementById("no-key-banner").classList.remove("hidden");
    }
  } catch {}
})();
