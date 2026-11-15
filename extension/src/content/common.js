// =====================================================================
// СПІЛЬНІ ДОПОМІЖНІ ФУНКЦІЇ для content script.
// =====================================================================

const MF = {
  // Надіслати повідомлення в background. Ніколи не кидає помилку:
  // повертає { ok: false, error } якщо щось пішло не так
  // (наприклад, розширення перезавантажили, а вкладку ні).
  async send(type, payload = {}) {
    try {
      const res = await chrome.runtime.sendMessage({ type, ...payload });
      return res || { ok: false, error: "no response" };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },

  async settings() {
    try {
      return await chrome.storage.local.get(MF_DEFAULTS);
    } catch (e) {
      return { ...MF_DEFAULTS, enabled: false };
    }
  },

  // Перший елемент за списком селекторів.
  qs(root, selectorList) {
    for (const sel of selectorList) {
      const el = root.querySelector(sel);
      if (el) return el;
    }
    return null;
  },

  // Перший ВИДИМИЙ елемент за списком селекторів.
  qsVisible(root, selectorList) {
    for (const sel of selectorList) {
      for (const el of root.querySelectorAll(sel)) {
        if (el.offsetParent !== null || el.getClientRects().length > 0) return el;
      }
    }
    return null;
  },

  // Чекати, доки функція поверне щось truthy (або таймаут → null).
  waitFor(fn, timeoutMs = 5000, stepMs = 100) {
    return new Promise((resolve) => {
      const start = Date.now();
      const tick = () => {
        let v = null;
        try {
          v = fn();
        } catch (e) {
          v = null;
        }
        if (v) return resolve(v);
        if (Date.now() - start >= timeoutMs) return resolve(null);
        setTimeout(tick, stepMs);
      };
      tick();
    });
  },

  // videoId з посилання: /watch?v=ID, /shorts/ID, youtu.be/ID
  videoIdFromHref(href) {
    if (!href) return null;
    try {
      const u = new URL(href, location.origin);
      if (u.pathname === "/watch") return u.searchParams.get("v");
      const shorts = u.pathname.match(/^\/shorts\/([\w-]{11})/);
      if (shorts) return shorts[1];
      if (u.hostname === "youtu.be") return u.pathname.slice(1, 12) || null;
    } catch (e) {
      /* ignore */
    }
    return null;
  },

  // videoId сторінки перегляду (лише /watch, не shorts).
  currentWatchVideoId() {
    if (location.pathname !== "/watch") return null;
    return new URLSearchParams(location.search).get("v");
  },

  isDarkTheme() {
    return document.documentElement.hasAttribute("dark");
  },

  thumbUrl(videoId) {
    return `https://i.ytimg.com/vi/${encodeURIComponent(videoId)}/mqdefault.jpg`;
  },

  // Маленький хелпер для створення DOM без innerHTML (безпечно для чужих назв).
  el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of [].concat(children)) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  },
};
