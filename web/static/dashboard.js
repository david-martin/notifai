let queries = [];
let generatedQuery = null;
let userTier = "free";

const INTERVAL_LABELS = {
  "1d": "Checked daily",
  "1w": "Checked weekly",
  "1mo": "Checked monthly",
};

function intervalSelectHtml(currentInterval, queryId) {
  const opts = [
    { value: "1d", label: "daily" },
    { value: "1w", label: "weekly" },
    { value: "1mo", label: "monthly" },
  ];
  const options = opts
    .map(o => `<option value="${o.value}"${o.value === currentInterval ? " selected" : ""}>${o.label}</option>`)
    .join("");
  return `<select class="schedule-select" data-id="${escHtml(queryId)}" aria-label="Check frequency">${options}</select>`;
}

function escHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function renderExampleChips() {
  const container = document.getElementById("exampleChips");
  container.innerHTML = "";
  const pick = NOTIFAI_EXAMPLES.slice().sort(() => Math.random() - 0.5).slice(0, 3);
  pick.forEach(text => {
    const btn = document.createElement("button");
    btn.className = "example-chip";
    btn.type = "button";
    btn.textContent = text;
    btn.addEventListener("click", () => {
      document.getElementById("descInput").value = text;
      document.getElementById("descInput").focus();
    });
    container.appendChild(btn);
  });
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const r = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      ...options,
    });
    if (r.status === 401) { window.location.replace("/"); return null; }
    return r;
  } finally {
    clearTimeout(timer);
  }
}

async function init() {
  const meRes = await api("/auth/me");
  if (!meRes) return;
  const me = await meRes.json();
  document.getElementById("userEmail").textContent = me.email;
  userTier = me.tier;
  const credits = me.query_credits ?? 0;
  document.getElementById("creditBadge").textContent =
    credits === 0 ? "⚠ 0 credits · " : `${credits} credits · `;
  if (credits === 0) {
    document.getElementById("creditBadge").style.color = "var(--yellow)";
  }
  await loadQueries();
}

async function loadQueries() {
  const r = await api("/queries");
  if (!r) return;
  queries = await r.json();
  renderQueries();
}

function renderQueries() {
  const list = document.getElementById("queryList");
  const notice = document.getElementById("limitNotice");

  if (queries.length === 0) {
    list.innerHTML = `<div class="empty-state"><strong>No queries yet.</strong><p>Add one above to get started.</p></div>`;
    notice.textContent = "";
    return;
  }

  list.innerHTML = queries.map(q => `
    <div class="query-card ${q.active ? "" : "inactive"}" data-id="${escHtml(q.id)}">
      <button
        class="query-toggle ${q.active ? "on" : ""}"
        title="${q.active ? "Pause" : "Resume"}"
        data-id="${escHtml(q.id)}"
        data-active="${q.active}"
      ></button>
      <div class="query-body">
        <div class="notify-prefix-card">Notify me when…</div>
        <div class="query-text">${escHtml(q.query_text)}</div>
        <div class="query-meta">
          <span class="status-badge ${q.active ? "active" : q.completed ? "completed" : "paused"}">${q.active ? "Active" : q.completed ? "Completed" : "Paused"}</span>
          <button class="btn-secondary btn-sm run-now-btn" data-id="${escHtml(q.id)}">Run now</button>
          <button class="btn-danger delete-btn" data-id="${escHtml(q.id)}">Remove</button>
        </div>
        <div class="query-schedule">
          📅 ${intervalSelectHtml(q.check_interval || "1d", q.id)}
        </div>
      </div>
    </div>
  `).join("");

  // Attach event listeners after rendering (replaces inline onclick)
  list.querySelectorAll(".query-toggle").forEach(btn => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.id;
      const currentlyActive = btn.dataset.active === "true";
      toggleQuery(id, !currentlyActive);
    });
  });

  list.querySelectorAll(".run-now-btn").forEach(btn => {
    btn.addEventListener("click", () => runNow(btn.dataset.id, btn));
  });

  list.querySelectorAll(".delete-btn").forEach(btn => {
    btn.addEventListener("click", () => deleteQuery(btn.dataset.id));
  });

  list.querySelectorAll(".schedule-select").forEach(sel => {
    sel.addEventListener("change", () => {
      patchQueryInterval(sel.dataset.id, sel.value);
    });
  });

  notice.textContent = "";
  const newQueryBtn = document.getElementById("newQueryBtn");
  newQueryBtn.disabled = false;
  newQueryBtn.title = "";

  if (userTier === "free") {
    const activeCount = queries.filter(q => q.active).length;
    if (activeCount >= 1) {
      newQueryBtn.disabled = true;
      newQueryBtn.title = "Free accounts support 1 active query. Purchase credits to add more.";
      notice.innerHTML = `Free accounts support 1 active query. <a href="/account.html">Buy credits</a> to track more.`;
    }
  }
}

