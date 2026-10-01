// Shared auth, hamburger, and utility code for grocery/pantry/trash pages.
// Include this script before each page's own <script> block.
// Usage: initCommonUI({ onLogin: () => myLoadFn() });

window.USER_ID = localStorage.getItem("grocery_user_code") || "";

function fmtDate(str) {
  if (!str) return "-";
  const d = new Date(str.slice(0, 10) + "T00:00:00");
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

const _AUTH_EXTRA_CSS = `
  .auth-link-row { text-align: center; margin-top: 12px; }
  .auth-link {
    background: none; border: none; padding: 4px; cursor: pointer; font-family: inherit;
    font-size: 0.8rem; color: #8d8474; text-decoration: underline; text-underline-offset: 3px;
  }
  .auth-link:hover { color: #b3402e; }
  .auth-mode-title { font-size: 0.95rem; font-weight: 700; color: #26221c; margin-bottom: 4px; }
  .auth-mode-desc { font-size: 0.8rem; color: #6f6759; line-height: 1.55; margin-bottom: 14px; }
  .rc-box {
    font-family: "SF Mono", ui-monospace, Menlo, Consolas, monospace;
    font-size: 1.15rem; font-weight: 700; letter-spacing: 0.06em; text-align: center;
    color: #26221c; background: #efece5; border: 2px dashed #cfc7b6; border-radius: 3px;
    padding: 14px 8px; margin: 12px 0; user-select: all; word-break: break-all;
  }
  .rc-copy {
    display: block; width: 100%; padding: 9px; margin-bottom: 8px; cursor: pointer;
    background: none; border: 1px solid #ddd6c8; border-radius: 3px;
    font-family: inherit; font-size: 0.85rem; color: #5d5548;
  }
  .rc-copy:hover { background: #ece7db; }
  .rc-done {
    display: block; width: 100%; padding: 12px; border: none; border-radius: 3px; cursor: pointer;
    background: #b3402e; color: #faf8f3; font-family: inherit; font-size: 0.95rem; font-weight: 600;
  }
  .rc-done:hover { background: #9a3527; }
`;

function _copyText(text, btn) {
  const done = () => { btn.textContent = "복사됨"; setTimeout(() => { btn.textContent = "복사하기"; }, 1500); };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done).catch(() => {});
  } else {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); done(); } catch {}
    ta.remove();
  }
}

// 복구 코드는 다시 볼 수 없으므로 "저장했어요"를 눌러야 다음으로 넘어간다.
function renderRecoveryCodePanel(container, code, { title, onDone }) {
  container.innerHTML = "";
  const t = document.createElement("p");
  t.className = "auth-mode-title";
  t.textContent = title;
  const d = document.createElement("p");
  d.className = "auth-mode-desc";
  d.textContent = "비밀번호를 잊었을 때 이 코드로 재설정할 수 있어요. 지금 한 번만 보여드리니 메모나 사진으로 꼭 저장해 두세요.";
  const box = document.createElement("div");
  box.className = "rc-box";
  box.textContent = code;
  const copy = document.createElement("button");
  copy.className = "rc-copy";
  copy.type = "button";
  copy.textContent = "복사하기";
  copy.addEventListener("click", () => _copyText(code, copy));
  const ok = document.createElement("button");
  ok.id = "rc-done-btn";
  ok.type = "button";
  ok.className = "rc-done";
  ok.textContent = "저장했어요";
  ok.addEventListener("click", onDone);
  container.append(t, d, box, copy, ok);
}

