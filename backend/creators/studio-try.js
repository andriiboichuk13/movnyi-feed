// «Спробуйте агента»: форма → запуск агента на ноутбуці команди (через ngrok) → живі кроки → звіт.
(function () {
  const $ = (id) => document.getElementById(id);
  const statusEl = $("agent-status"), form = $("try-form"), offline = $("try-offline");
  const ideaEl = $("idea"), submitBtn = $("try-submit"), errorEl = $("try-error");
  const progress = $("try-progress"), stepsEl = $("try-steps"), timerEl = $("try-timer");
  const result = $("try-result"), reportEl = $("try-report"), metaEl = $("try-result-meta");
  const HEADERS = { "ngrok-skip-browser-warning": "1" }; // без цього ngrok повертає сторінку-попередження
  let agentUrl = "";

  const escapeHtml = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function renderMarkdown(md) {
    if (!(window.marked && window.DOMPurify)) return `<pre>${escapeHtml(md)}</pre>`;
    const wrap = document.createElement("div");
    wrap.innerHTML = DOMPurify.sanitize(marked.parse(md));
    wrap.querySelectorAll("table").forEach((t) => {
      const w = document.createElement("div");
      w.className = "table-wrap";
      t.replaceWith(w);
      w.appendChild(t);
    });
    wrap.querySelectorAll("a[href]").forEach((a) => { a.target = "_blank"; a.rel = "noopener noreferrer"; });
    return wrap.innerHTML;
  }

  function setStatus(state, text) {
    statusEl.dataset.state = state;
    statusEl.textContent = text;
  }

  async function api(path, options = {}) {
    const res = await fetch(agentUrl + path, {
      ...options,
      headers: { ...HEADERS, ...(options.body ? { "Content-Type": "application/json" } : {}) },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(data.detail || `HTTP ${res.status}`), { status: res.status });
    return data;
  }

  async function checkAgent() {
    try {
      const cfg = await (await fetch("/api/studio/config")).json();
      agentUrl = cfg.agent_url;
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 8000);
      const res = await fetch(agentUrl + "/api/health", { headers: HEADERS, signal: ctrl.signal });
      clearTimeout(t);
      const h = await res.json();
      if (!h.ok) throw new Error("not ok");
      setStatus("online", "Агент онлайн");
      form.hidden = false;
    } catch (e) {
      setStatus("offline", "Агент офлайн");
      offline.hidden = false;
    }
  }

  function renderSteps(job) {
    const items = job.steps.map((s) => {
      const state = s.ok === null ? "pending" : String(s.ok);
      const mark = s.ok === null ? "…" : s.ok ? "✓" : "✗";
      return `<li data-ok="${state}"><span class="mark" aria-hidden="true">${mark}</span><span>${escapeHtml(s.label)}</span></li>`;
    });
    if (job.status === "queued") {
      items.push(`<li data-ok="pending"><span class="mark" aria-hidden="true">…</span><span>У черзі${job.ahead ? `: перед вами ${job.ahead}` : ""}. Агент почне за мить.</span></li>`);
    } else if (job.status === "running" && !job.steps.length) {
      items.push('<li data-ok="pending"><span class="mark" aria-hidden="true">…</span><span>Підключаюся до інструментів…</span></li>');
    } else if (job.status === "running") {
      items.push('<li data-ok="pending"><span class="mark" aria-hidden="true">…</span><span>Думаю над наступним кроком…</span></li>');
    }
    stepsEl.innerHTML = items.join("");
    timerEl.textContent = `${job.elapsed} с`;
  }

  async function poll(jobId) {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      renderSteps(job);
      if (job.status === "done") {
        timerEl.dataset.state = "online";
        timerEl.textContent = `Готово за ${job.elapsed} с`;
        metaEl.innerHTML = job.report_id
          ? `<a class="badge" href="/creators/studio#${job.report_id}">Постійне посилання на звіт</a>`
          : "";
        reportEl.innerHTML = renderMarkdown(job.report_md);
        result.hidden = false;
        result.scrollIntoView({ behavior: "smooth", block: "start" });
        submitBtn.disabled = false;
        return;
      }
      if (job.status === "error") {
        timerEl.dataset.state = "offline";
        timerEl.textContent = "Помилка";
        errorEl.textContent = job.error || "Агент не зміг завершити роботу.";
        errorEl.hidden = false;
        submitBtn.disabled = false;
        return;
      }
    } catch (e) {
      if (e.status === 404) {
        errorEl.textContent = "Запуск загубився (агент перезапускався). Спробуйте ще раз.";
        errorEl.hidden = false;
        submitBtn.disabled = false;
        return;
      }
    }
    setTimeout(() => poll(jobId), 2000);
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.hidden = true;
    const idea = ideaEl.value.trim();
    if (idea.length < 10) {
      errorEl.textContent = "Опишіть ідею хоча б одним реченням (від 10 символів).";
      errorEl.hidden = false;
      return;
    }
    submitBtn.disabled = true;
    try {
      const { job_id } = await api("/api/run", { method: "POST", body: JSON.stringify({ idea }) });
      $("try-idea").textContent = `Запит: «${idea}»`;
      result.hidden = true;
      progress.hidden = false;
      timerEl.dataset.state = "running";
      stepsEl.innerHTML = "";
      progress.scrollIntoView({ behavior: "smooth", block: "start" });
      poll(job_id);
    } catch (err) {
      errorEl.textContent = err.status === 429 ? err.message : "Не вдалося запустити агента. Спробуйте ще раз.";
      errorEl.hidden = false;
      submitBtn.disabled = false;
    }
  });

  document.querySelectorAll(".try-examples button").forEach((b) =>
    b.addEventListener("click", () => { ideaEl.value = b.dataset.idea; ideaEl.focus(); })
  );

  checkAgent();
})();
