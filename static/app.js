/* ── State ───────────────────────────────────────────────────────────────── */
const state = {
  traders: [],
  activeAddress: null,   // null = show all
  offset: 0,
  limit: 50,
  filterSide: "",
};

/* ── DOM refs ────────────────────────────────────────────────────────────── */
const traderList     = document.getElementById("trader-list");
const tradeFeed      = document.getElementById("trade-feed");
const filterLabel    = document.getElementById("filter-label");
const lastRefresh    = document.getElementById("last-refresh");
const addForm        = document.getElementById("add-form");
const inputAddress   = document.getElementById("input-address");
const addError       = document.getElementById("add-error");
const loadMoreWrap   = document.getElementById("load-more-wrap");
const filterSideEl   = document.getElementById("filter-side");

/* ── Helpers ─────────────────────────────────────────────────────────────── */
function fmt(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = Date.now();
  const diff = Math.floor((now - d.getTime()) / 1000);
  if (diff < 60)   return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400)return `${Math.floor(diff/3600)}h ago`;
  return d.toLocaleDateString();
}

function shortAddr(addr) {
  if (!addr) return "";
  return addr.slice(0, 6) + "…" + addr.slice(-4);
}

function displayName(trader) {
  return trader.username || shortAddr(trader.address);
}

function avatarEl(src, name, size = 36) {
  if (src) {
    const img = document.createElement("img");
    img.className = "trader-avatar";
    img.style.width = img.style.height = size + "px";
    img.src = src;
    img.alt = name || "avatar";
    img.onerror = () => img.replaceWith(initials(name, size));
    return img;
  }
  return initials(name, size);
}

function initials(name, size = 36) {
  const div = document.createElement("div");
  div.className = "trader-avatar-placeholder";
  div.style.width = div.style.height = size + "px";
  const n = (name || "?").trim();
  div.textContent = n.length > 1 ? (n[0] + (n.split(" ")[1]?.[0] || n[1])).toUpperCase() : n[0].toUpperCase();
  return div;
}

function polyUrl(address) {
  return `https://polymarket.com/profile/${address}`;
}

function txUrl(hash) {
  return `https://polygonscan.com/tx/${hash}`;
}

