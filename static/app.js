"use strict";

/* ── Utilities ───────────────────────────────────────────────────────────── */

function esc(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function money(v) {
  if (v == null) return "–";
  return "$" + Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function usdc(v) {
  if (v == null) return "–";
  return "$" + Number(v).toFixed(2);
}

function relTime(iso) {
  if (!iso) return "";
  const s = Math.floor((Date.now() - new Date(iso + (iso.includes("Z") ? "" : "Z")).getTime()) / 1000);
  if (s < 0)     return "just now";
  if (s < 60)    return s + "s ago";
  if (s < 3600)  return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

function fmtDate(iso) {
  if (!iso) return "–";
  try {
    const d = new Date(iso.includes("Z") || iso.includes("+") ? iso : iso + "Z");
    return d.toLocaleString(undefined, {
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch (_) { return iso; }
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

function edgeColorClass(pct) {
  if (pct >= 5) return "edge-high";
  if (pct >= 2) return "edge-mid";
  return "edge-none";
}

function sideBadge(side) {
  if (!side) return `<span class="side-badge side-none">–</span>`;
  return `<span class="side-badge ${side === "YES" ? "side-yes" : "side-no"}">${esc(side)}</span>`;
}

function betStatusBadge(status) {
  if (!status) return `<span class="bet-none">–</span>`;
  if (status === "PLACED") return `<span class="bet-status bet-placed">PLACED</span>`;
  return `<span class="bet-status bet-failed">FAILED</span>`;
}

function confBadge(score) {
  if (score == null) return "–";
  const cls = score >= 0.8 ? "conf-high" : score >= 0.6 ? "conf-mid" : "conf-low";
  return `<span class="conf-dot ${cls}"></span>${(score * 100).toFixed(0)}%`;
}

/* ── Tab switching ───────────────────────────────────────────────────────── */

function switchTab(tab) {
  document.querySelectorAll(".nav-tab").forEach(b =>
    b.classList.toggle("active", b.dataset.tab === tab)
  );
  document.querySelectorAll(".tab-pane").forEach(p =>
    p.classList.toggle("active", p.id === "tab-" + tab)
  );
  if (tab === "edges")    loadEdges();
  if (tab === "bets")     loadBets();
  if (tab === "markets")  loadMarkets();
  if (tab === "settings") loadConfig();
}

document.querySelectorAll(".nav-tab").forEach(btn =>
  btn.addEventListener("click", () => switchTab(btn.dataset.tab))
);
document.querySelectorAll(".tab-link[data-tab]").forEach(a =>
  a.addEventListener("click", e => { e.preventDefault(); switchTab(a.dataset.tab); })
);

/* ── Stats bar ───────────────────────────────────────────────────────────── */

async function refreshStats() {
  try {
    const s = await api("/api/stats");
    const el = id => document.getElementById(id);
    el("st-pm").textContent    = s.pm_markets     ?? "–";
    el("st-ev").textContent    = s.odds_events     ?? "–";
    el("st-match").textContent = s.matched_markets ?? "–";
    el("st-edge").textContent  = s.edges_found     ?? "–";
    el("st-bets").textContent  = s.bets_today      ?? "–";
    el("st-api").textContent   = s.api_remaining != null ? "API: " + s.api_remaining + " left" : "";
    el("st-fetch").textContent = s.last_fetch ? "Fetched " + relTime(s.last_fetch) : "";
  } catch (_) {}
}

/* ── Opportunities tab ───────────────────────────────────────────────────── */

async function loadEdges() {
  const tbody   = document.getElementById("edges-body");
  const minEdge = parseFloat(document.getElementById("f-edge").value)  || 0;
  const sport   = document.getElementById("f-sport").value.trim();
  const bk      = document.getElementById("f-bk").value.trim();

  tbody.innerHTML = `<tr><td colspan="10" class="loading"><span class="spinner"></span>Loading…</td></tr>`;

  const params = new URLSearchParams({ min_edge: minEdge, limit: 200 });
  if (sport) params.set("sport", sport);
  if (bk)    params.set("bookmaker", bk);

  let rows;
  try {
    rows = await api("/api/edges?" + params);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty">Error loading data.</td></tr>`;
    return;
  }

  // Key banner
  try {
    const cfg = await api("/api/config");
    document.getElementById("no-key-banner").classList.toggle("hidden", !!cfg.odds_api_key_set);
  } catch (_) {}

  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty">No edge opportunities found. Try lowering the minimum edge or click Fetch now.</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map(r => {
    const hasEdge   = (r.best_edge_pct || 0) >= 2;
    const eventName = r.home_team && r.away_team
      ? `${esc(r.home_team)} vs ${esc(r.away_team)}` : "–";
    const pmYes  = r.yes_price  != null ? (r.yes_price  * 100).toFixed(1) + "%" : "–";
    const pmNo   = r.no_price   != null ? (r.no_price   * 100).toFixed(1) + "%" : "–";
    const bkYes  = r.book_yes_implied_pct != null ? r.book_yes_implied_pct.toFixed(1) + "%" : "–";
    const bkNo   = r.book_no_implied_pct  != null ? r.book_no_implied_pct.toFixed(1)  + "%" : "–";
    const epct   = r.best_edge_pct || 0;
    const pmUrl  = r.slug ? `https://polymarket.com/event/${esc(r.slug)}` : null;
    const qCell  = pmUrl
      ? `<a class="q-link" href="${pmUrl}" target="_blank">${esc(r.question)}</a>`
      : esc(r.question);

    return `<tr class="${hasEdge ? "has-edge" : ""}">
      <td>
        <div class="ev-teams">${eventName}</div>
        <div class="ev-meta">${esc(r.bookmaker || "")}${r.sport ? " · " + esc(r.sport) : ""}</div>
      </td>
      <td class="q-cell">${qCell}</td>
      <td class="n">${pmYes}</td>
      <td class="n">${bkYes}</td>
      <td class="n">${pmNo}</td>
      <td class="n">${bkNo}</td>
      <td class="n"><span class="edge-val ${edgeColorClass(epct)}">${epct.toFixed(2)}%</span></td>
      <td class="n">${sideBadge(r.best_side)}</td>
      <td class="n">${r.today_bet_status ? betStatusBadge(r.today_bet_status) : '<span class="bet-none">–</span>'}</td>
      <td class="n">${confBadge(r.match_score)}</td>
    </tr>`;
  }).join("");
}

document.getElementById("btn-apply").addEventListener("click", loadEdges);
["f-edge", "f-sport", "f-bk"].forEach(id => {
  document.getElementById(id)?.addEventListener("keydown", e => e.key === "Enter" && loadEdges());
});

document.getElementById("btn-fetch").addEventListener("click", async function () {
  this.disabled = true;
  this.innerHTML = `<span class="spinner"></span>Fetching…`;
  const btn = this;
  try {
    await api("/api/fetch", { method: "POST" });
    setTimeout(async () => {
      await refreshStats();
      await loadEdges();
      btn.disabled = false;
      btn.textContent = "↻ Fetch now";
    }, 5000);
  } catch (_) {
    btn.disabled = false;
    btn.textContent = "↻ Fetch now";
  }
});

/* ── Bets tab ────────────────────────────────────────────────────────────── */

async function loadBets() {
  const tbody = document.getElementById("bets-body");
  tbody.innerHTML = `<tr><td colspan="10" class="loading"><span class="spinner"></span>Loading…</td></tr>`;

  let rows;
  try {
    rows = await api("/api/bets?limit=200");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty">Error loading bets.</td></tr>`;
    return;
  }

  // Summary bar
  const placed     = rows.filter(b => b.status === "PLACED");
  const nowDate    = new Date().toDateString();
  const todayBets  = placed.filter(b => {
    if (!b.placed_at) return false;
    const s = b.placed_at;
    return new Date(s.includes("Z") ? s : s + "Z").toDateString() === nowDate;
  });
  const totalStaked = placed.reduce((s, b) => s + (b.size_usdc || 0), 0);
  const todayStaked = todayBets.reduce((s, b) => s + (b.size_usdc || 0), 0);

  document.getElementById("bets-summary").innerHTML = `
    <div class="bsum-item">
      <span class="bsum-label">Total placed</span>
      <span class="bsum-val bsum-green">${placed.length}</span>
    </div>
    <div class="bsum-item">
      <span class="bsum-label">Today</span>
      <span class="bsum-val bsum-yellow">${todayBets.length}</span>
    </div>
    <div class="bsum-item">
      <span class="bsum-label">Staked today</span>
      <span class="bsum-val bsum-green">${usdc(todayStaked)}</span>
    </div>
    <div class="bsum-item">
      <span class="bsum-label">Total staked</span>
      <span class="bsum-val">${usdc(totalStaked)}</span>
    </div>
    <div class="bsum-item">
      <span class="bsum-label">Failed</span>
      <span class="bsum-val bsum-red">${rows.filter(b => b.status === "FAILED").length}</span>
    </div>
  `;

  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty">No bets placed yet. Enable auto-betting in Settings and run a fetch.</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map(b => `
    <tr>
      <td>${fmtDate(b.placed_at)}</td>
      <td><div class="ev-teams">${esc(b.event_name || "–")}</div></td>
      <td class="q-cell">${esc(b.question || "–")}</td>
      <td class="n">${sideBadge(b.side)}</td>
      <td class="n pm-val">${b.pm_price != null ? (b.pm_price * 100).toFixed(1) + "%" : "–"}</td>
      <td class="n book-val">${b.book_implied != null ? (b.book_implied * 100).toFixed(1) + "%" : "–"}</td>
      <td class="n"><span class="edge-val ${edgeColorClass(b.edge_pct || 0)}">${(b.edge_pct || 0).toFixed(2)}%</span></td>
      <td class="n">${usdc(b.size_usdc)}</td>
      <td class="n">${betStatusBadge(b.status)}</td>
      <td><span class="order-id" title="${esc(b.order_id || "")}">${esc(b.order_id || "–")}</span></td>
    </tr>
  `).join("");
}

document.getElementById("btn-refresh-bets").addEventListener("click", loadBets);

/* ── Markets tab ─────────────────────────────────────────────────────────── */

async function loadMarkets() {
  const tbody       = document.getElementById("markets-body");
  const q           = document.getElementById("m-search").value.trim();
  const matchedOnly = document.getElementById("m-matched").checked;

  tbody.innerHTML = `<tr><td colspan="8" class="loading"><span class="spinner"></span>Loading…</td></tr>`;

  const params = new URLSearchParams({ limit: 200 });
  if (q)           params.set("q", q);
  if (matchedOnly) params.set("matched_only", "true");

  let rows;
  try {
    rows = await api("/api/markets?" + params);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty">Error loading markets.</td></tr>`;
    return;
  }

  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty">No markets found.</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map(r => {
    const pmUrl  = r.slug ? `https://polymarket.com/event/${esc(r.slug)}` : null;
    const qCell  = pmUrl
      ? `<a class="q-link" href="${pmUrl}" target="_blank">${esc(r.question)}</a>`
      : esc(r.question);
    const matched = !!r.event_id;
    const evCell  = matched
      ? `<span class="tag">${esc(r.bookmaker || "")}</span> ${esc(r.home_team)} vs ${esc(r.away_team)}`
      : `<span style="color:var(--dim)">–</span>`;
    const unmatchBtn = matched
      ? `<button class="btn-unmatch" data-pmid="${esc(r.id)}" title="Remove match">✕</button>`
      : "";
    return `<tr>
      <td class="q-cell">${qCell}</td>
      <td class="n">${r.yes_price != null ? (r.yes_price * 100).toFixed(1) + "%" : "–"}</td>
      <td class="n">${r.no_price  != null ? (r.no_price  * 100).toFixed(1) + "%" : "–"}</td>
      <td class="n">${money(r.volume)}</td>
      <td>${evCell}</td>
      <td class="n">${confBadge(r.match_score)}</td>
      <td><span class="tag">${esc(r.category || "–")}</span></td>
      <td>${unmatchBtn}</td>
    </tr>`;
  }).join("");

  tbody.querySelectorAll(".btn-unmatch").forEach(btn => {
    btn.addEventListener("click", async () => {
      await api(`/api/markets/${encodeURIComponent(btn.dataset.pmid)}/unmatch`, { method: "POST" });
      loadMarkets();
    });
  });
}

document.getElementById("m-apply").addEventListener("click", loadMarkets);
document.getElementById("m-search")?.addEventListener("keydown", e => e.key === "Enter" && loadMarkets());

/* ── Settings tab ────────────────────────────────────────────────────────── */

async function loadConfig() {
  let cfg;
  try { cfg = await api("/api/config"); } catch (_) { return; }

  // Odds API
  if (cfg.odds_api_key_set) {
    document.getElementById("cfg-odds-key").placeholder = cfg.odds_api_key_masked || "••••••••";
    document.getElementById("cfg-odds-hint").textContent =
      "Key saved (" + (cfg.odds_api_key_masked || "set") + ") — leave blank to keep";
  }
  document.getElementById("cfg-books").value = cfg.bookmakers || "pinnacle";

  // Auto-betting
  document.getElementById("cfg-auto-bet").checked = cfg.auto_bet_enabled === "true";
  if (cfg.pm_private_key_set) {
    document.getElementById("cfg-pk").placeholder = cfg.pm_private_key_masked || "0x… (set)";
    document.getElementById("cfg-pk-hint").textContent =
      "Key saved (" + (cfg.pm_private_key_masked || "set") + ") — leave blank to keep";
  }
  document.getElementById("cfg-size").value     = cfg.bet_size     || "10";
  document.getElementById("cfg-min-edge").value = cfg.min_edge_pct  || "2.5";
  document.getElementById("cfg-max-bets").value = cfg.max_bets_day  || "10";

  // Fetch & Matching
  document.getElementById("cfg-interval").value = cfg.fetch_interval  || "10";
  document.getElementById("cfg-minvol").value   = cfg.min_volume       || "5000";
  document.getElementById("cfg-thresh").value   = cfg.match_threshold  || "0.50";
}

document.getElementById("btn-save").addEventListener("click", async () => {
  const msg = document.getElementById("save-msg");
  msg.className = "save-msg hidden";

  const body = {
    bookmakers:       document.getElementById("cfg-books").value.trim()    || "pinnacle",
    auto_bet_enabled: document.getElementById("cfg-auto-bet").checked ? "true" : "false",
    bet_size:         document.getElementById("cfg-size").value,
    min_edge_pct:     document.getElementById("cfg-min-edge").value,
    max_bets_day:     document.getElementById("cfg-max-bets").value,
    fetch_interval:   document.getElementById("cfg-interval").value,
    min_volume:       document.getElementById("cfg-minvol").value,
    match_threshold:  document.getElementById("cfg-thresh").value,
  };

  const oddsKey = document.getElementById("cfg-odds-key").value.trim();
  if (oddsKey) body.odds_api_key = oddsKey;

  const pk = document.getElementById("cfg-pk").value.trim();
  if (pk) body.pm_private_key = pk;

  try {
    await api("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    msg.textContent = "Settings saved.";
    msg.className = "save-msg ok";
    document.getElementById("cfg-odds-key").value = "";
    document.getElementById("cfg-pk").value = "";
    await loadConfig();
  } catch (e) {
    msg.textContent = "Error saving: " + e.message;
    msg.className = "save-msg err";
  }
  msg.classList.remove("hidden");
  setTimeout(() => msg.classList.add("hidden"), 4000);
});

/* ── Auto-refresh & init ─────────────────────────────────────────────────── */

refreshStats();
setInterval(refreshStats, 30_000);

switchTab("edges");
