// Shared auth, hamburger, and utility code for grocery/pantry/trash pages.
// Include this script before each page's own <script> block.
// Usage: initCommonUI({ onLogin: () => myLoadFn() });

window.USER_ID = localStorage.getItem("grocery_user_code") || "";

function fmtDate(str) {
  if (!str) return "-";
  const d = new Date(str + "T00:00:00");
  return `${d.getMonth() + 1}/${String(d.getDate()).padStart(2, "0")}`;
}

function showAuth() {
  document.getElementById("auth-overlay").classList.add("visible");
}
function hideAuth() {
  document.getElementById("auth-overlay").classList.remove("visible");
}
function updateUserBar() {
  const el = document.getElementById("user-bar-code");
  if (el) el.textContent = window.USER_ID ? `👤 ${window.USER_ID}` : "";
}

function initCommonUI({ onLogin } = {}) {
  let authMode = "login";

  // Auth tabs
  document.getElementById("tab-login").addEventListener("click", () => {
    authMode = "login";
    document.getElementById("tab-login").classList.add("active");
    document.getElementById("tab-register").classList.remove("active");
    document.getElementById("auth-submit-btn").textContent = "로그인";
    document.getElementById("auth-error-msg").textContent = "";
  });
  document.getElementById("tab-register").addEventListener("click", () => {
    authMode = "register";
    document.getElementById("tab-register").classList.add("active");
    document.getElementById("tab-login").classList.remove("active");
    document.getElementById("auth-submit-btn").textContent = "회원가입";
    document.getElementById("auth-error-msg").textContent = "";
  });

  async function submitAuth() {
    const code     = document.getElementById("auth-code-input").value.trim();
    const pass     = document.getElementById("auth-pass-input").value;
    const errMsg   = document.getElementById("auth-error-msg");
    const btn      = document.getElementById("auth-submit-btn");
    if (!code || !pass) { errMsg.textContent = "코드와 비밀번호를 입력하세요."; return; }
    btn.disabled = true;
    errMsg.textContent = "";
    try {
      const url  = authMode === "login" ? "/auth/login" : "/auth/register";
      const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_code: code, password: pass }),
      });
      const data = await resp.json();
      if (data.ok) {
        localStorage.setItem("grocery_user_code", data.user_code);
        window.USER_ID = data.user_code;
        updateUserBar();
        hideAuth();
        if (onLogin) onLogin();
      } else if (data.reason === "already_exists") {
        errMsg.textContent = "이미 사용 중인 코드입니다. 다른 코드를 선택하세요.";
      } else {
        errMsg.textContent = authMode === "login" ? "코드 또는 비밀번호가 맞지 않습니다." : "오류가 발생했습니다.";
      }
    } catch {
      errMsg.textContent = "네트워크 오류가 발생했습니다.";
    }
    btn.disabled = false;
  }

  document.getElementById("auth-submit-btn").addEventListener("click", submitAuth);
  document.getElementById("auth-pass-input").addEventListener("keydown", e => {
    if (e.key === "Enter") submitAuth();
  });

  // Hamburger
  const hamBtn  = document.getElementById("g-hamburger-btn");
  const hamMenu = document.getElementById("g-hamburger-menu");
  if (hamBtn && hamMenu) {
    hamBtn.addEventListener("click", e => { e.stopPropagation(); hamMenu.classList.toggle("open"); });
    hamMenu.addEventListener("click", e => e.stopPropagation());
    document.addEventListener("click", () => hamMenu.classList.remove("open"));
  }

  // Logout
  const logoutBtn = document.getElementById("ghm-logout-btn");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", async () => {
      await fetch("/auth/logout", { method: "POST" });
      localStorage.removeItem("grocery_user_code");
      location.href = "/grocery";
    });
  }

  updateUserBar();
  if (!window.USER_ID) {
    showAuth();
  } else {
    // Verify the sid cookie is still valid. If not, force re-login.
    fetch("/auth/session").then(r => r.json()).then(data => {
      if (!data.user_id) {
        localStorage.removeItem("grocery_user_code");
        window.USER_ID = "";
        updateUserBar();
        showAuth();
      } else if (onLogin) {
        onLogin();
      }
    }).catch(() => { if (onLogin) onLogin(); });
  }
}