function initCommonUI({ onLogin } = {}) {
  let authMode = "login";

  const modal    = document.getElementById("auth-modal");
  const tabs     = modal.querySelector(".auth-tabs");
  const codeIn   = document.getElementById("auth-code-input");
  const passIn   = document.getElementById("auth-pass-input");
  const submitBtn = document.getElementById("auth-submit-btn");
  const errMsg   = document.getElementById("auth-error-msg");
  const hint     = modal.querySelector(".auth-hint");

  const style = document.createElement("style");
  style.textContent = _AUTH_EXTRA_CSS;
  document.head.appendChild(style);

  // 복구 모드 안내 + 복구 코드 입력칸 (아이디 입력칸 바로 아래)
  const recoverHead = document.createElement("div");
  recoverHead.style.display = "none";
  recoverHead.innerHTML = `<p class="auth-mode-title">비밀번호 재설정</p>
    <p class="auth-mode-desc">가입할 때 받은 복구 코드를 입력하고 새 비밀번호를 정하세요.</p>`;
  tabs.after(recoverHead);

  const rcIn = document.createElement("input");
  rcIn.id = "auth-recovery-input";
  rcIn.className = "auth-field";
  rcIn.type = "text";
  rcIn.placeholder = "복구 코드 (예: AB12-CD34-…)";
  rcIn.maxLength = 64;
  rcIn.autocomplete = "off";
  rcIn.autocapitalize = "characters";
  rcIn.spellcheck = false;
  rcIn.style.display = "none";
  codeIn.after(rcIn);

  const linkRow = document.createElement("p");
  linkRow.className = "auth-link-row";
  const modeLink = document.createElement("button");
  modeLink.type = "button";
  modeLink.className = "auth-link";
  linkRow.appendChild(modeLink);
  errMsg.after(linkRow);

  const formEls = [...modal.children];
  const resultPanel = document.createElement("div");
  resultPanel.style.display = "none";
  modal.appendChild(resultPanel);

  function setMode(mode) {
    authMode = mode;
    const recover = mode === "recover";
    document.getElementById("tab-login").classList.toggle("active", mode === "login");
    document.getElementById("tab-register").classList.toggle("active", mode === "register");
    tabs.style.display = recover ? "none" : "";
    recoverHead.style.display = recover ? "" : "none";
    rcIn.style.display = recover ? "" : "none";
    if (hint) hint.style.display = recover ? "none" : "";
    passIn.placeholder = {
      login: "비밀번호",
      register: "비밀번호 (8자 이상, 대·소문자 포함)",
      recover: "새 비밀번호 (8자 이상, 대·소문자 포함)",
    }[mode];
    passIn.autocomplete = mode === "login" ? "current-password" : "new-password";
    submitBtn.textContent = { login: "로그인", register: "회원가입", recover: "비밀번호 재설정" }[mode];
    linkRow.style.display = mode === "register" ? "none" : "";
    modeLink.textContent = recover ? "← 로그인으로 돌아가기" : "비밀번호를 잊으셨나요?";
    errMsg.textContent = "";
  }

  document.getElementById("tab-login").addEventListener("click", () => setMode("login"));
  document.getElementById("tab-register").addEventListener("click", () => setMode("register"));
  modeLink.addEventListener("click", () => setMode(authMode === "recover" ? "login" : "recover"));
  setMode("login");

  function finishLogin(userCode) {
    localStorage.setItem("grocery_user_code", userCode);
    window.USER_ID = userCode;
    updateUserBar();
    hideAuth();
    resultPanel.style.display = "none";
    formEls.forEach(el => { el.style.display = el.dataset.prevDisplay ?? ""; });
    setMode("login");
    passIn.value = "";
    rcIn.value = "";
    if (typeof initPushNotifications === "function") initPushNotifications();
    if (onLogin) onLogin();
  }

  function showRecoveryCode(userCode, code, title) {
    formEls.forEach(el => { el.dataset.prevDisplay = el.style.display; el.style.display = "none"; });
    resultPanel.style.display = "";
    renderRecoveryCodePanel(resultPanel, code, { title, onDone: () => finishLogin(userCode) });
  }

  async function submitAuth() {
    const code = codeIn.value.trim();
    const pass = passIn.value;
    const rc   = rcIn.value.trim();
    if (!code || !pass || (authMode === "recover" && !rc)) {
      errMsg.textContent = authMode === "recover"
        ? "코드, 복구 코드, 새 비밀번호를 모두 입력하세요."
        : "코드와 비밀번호를 입력하세요.";
      return;
    }
    submitBtn.disabled = true;
    errMsg.textContent = "";
    try {
      const url = { login: "/auth/login", register: "/auth/register", recover: "/auth/recover" }[authMode];
      const body = authMode === "recover"
        ? { user_code: code, recovery_code: rc, new_password: pass }
        : { user_code: code, password: pass };
      const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (resp.status === 429) {
        errMsg.textContent = "시도 횟수가 너무 많아요. 15분 뒤에 다시 시도하세요.";
      } else if (resp.status === 422) {
        errMsg.textContent = "비밀번호는 8자 이상이고 대문자와 소문자를 모두 포함해야 해요.";
      } else {
        const data = await resp.json();
        if (data.ok && data.recovery_code) {
          showRecoveryCode(data.user_code, data.recovery_code,
            authMode === "recover" ? "비밀번호를 바꿨어요. 새 복구 코드를 저장하세요" : "가입 완료! 복구 코드를 저장하세요");
        } else if (data.ok) {
          finishLogin(data.user_code);
        } else if (data.reason === "already_exists") {
          errMsg.textContent = "이미 사용 중인 코드입니다. 다른 코드를 선택하세요.";
        } else if (authMode === "recover") {
          errMsg.textContent = "코드 또는 복구 코드가 맞지 않습니다.";
        } else if (authMode === "login") {
          errMsg.textContent = "코드 또는 비밀번호가 맞지 않습니다.";
        } else {
          errMsg.textContent = "가입하지 못했어요. 잠시 뒤 다시 시도하세요.";
        }
      }
    } catch {
      errMsg.textContent = "네트워크 오류가 발생했습니다.";
    }
    submitBtn.disabled = false;
  }

  submitBtn.addEventListener("click", submitAuth);
  passIn.addEventListener("keydown", e => {
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
      } else {
        if (typeof initPushNotifications === "function") initPushNotifications();
        if (onLogin) onLogin();
      }
    }).catch(() => { if (onLogin) onLogin(); });
  }
}