async function toggleQuery(id, active) {
  await api(`/queries/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ active }),
  });
  await loadQueries();
}

async function deleteQuery(id) {
  if (!confirm("Remove this query?")) return;
  await api(`/queries/${id}`, { method: "DELETE" });
  await loadQueries();
}

async function patchQueryInterval(id, interval) {
  await api(`/queries/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ check_interval: interval }),
  });
  await loadQueries();
}

async function runNow(id, btn) {
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>`;

  // Remove any previous result panel for this card
  const card = btn.closest(".query-card");
  const prev = card.querySelector(".run-now-result");
  if (prev) prev.remove();

  const r = await api(`/queries/${id}/run`, { method: "POST" });
  btn.disabled = false;
  btn.textContent = "Run now";

  if (!r) return;

  if (r.status === 429) {
    const panel = document.createElement("div");
    panel.className = "run-now-result";
    panel.innerHTML = `<div class="rn-answer no">Rate limit reached — try again in an hour.</div>`;
    card.querySelector(".query-body").appendChild(panel);
    return;
  }

  if (r.status === 402) {
    const panel = document.createElement("div");
    panel.className = "run-now-result";
    panel.innerHTML = `<div class="rn-answer no">No credits remaining. <a href="/account.html">Buy credits</a> to continue.</div>`;
    card.querySelector(".query-body").appendChild(panel);
    return;
  }

  if (r.status === 503) {
    const panel = document.createElement("div");
    panel.className = "run-now-result";
    panel.innerHTML = `<div class="rn-answer no">Web search unavailable — no credit deducted. Try again in a moment.</div>`;
    card.querySelector(".query-body").appendChild(panel);
    return;
  }

  if (!r.ok) {
    const panel = document.createElement("div");
    panel.className = "run-now-result";
    panel.innerHTML = `<div class="rn-answer no">Error running query. Please try again.</div>`;
    card.querySelector(".query-body").appendChild(panel);
    return;
  }

  const data = await r.json();
  const isYes = data.answer === "YES";
  const panel = document.createElement("div");
  panel.className = "run-now-result";
  panel.innerHTML = `
    <div class="rn-answer ${isYes ? "yes" : "no"}">${isYes ? "✓ YES" : "✗ NO"}</div>
    <div class="rn-reason">${escHtml(data.reason || "")}</div>
    <div class="rn-history"><a href="history.html">View in history</a></div>
  `;
  card.querySelector(".query-body").appendChild(panel);
}

// Create flow
function resetStep1() {
  document.getElementById("feedbackBox").style.display = "none";
  document.getElementById("reframeBox").style.display = "none";
  document.getElementById("validateError").style.display = "none";
  document.getElementById("validateBtn").style.display = "";
}

document.getElementById("newQueryBtn").addEventListener("click", () => {
  document.getElementById("createPanel").style.display = "block";
  document.getElementById("step1").style.display = "block";
  document.getElementById("step2").style.display = "none";
  const descInput = document.getElementById("descInput");
  descInput.value = "";
  descInput.placeholder = NOTIFAI_EXAMPLES[Math.floor(Math.random() * NOTIFAI_EXAMPLES.length)];
  document.getElementById("newQueryBtn").style.display = "none";
  resetStep1();
  renderExampleChips();
  generatedQuery = null;
  document.getElementById("intervalSelect").value = "1d";
});

function closePanel() {
  document.getElementById("createPanel").style.display = "none";
  document.getElementById("newQueryBtn").style.display = "";
}

document.getElementById("cancelBtn1").addEventListener("click", closePanel);
document.getElementById("cancelBtn2").addEventListener("click", closePanel);

document.getElementById("validateBtn").addEventListener("click", async () => {
  const desc = document.getElementById("descInput").value.trim();
  if (!desc) return;
  const btn = document.getElementById("validateBtn");
  const fb = document.getElementById("feedbackBox");
  const errEl = document.getElementById("validateError");
  errEl.style.display = "none";

  if (desc.length < 10) {
    errEl.textContent = "Please describe the event in more detail.";
    errEl.style.display = "block";
    return;
  }
  if (desc.length > 500) {
    errEl.textContent = "Description is too long (maximum 500 characters).";
    errEl.style.display = "block";
    return;
  }

  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>Checking…`;

  try {
    const r = await api("/queries/validate", {
      method: "POST",
      body: JSON.stringify({ description: desc }),
    });
    if (!r) return;

    if (r.status === 429) {
      errEl.textContent = "Monthly creation limit reached. Try again next month.";
      errEl.style.display = "block";
      btn.disabled = false;
      btn.textContent = "Check & continue";
      return;
    }

    const data = await r.json();

    if (data.valid) {
      fb.className = "feedback-box valid";
      fb.textContent = data.feedback;
      fb.style.display = "block";
      generatedQuery = { query_text: data.query_text };
      document.getElementById("previewQuery").textContent = data.query_text;
      document.getElementById("step1").style.display = "none";
      document.getElementById("step2").style.display = "block";
      btn.disabled = false;
      btn.textContent = "Check & continue";
    } else if (data.reframed) {
      fb.className = "feedback-box invalid";
      fb.textContent = data.feedback;
      fb.style.display = "block";
      document.getElementById("reframeText").textContent = data.reframed;
      document.getElementById("reframeBox").style.display = "block";
      document.getElementById("validateBtn").style.display = "none";
      btn.disabled = false;
      btn.textContent = "Check & continue";
    } else {
      fb.className = "feedback-box invalid";
      fb.textContent = data.feedback;
      fb.style.display = "block";
      btn.disabled = false;
      btn.textContent = "Check & continue";
    }
  } catch (_) {
    errEl.textContent = "Network error. Please try again.";
    errEl.style.display = "block";
    btn.disabled = false;
    btn.textContent = "Check & continue";
  }
});

