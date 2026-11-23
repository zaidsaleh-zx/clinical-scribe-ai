(() => {
  const API_BASE = window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "";
  let sessionKey = "";
  let authRequired = false;
  let overlay;

  function showLogin(message = "") {
    if (!overlay) {
      const style = document.createElement("style");
      style.textContent = `
        .auth-overlay{position:fixed;inset:0;z-index:10000;display:grid;place-items:center;padding:20px;background:rgba(5,16,14,.82);backdrop-filter:blur(8px)}
        .auth-overlay[hidden]{display:none}
        .auth-card{width:min(420px,100%);padding:28px;border:1px solid var(--border,#dce7e3);border-radius:16px;background:var(--surface-solid,#fff);color:var(--text-primary,#172624);box-shadow:0 20px 70px rgba(0,0,0,.35);font-family:var(--font-body,Arial,sans-serif)}
        .auth-card h2{margin:0 0 8px}.auth-card p{color:var(--text-secondary,#54655f);line-height:1.5}
        .auth-card label{display:block;margin:18px 0 6px;font-weight:600}.auth-card input{width:100%;padding:12px;border:1px solid var(--border,#dce7e3);border-radius:8px;font:inherit}
        .auth-card button{width:100%;margin-top:14px;padding:12px;border:0;border-radius:8px;background:var(--primary,#0f6b5c);color:white;font:inherit;font-weight:700;cursor:pointer}
        .auth-error{min-height:1.4em;color:#b42318!important}.auth-logout{margin-left:8px}
      `;
      document.head.append(style);
      overlay = document.createElement("div");
      overlay.className = "auth-overlay";
      overlay.innerHTML = `
        <form class="auth-card" autocomplete="on">
          <h2>Sign in</h2>
          <p>This prototype is protected by the administrator access key configured on the server.</p>
          <label for="authAccessKey">Access key</label>
          <input id="authAccessKey" type="password" name="password" autocomplete="current-password" required />
          <p class="auth-error" role="alert" aria-live="polite"></p>
          <button type="submit">Continue</button>
        </form>`;
      overlay.querySelector("form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const input = overlay.querySelector("#authAccessKey");
        const error = overlay.querySelector(".auth-error");
        const button = overlay.querySelector("button");
        button.disabled = true;
        error.textContent = "";
        try {
          const response = await fetch(`${API_BASE}/api/auth/login`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ api_key: input.value }),
          });
          if (!response.ok) {
            const result = await response.json().catch(() => ({}));
            throw new Error(result.error || "Sign-in failed.");
          }
          sessionKey = window.location.protocol === "file:" ? input.value : "";
          input.value = "";
          hideLogin();
          addLogoutButton();
          document.dispatchEvent(new Event("clinical-scribe-authenticated"));
        } catch (errorValue) {
          error.textContent = errorValue.message || "Unable to sign in.";
          input.select();
        } finally {
          button.disabled = false;
        }
      });
      document.body.append(overlay);
    }
    overlay.hidden = false;
    overlay.querySelector(".auth-error").textContent = message;
    overlay.querySelector("input").focus();
  }

  function hideLogin() {
    if (overlay) overlay.hidden = true;
  }

  async function apiFetch(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (sessionKey && window.location.protocol === "file:") {
      headers.set("X-API-Key", sessionKey);
    }
    const response = await fetch(url, { ...options, headers, credentials: "include" });
    if (response.status === 401 && authRequired) showLogin("Your session expired. Sign in again.");
    return response;
  }

  async function initialize() {
    try {
      const response = await fetch(`${API_BASE}/api/auth/status`, { credentials: "include" });
      if (!response.ok) throw new Error(`Authentication status returned ${response.status}.`);
      const status = await response.json();
      authRequired = status.auth_required;
      if (authRequired && !status.authenticated) {
        showLogin();
        return false;
      }
      hideLogin();
      if (authRequired) addLogoutButton();
      return true;
    } catch (error) {
      showLogin(error.message || "Could not connect to the server.");
      return false;
    }
  }

  function addLogoutButton() {
    const target = document.querySelector(".topbar-right, .top-actions");
    if (!target || target.querySelector(".auth-logout")) return;
    const button = document.createElement("button");
    button.className = "auth-logout";
    button.textContent = "Sign out";
    button.type = "button";
    button.addEventListener("click", async () => {
      await apiFetch(`${API_BASE}/api/auth/logout`, { method: "POST" });
      sessionKey = "";
      window.location.reload();
    });
    target.append(button);
  }

  window.apiFetch = apiFetch;
  window.clinicalScribeAuth = { initialize, showLogin };
})();
