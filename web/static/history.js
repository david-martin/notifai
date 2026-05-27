let queries = [];
let currentPage = 1;
const PAGE_SIZE = 20;

async function api(path, options = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (r.status === 401) { window.location.replace("/"); return null; }
  return r;
}

function fmtDate(iso) {
  const d = new Date(iso + "Z");
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
    + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function escHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

async function init() {
  const r = await api("/auth/me");
  if (!r) return;
  const qr = await api("/queries");
  if (!qr) return;
  queries = await qr.json();

  const sel = document.getElementById("querySelect");
  queries.forEach(q => {
    const opt = document.createElement("option");
    opt.value = q.id;
    opt.textContent = "Notify me when… " + q.query_text;
    sel.appendChild(opt);
  });

  sel.addEventListener("change", () => { currentPage = 1; loadHistory(); });

  // Pre-select if query id in URL hash
  const hash = location.hash.replace("#", "");
  if (hash && queries.find(q => q.id === hash)) {
    sel.value = hash;
    loadHistory();
  }
}

async function loadHistory() {
  const qid = document.getElementById("querySelect").value;
  const list = document.getElementById("logList");
  const pag = document.getElementById("pagination");
  if (!qid) {
    list.innerHTML = "";
    pag.innerHTML = "";
    return;
  }

  const r = await api(`/queries/${qid}/history?page=${currentPage}&page_size=${PAGE_SIZE}`);
  if (!r) return;
  const data = await r.json();

  if (data.total === 0) {
    list.innerHTML = `<div class="empty-state">No checks yet for this query.</div>`;
    pag.innerHTML = "";
    return;
  }

  list.innerHTML = data.items.map(item => `
    <div class="log-entry">
      <span class="answer-badge ${escHtml(item.answer.toLowerCase())}">${escHtml(item.answer)}</span>
      <div class="log-body">
        <div class="log-reason">${escHtml(item.reason || "")}</div>
        ${item.sources && item.sources.length ? `
          <div class="log-sources">
            ${item.sources.map(s => {
              const safe = /^https?:\/\//.test(s) ? s : "#";
              return `<a href="${escHtml(safe)}" target="_blank" rel="noopener">${escHtml(s)}</a>`;
            }).join("")}
          </div>` : ""}
      </div>
      <div class="log-date">${fmtDate(item.checked_at)}</div>
    </div>
  `).join("");

  // Pagination
  const totalPages = Math.ceil(data.total / PAGE_SIZE);
  if (totalPages <= 1) { pag.innerHTML = ""; return; }

  let html = `<button class="btn-page" onclick="goPage(${currentPage - 1})" ${currentPage === 1 ? "disabled" : ""}>← Prev</button>`;
  for (let p = 1; p <= totalPages; p++) {
    html += `<button class="btn-page ${p === currentPage ? "current" : ""}" onclick="goPage(${p})">${p}</button>`;
  }
  html += `<button class="btn-page" onclick="goPage(${currentPage + 1})" ${currentPage === totalPages ? "disabled" : ""}>Next →</button>`;
  pag.innerHTML = html;
}

function goPage(p) {
  currentPage = p;
  loadHistory();
}

document.getElementById("logoutBtn").addEventListener("click", async () => {
  await api("/auth/logout", { method: "POST" });
  window.location.replace("/");
});

init();