/* ── API calls ───────────────────────────────────────────────────────────── */
async function apiFetch(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

/* ── Render traders ──────────────────────────────────────────────────────── */
function renderTraders() {
  traderList.innerHTML = "";

  if (state.traders.length === 0) {
    const li = document.createElement("li");
    li.className = "trader-item";
    li.innerHTML = `<span class="dim" style="font-size:.82rem;padding:8px 4px">No traders yet</span>`;
    traderList.appendChild(li);
    return;
  }

  state.traders.forEach(t => {
    const li = document.createElement("li");
    li.className = "trader-item" + (state.activeAddress === t.address ? " active" : "");
    li.dataset.address = t.address;

    const avatar = avatarEl(t.profile_image, displayName(t), 36);
    avatar.className = t.profile_image ? "trader-avatar" : "trader-avatar-placeholder";

    // Extra info row
    let extraHtml = "";
    if (t.twitter) {
      extraHtml += `<span class="trader-tag">🐦 <a href="https://x.com/${t.twitter}" target="_blank">@${t.twitter}</a></span>`;
    }
    if (t.website) {
      extraHtml += `<span class="trader-tag">🌐 <a href="${t.website}" target="_blank">site</a></span>`;
    }
    extraHtml += `<span class="trader-tag">🔗 <a href="${polyUrl(t.address)}" target="_blank">profile</a></span>`;

    const bioHtml = t.bio
      ? `<div class="trader-bio">${escHtml(t.bio)}</div>`
      : "";

    li.innerHTML = `
      <div class="trade-avatar-wrap"></div>
      <div class="trader-info">
        <div class="trader-name">${escHtml(displayName(t))}</div>
        <div class="trader-address">${escHtml(t.address)}</div>
        ${bioHtml}
        <div class="trader-extra">${extraHtml}</div>
      </div>
      <button class="trader-remove" data-address="${t.address}" title="Remove">×</button>
    `;
    li.querySelector(".trade-avatar-wrap").appendChild(avatar);

    li.addEventListener("click", e => {
      if (e.target.closest(".trader-remove") || e.target.closest("a")) return;
      selectTrader(t.address);
    });

    li.querySelector(".trader-remove").addEventListener("click", e => {
      e.stopPropagation();
      removeTrader(t.address);
    });

    traderList.appendChild(li);
  });
}

/* ── Render trades ───────────────────────────────────────────────────────── */
function renderTrades(trades, append = false) {
  if (!append) tradeFeed.innerHTML = "";

  if (trades.length === 0 && !append) {
    tradeFeed.innerHTML = `<div class="empty-state">No trades found.</div>`;
    return;
  }

  trades.forEach(tr => {
    const card = document.createElement("div");
    const sideClass = tr.side === "SELL" ? "sell" : "buy";
    card.className = `trade-card ${sideClass}`;

    const traderName = tr.username || shortAddr(tr.trader_address);
    const avatar = avatarEl(tr.profile_image, traderName, 32);
    avatar.className = tr.profile_image ? "trade-avatar" : "trade-avatar-placeholder";

    const iconHtml = tr.market_icon
      ? `<img class="market-icon" src="${escHtml(tr.market_icon)}" alt="" onerror="this.remove()">`
      : "";

    const txHtml = tr.transaction_hash
      ? `<a class="tx-link" href="${txUrl(tr.transaction_hash)}" target="_blank">↗ ${tr.transaction_hash.slice(0,10)}…</a>`
      : "";

    const usdcStr = tr.usdc_size
      ? `$${Number(tr.usdc_size).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})}`
      : "–";
    const priceStr = tr.price
      ? `${(Number(tr.price)*100).toFixed(1)}¢`
      : "–";
    const sharesStr = tr.size
      ? Number(tr.size).toLocaleString(undefined, {maximumFractionDigits:2})
      : "–";

    card.innerHTML = `
      <div class="trade-avatar-wrap"></div>
      <div class="trade-body">
        <div class="trade-top">
          <span class="trade-trader">${escHtml(traderName)}</span>
          <span class="side-badge ${sideClass}">${tr.side || "BUY"}</span>
          <span class="trade-time">${fmt(tr.timestamp)}</span>
        </div>
        <div class="trade-market">
          ${iconHtml}
          <span class="trade-title">${escHtml(tr.market_title || "Unknown market")}</span>
        </div>
        <div class="trade-meta">
          <div class="meta-item">
            <span class="meta-label">Outcome</span>
            <span class="meta-value"><span class="outcome-chip">${escHtml(tr.outcome || "–")}</span></span>
          </div>
          <div class="meta-item">
            <span class="meta-label">Amount</span>
            <span class="meta-value">${usdcStr}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">Price</span>
            <span class="meta-value">${priceStr}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">Shares</span>
            <span class="meta-value">${sharesStr}</span>
          </div>
        </div>
        ${txHtml}
      </div>
    `;
    card.querySelector(".trade-avatar-wrap").appendChild(avatar);
    tradeFeed.appendChild(card);
  });
}

/* ── Load trades ─────────────────────────────────────────────────────────── */
async function loadTrades(append = false) {
  if (!append) {
    state.offset = 0;
    tradeFeed.innerHTML = `<div class="empty-state"><span class="spinner"></span>Loading…</div>`;
    loadMoreWrap.classList.add("hidden");
  }

  let url = `/api/trades?limit=${state.limit}&offset=${state.offset}`;
  if (state.activeAddress) url += `&address=${state.activeAddress}`;
  if (state.filterSide)    url += `&side=${state.filterSide}`;

  try {
    const trades = await apiFetch(url);
    renderTrades(trades, append);
    state.offset += trades.length;
    loadMoreWrap.classList.toggle("hidden", trades.length < state.limit);
    lastRefresh.textContent = "Updated " + fmt(Math.floor(Date.now()/1000));
  } catch (e) {
    tradeFeed.innerHTML = `<div class="empty-state">Error loading trades: ${e.message}</div>`;
  }
}

/* ── Load traders ────────────────────────────────────────────────────────── */
async function loadTraders() {
  try {
    state.traders = await apiFetch("/api/traders");
    renderTraders();
  } catch (e) {
    console.error("Failed to load traders", e);
  }
}

/* ── Actions ─────────────────────────────────────────────────────────────── */
function selectTrader(address) {
  state.activeAddress = state.activeAddress === address ? null : address;
  const t = state.traders.find(t => t.address === address);
  filterLabel.textContent = state.activeAddress
    ? (t ? displayName(t) : shortAddr(address))
    : "All traders";
  renderTraders();
  loadTrades();
}

async function removeTrader(address) {
  if (!confirm("Remove this trader and all their trades?")) return;
  try {
    await apiFetch(`/api/traders/${address}`, { method: "DELETE" });
    if (state.activeAddress === address) {
      state.activeAddress = null;
      filterLabel.textContent = "All traders";
    }
    await loadTraders();
    await loadTrades();
  } catch (e) {
    alert("Failed to remove: " + e.message);
  }
}

async function addTrader(address) {
  addError.classList.add("hidden");
  const btn = document.getElementById("btn-submit-add");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>Fetching…`;

  try {
    const trader = await apiFetch("/api/traders", {
      method: "POST",
      body: JSON.stringify({ address }),
    });
    await loadTraders();
    await loadTrades();
    addForm.classList.add("hidden");
    inputAddress.value = "";
    // Select the new trader
    selectTrader(trader.address || address.toLowerCase());
  } catch (e) {
    addError.textContent = "Error: " + e.message;
    addError.classList.remove("hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = "Track";
  }
}

function escHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ── Event listeners ─────────────────────────────────────────────────────── */
document.getElementById("btn-add-trader").addEventListener("click", () => {
  addForm.classList.toggle("hidden");
  if (!addForm.classList.contains("hidden")) inputAddress.focus();
});

document.getElementById("btn-cancel-add").addEventListener("click", () => {
  addForm.classList.add("hidden");
  addError.classList.add("hidden");
  inputAddress.value = "";
});

document.getElementById("btn-submit-add").addEventListener("click", () => {
  const addr = inputAddress.value.trim();
  if (!addr) { addError.textContent = "Please enter a wallet address."; addError.classList.remove("hidden"); return; }
  addTrader(addr);
});

inputAddress.addEventListener("keydown", e => {
  if (e.key === "Enter") document.getElementById("btn-submit-add").click();
});

document.getElementById("btn-refresh-all").addEventListener("click", async () => {
  const btn = document.getElementById("btn-refresh-all");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>`;
  try {
    if (state.activeAddress) {
      await apiFetch(`/api/traders/${state.activeAddress}/refresh`, { method: "POST" });
    } else {
      await Promise.all(state.traders.map(t =>
        apiFetch(`/api/traders/${t.address}/refresh`, { method: "POST" }).catch(() => {})
      ));
    }
    await loadTraders();
    await loadTrades();
  } finally {
    btn.disabled = false;
    btn.textContent = "↻ Refresh";
  }
});

document.getElementById("btn-load-more").addEventListener("click", () => {
  loadTrades(true);
});

filterSideEl.addEventListener("change", () => {
  state.filterSide = filterSideEl.value;
  loadTrades();
});

/* ── Auto-refresh every 60s ──────────────────────────────────────────────── */
setInterval(async () => {
  await loadTraders();
  await loadTrades();
}, 60_000);

/* ── Init ────────────────────────────────────────────────────────────────── */
(async () => {
  await loadTraders();
  await loadTrades();
})();
