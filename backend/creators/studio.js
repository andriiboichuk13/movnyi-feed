// Сторінка «Студія ідей»: список опублікованих звітів агента і перегляд обраного.
(function () {
  const listEl = document.getElementById("report-list");
  const bodyEl = document.getElementById("report-body");
  const metaEl = document.getElementById("report-meta");

  const escapeHtml = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const formatDate = (iso) =>
    new Date(iso).toLocaleString("uk-UA", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });

  function renderMarkdown(md) {
    // Звіт містить назви відео з YouTube, тож HTML обов'язково очищаємо (DOMPurify).
    if (window.marked && window.DOMPurify) {
      const html = DOMPurify.sanitize(marked.parse(md));
      const wrap = document.createElement("div");
      wrap.innerHTML = html;
      wrap.querySelectorAll("table").forEach((t) => {
        const w = document.createElement("div");
        w.className = "table-wrap";
        t.replaceWith(w);
        w.appendChild(t);
      });
      wrap.querySelectorAll("a[href]").forEach((a) => {
        a.target = "_blank";
        a.rel = "noopener noreferrer";
      });
      return wrap.innerHTML;
    }
    return `<pre>${escapeHtml(md)}</pre>`; // запасний варіант, якщо бібліотеки не завантажились
  }

  async function showReport(id) {
    bodyEl.innerHTML = '<p class="muted">Завантаження звіту…</p>';
    listEl.querySelectorAll("button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.id === String(id))));
    try {
      const res = await fetch(`/api/studio/reports/${encodeURIComponent(id)}`);
      if (!res.ok) throw new Error(res.status);
      const r = await res.json();
      metaEl.innerHTML = [
        r.game ? `<span class="badge">${escapeHtml(r.game)}</span>` : "",
        `<span class="badge">Креатор: ${escapeHtml(r.creator || "—")}</span>`,
        `<span class="badge">${escapeHtml(formatDate(r.created_at))}</span>`,
        r.model ? `<span class="badge">Модель: ${escapeHtml(r.model)}</span>` : "",
      ].join("");
      bodyEl.innerHTML =
        `<p class="muted">Запит креатора: «${escapeHtml(r.idea)}»</p>` + renderMarkdown(r.report_md);
      if (location.hash !== `#${id}`) history.replaceState(null, "", `#${id}`);
    } catch (e) {
      bodyEl.innerHTML = '<p class="muted">Не вдалося завантажити звіт. Спробуйте оновити сторінку.</p>';
    }
  }

  async function init() {
    try {
      const res = await fetch("/api/studio/reports?limit=50");
      if (!res.ok) throw new Error(res.status);
      const { reports } = await res.json();
      if (!reports.length) {
        listEl.innerHTML = '<li class="muted">Звітів ще немає.</li>';
        bodyEl.innerHTML = '<p class="muted">Щойно агент опублікує перший звіт, він з\'явиться тут.</p>';
        return;
      }
      listEl.innerHTML = reports
        .map(
          (r) => `<li><button type="button" data-id="${r.id}">
              <span class="r-idea">${escapeHtml(r.idea)}</span>
              <span class="r-meta">${escapeHtml(r.game || "")} · ${escapeHtml(formatDate(r.created_at))}</span>
            </button></li>`
        )
        .join("");
      listEl.addEventListener("click", (e) => {
        const b = e.target.closest("button[data-id]");
        if (b) showReport(b.dataset.id);
      });
      const fromHash = location.hash.slice(1);
      showReport(reports.some((r) => String(r.id) === fromHash) ? fromHash : reports[0].id);
    } catch (e) {
      listEl.innerHTML = '<li class="muted">Не вдалося завантажити список звітів.</li>';
    }
  }

  window.addEventListener("hashchange", () => {
    const id = location.hash.slice(1);
    if (id) showReport(id);
  });
  init();
})();