document.getElementById("useReframeBtn").addEventListener("click", async () => {
  const reframed = document.getElementById("reframeText").textContent;
  const btn = document.getElementById("validateBtn");
  const errEl = document.getElementById("validateError");
  document.getElementById("descInput").value = reframed;
  document.getElementById("reframeBox").style.display = "none";
  document.getElementById("feedbackBox").style.display = "none";
  errEl.style.display = "none";
  document.getElementById("validateBtn").style.display = "";
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>Generating…`;

  try {
    const r = await api("/queries/validate", {
      method: "POST",
      body: JSON.stringify({ description: reframed }),
    });
    if (!r) return;

    if (r.status === 429) {
      errEl.textContent = "Monthly creation limit reached.";
      errEl.style.display = "block";
      btn.disabled = false;
      btn.textContent = "Check & continue";
      return;
    }

    const data = await r.json();
    if (data.valid && data.query_text) {
      generatedQuery = { query_text: data.query_text };
      document.getElementById("previewQuery").textContent = data.query_text;
      document.getElementById("step1").style.display = "none";
      document.getElementById("step2").style.display = "block";
    } else {
      errEl.textContent = data.feedback || "Could not generate query. Please try again.";
      errEl.style.display = "block";
    }
  } catch (_) {
    errEl.textContent = "Network error. Please try again.";
    errEl.style.display = "block";
  }
  btn.disabled = false;
  btn.textContent = "Check & continue";
});

document.getElementById("editInsteadBtn").addEventListener("click", () => {
  const reframed = document.getElementById("reframeText").textContent;
  document.getElementById("descInput").value = reframed;
  resetStep1();
  document.getElementById("descInput").focus();
});

document.getElementById("backBtn").addEventListener("click", () => {
  document.getElementById("step2").style.display = "none";
  document.getElementById("step1").style.display = "block";
  resetStep1();
});

document.getElementById("confirmBtn").addEventListener("click", async () => {
  if (!generatedQuery) return;
  const btn = document.getElementById("confirmBtn");
  const errEl = document.getElementById("confirmError");
  errEl.style.display = "none";
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>Adding…`;

  try {
    const r = await api("/queries", {
      method: "POST",
      body: JSON.stringify({
        query_text: generatedQuery.query_text,
        check_interval: document.getElementById("intervalSelect").value,
      }),
    });
    if (!r) { btn.disabled = false; btn.textContent = "Add query"; return; }

    if (r.status === 403) {
      errEl.innerHTML = `Free accounts support 1 active query. <a href="/account.html">Buy credits</a> to add more.`;
      errEl.style.display = "block";
      btn.disabled = false;
      btn.textContent = "Add query";
      return;
    }
    if (r.status === 429) {
      errEl.textContent = "Active query limit reached. Pause or remove an existing query.";
      errEl.style.display = "block";
      btn.disabled = false;
      btn.textContent = "Add query";
      return;
    }

    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      errEl.textContent = d.detail || "Failed to add query.";
      errEl.style.display = "block";
      btn.disabled = false;
      btn.textContent = "Add query";
      return;
    }

    closePanel();
    await loadQueries();
  } catch (_) {
    errEl.textContent = "Network error.";
    errEl.style.display = "block";
    btn.disabled = false;
    btn.textContent = "Add query";
  }
});

document.getElementById("logoutBtn").addEventListener("click", async () => {
  await api("/auth/logout", { method: "POST" });
  window.location.replace("/");
});

init();
