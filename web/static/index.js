(function () {
  const pick = NOTIFAI_EXAMPLES.slice().sort(() => Math.random() - 0.5).slice(0, 5);
  const container = document.getElementById("examples");
  container.style.display = "flex";
  container.style.flexDirection = "column";
  container.style.gap = "0.5rem";
  pick.forEach(text => {
    const div = document.createElement("div");
    div.className = "example";
    div.textContent = text;
    container.appendChild(div);
  });
})();

(async () => {
  try {
    const r = await fetch("/auth/me");
    if (r.ok) window.location.replace("/dashboard.html");
  } catch (_) {}
})();

document.getElementById("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = document.getElementById("email").value.trim();
  const btn = document.getElementById("btn");
  const success = document.getElementById("success");
  const error = document.getElementById("error");

  btn.disabled = true;
  error.style.display = "none";

  try {
    const r = await fetch("/auth/request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    if (r.ok) {
      document.getElementById("form").style.display = "none";
      success.style.display = "block";
    } else {
      const data = await r.json().catch(() => ({}));
      error.textContent = data.detail || "Something went wrong. Please try again.";
      error.style.display = "block";
      btn.disabled = false;
    }
  } catch (_) {
    error.textContent = "Network error. Please try again.";
    error.style.display = "block";
    btn.disabled = false;
  }
});
