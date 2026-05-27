async function api(path, options = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (r.status === 401) { window.location.replace("/"); return null; }
  return r;
}

async function init() {
  const r = await api("/auth/me");
  if (!r) return;
  const me = await r.json();
  document.getElementById("loginEmail").textContent = me.email;
  document.getElementById("creditDisplay").textContent =
    me.query_credits === 1 ? "1 credit" : `${me.query_credits} credits`;

  if (me.notify_email) {
    document.getElementById("notifyEmail").value = me.notify_email;
  }

  // Handle ?purchased=1 redirect from Stripe
  if (location.search.includes("purchased=1")) {
    // Re-fetch to get updated credit count
    const r2 = await api("/auth/me");
    if (r2 && r2.ok) {
      const me2 = await r2.json();
      document.getElementById("creditDisplay").textContent =
        me2.query_credits === 1 ? "1 credit" : `${me2.query_credits} credits`;
    }
  }
}

document.getElementById("saveBtn").addEventListener("click", async () => {
  const val = document.getElementById("notifyEmail").value.trim();
  const btn = document.getElementById("saveBtn");
  const success = document.getElementById("saveSuccess");
  const error = document.getElementById("saveError");
  success.style.display = "none";
  error.style.display = "none";
  btn.disabled = true;

  const r = await api("/auth/account", {
    method: "PATCH",
    body: JSON.stringify({ notify_email: val || null }),
  });
  btn.disabled = false;
  if (!r) return;
  if (r.ok) {
    success.style.display = "block";
  } else {
    const d = await r.json().catch(() => ({}));
    error.textContent = d.detail || "Failed to save.";
    error.style.display = "block";
  }
});

document.querySelectorAll("[data-package]").forEach(btn => {
  btn.addEventListener("click", async () => {
    const pkg = btn.dataset.package;
    const err = document.getElementById("buyError");
    err.style.display = "none";
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Redirecting…";

    const r = await api("/billing/checkout", {
      method: "POST",
      body: JSON.stringify({ package: pkg }),
    });
    if (!r) { btn.disabled = false; btn.textContent = originalText; return; }
    if (r.ok) {
      const { url } = await r.json();
      window.location.href = url;
    } else {
      err.textContent = "Failed to start checkout. Please try again.";
      err.style.display = "block";
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });
});

document.getElementById("logoutBtn").addEventListener("click", async () => {
  await api("/auth/logout", { method: "POST" });
  window.location.replace("/");
});

init();
