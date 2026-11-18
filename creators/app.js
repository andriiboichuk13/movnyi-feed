const API_OVERVIEW = "/api/creators/overview";
const API_PROCESS = "/api/creators/process";

const charts = {};
let activeTokenFilter = "";
let debounceTimer = null;

const el = (id) => document.getElementById(id);

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function showToast(message) {
  const toast = el("toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => toast.classList.remove("show"), 3500);
}

function buildQueryParams() {
  const params = new URLSearchParams();
  const days = el("filter-days").value;
  const topicId = el("filter-topic").value;
  const level = el("filter-level").value;
  const q = el("filter-query").value.trim() || activeTokenFilter;

  if (days) params.set("days", days);
  if (topicId) params.set("topic_id", topicId);
  if (level !== "") params.set("level", level);
  if (q) params.set("q", q);
  return params.toString();
}

async function fetchOverview() {
  const pill = el("status-pill");
  try {
    const qs = buildQueryParams();
    const res = await fetch(qs ? `${API_OVERVIEW}?${qs}` : API_OVERVIEW);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    pill.textContent = "Supabase підключено";
    pill.classList.remove("error");
    renderAll(data);
  } catch (err) {
    console.error(err);
    pill.textContent = "Помилка з'єднання";
    pill.classList.add("error");
    showToast("Не вдалося завантажити аналітику з сервера.");
  }
}

function renderTopicSelect(availableTopics) {
  const select = el("filter-topic");
  const current = select.value;
  const options = ['<option value="">Усі теми</option>'];
  for (const t of availableTopics || []) {
    options.push(`<option value="${t.id}">${escapeHtml(t.name)}</option>`);
  }
  select.innerHTML = options.join("");
  if (current) select.value = current;
}

function renderKpis(summary) {
  el("kpi-total").textContent = summary.total_wishes ?? 0;
  el("kpi-processed").textContent = `Оброблено моделлю: ${summary.processed_wishes ?? 0}`;
  el("kpi-installs").textContent = summary.unique_installs ?? 0;
  el("kpi-with-text").textContent = `Із текстовим коментарем: ${summary.with_user_text ?? 0}`;
  el("kpi-unmet").textContent = summary.unmet_demand ?? 0;
  el("kpi-top-topic").textContent = summary.top_topic || "Недостатньо даних";
  const confPct = Math.round((summary.avg_confidence || 0) * 100);
  el("kpi-confidence").textContent = `Середня впевненість: ${confPct}%`;
}

function renderChart(key, canvasId, config) {
  if (charts[key]) {
    charts[key].destroy();
  }
  const ctx = el(canvasId).getContext("2d");
  charts[key] = new Chart(ctx, config);
}

const CHART_COLORS = [
  "#3b82f6",
  "#f59e0b",
  "#10b981",
  "#a855f7",
  "#ec4899",
  "#06b6d4",
  "#f97316",
  "#84cc16",
];

function renderCharts(data) {
  Chart.defaults.color = "#94a3b8";
  Chart.defaults.borderColor = "rgba(148, 163, 184, 0.12)";
  Chart.defaults.font.family = "'Inter', system-ui, sans-serif";

  const topics = data.topics || [];
  renderChart("topics", "chart-topics", {
    type: "bar",
    data: {
      labels: topics.map((t) => t.name),
      datasets: [
        {
          label: "Усього запитів",
          data: topics.map((t) => t.count),
          backgroundColor: "rgba(59, 130, 246, 0.78)",
          borderRadius: 6,
        },
        {
          label: "Без точної альтернативи",
          data: topics.map((t) => t.unmet_count),
          backgroundColor: "rgba(245, 158, 11, 0.78)",
          borderRadius: 6,
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "bottom" } },
      scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
    },
  });

  const timeline = data.timeline || [];
  renderChart("timeline", "chart-timeline", {
    type: "line",
    data: {
      labels: timeline.map((d) => d.date),
      datasets: [
        {
          label: "Запити за день",
          data: timeline.map((d) => d.count),
          borderColor: "#3b82f6",
          backgroundColor: "rgba(59, 130, 246, 0.16)",
          fill: true,
          tension: 0.3,
        },
        {
          label: "Незакритий попит",
          data: timeline.map((d) => d.unmet_count),
          borderColor: "#f59e0b",
          backgroundColor: "rgba(245, 158, 11, 0.12)",
          fill: true,
          tension: 0.3,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "bottom" } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
    },
  });

  const sentiments = data.sentiments || [];
  renderChart("sentiment", "chart-sentiment", {
    type: "doughnut",
    data: {
      labels: sentiments.map((s) => s.label),
      datasets: [
        {
          data: sentiments.map((s) => s.count),
          backgroundColor: CHART_COLORS,
          borderWidth: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "62%",
      plugins: { legend: { position: "bottom" } },
    },
  });

  const levels = data.levels || [];
  renderChart("levels", "chart-levels", {
    type: "doughnut",
    data: {
      labels: levels.map((l) => l.label),
      datasets: [
        {
          data: levels.map((l) => l.count),
          backgroundColor: ["#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#64748b"],
          borderWidth: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "62%",
      plugins: { legend: { position: "bottom" } },
    },
  });
}

function renderClusters(clusters) {
  el("clusters-count").textContent = `${clusters.length} груп`;
  const container = el("clusters-list");
  if (!clusters.length) {
    container.innerHTML = '<div class="empty-state">Поки немає записів для групування.</div>';
    return;
  }

  container.innerHTML = clusters
    .map((c) => {
      const samplesHtml = (c.sample_wishes || [])
        .map((w) => {
          const title = escapeHtml(w.video_title || w.video_id);
          const url = `https://www.youtube.com/watch?v=${encodeURIComponent(w.video_id)}`;
          const comment = w.user_text ? ` — «${escapeHtml(w.user_text)}»` : "";
          return `<div>• <a href="${url}" target="_blank" rel="noopener">${title}</a>${comment}</div>`;
        })
        .join("");

      return `
        <div class="cluster-item">
          <div class="cluster-top">
            <span class="cluster-title">${escapeHtml(c.label)}</span>
            <div class="cluster-stats">
              <span class="badge">${c.count} запит(ів)</span>
              <span class="badge">${Math.round((c.avg_confidence || 0) * 100)}%</span>
            </div>
          </div>
          <div class="cluster-samples">${samplesHtml}</div>
        </div>
      `;
    })
    .join("");
}

function renderTokens(tokens) {
  const container = el("tokens-cloud");
  if (!tokens.length) {
    container.innerHTML = '<div class="empty-state">Токени не знайдено.</div>';
    return;
  }

  container.innerHTML = tokens
    .map((t) => {
      const isActive = activeTokenFilter.toLowerCase() === t.token.toLowerCase();
      const group = escapeHtml(t.entity_group || "KEY");
      return `
        <button type="button" class="token-chip ${isActive ? "active" : ""}" data-token="${escapeHtml(t.token)}">
          <span class="tok-badge tok-${group}">${group}</span>
          <span>${escapeHtml(t.token)}</span>
          <span class="tok-count">×${t.count}</span>
        </button>
      `;
    })
    .join("");

  container.querySelectorAll(".token-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      const token = btn.dataset.token || "";
      if (activeTokenFilter.toLowerCase() === token.toLowerCase()) {
        activeTokenFilter = "";
        el("filter-query").value = "";
      } else {
        activeTokenFilter = token;
        el("filter-query").value = token;
      }
      fetchOverview();
    });
  });
}

function renderWishesTable(wishes) {
  el("wishes-count").textContent = `${wishes.length} записів`;
  const tbody = el("wishes-tbody");
  if (!wishes.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-state">Записів за обраними фільтрами не знайдено.</td></tr>';
    return;
  }

  tbody.innerHTML = wishes
    .map((w) => {
      const dateStr = w.created_at ? new Date(w.created_at).toLocaleString("uk-UA", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }) : "—";
      const url = `https://www.youtube.com/watch?v=${encodeURIComponent(w.video_id)}`;
      const title = escapeHtml(w.video_title || w.video_id);
      const userText = w.user_text ? escapeHtml(w.user_text) : '<span class="sub-text">без тексту</span>';
      const confPct = Math.round((w.topic_confidence || 0) * 100);
      const tokensHtml = (w.tokens || [])
        .map((tok) => `<span class="tok-badge tok-${escapeHtml(tok.entity_group)}">${escapeHtml(tok.token)}</span>`)
        .join("");

      return `
        <tr>
          <td>${escapeHtml(dateStr)}</td>
          <td>
            <a class="video-link" href="${url}" target="_blank" rel="noopener">${title}</a>
            <div class="sub-text">${escapeHtml(w.video_id)}</div>
          </td>
          <td>${userText}</td>
          <td>
            <div><strong>${escapeHtml(w.topic_name || "—")}</strong></div>
            <div class="sub-text">${escapeHtml(w.subtopic_name || "")} · ${confPct}%</div>
          </td>
          <td><div class="inline-tokens">${tokensHtml || '<span class="sub-text">—</span>'}</div></td>
          <td><span class="pill-sentiment">${escapeHtml(w.sentiment || "—")}</span></td>
          <td>${w.level_reached ?? "—"}</td>
        </tr>
      `;
    })
    .join("");
}

function renderSearchSuggestion(suggestion) {
  const banner = el("search-suggestion");
  if (!suggestion) {
    banner.hidden = true;
    banner.innerHTML = "";
    return;
  }
  const simPct = Math.round((suggestion.similarity || 0) * 100);
  banner.hidden = false;
  banner.innerHTML = `Точних збігів за запитом «<strong>${escapeHtml(suggestion.query)}</strong>» не знайдено. Показано найближчу за назвою тему: <strong>${escapeHtml(suggestion.matched_topic_name)}</strong> (синтаксична схожість: ${simPct}%).`;
}

function renderAll(data) {
  renderSearchSuggestion(data.search_suggestion || null);
  renderTopicSelect(data.available_topics || []);
  renderKpis(data.summary || {});
  renderCharts(data);
  renderClusters(data.clusters || []);
  renderTokens(data.tokens || []);
  renderWishesTable(data.wishes || []);
}

async function processWishes() {
  const btn = el("btn-process");
  btn.disabled = true;
  btn.classList.add("loading");
  try {
    const res = await fetch(API_PROCESS, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force: false }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.overview) renderAll(data.overview);
    showToast(`AI-аналіз завершено: оновлено ${data.processed} записів.`);
  } catch (err) {
    console.error(err);
    showToast("Помилка під час запуску AI-аналізу.");
  } finally {
    btn.disabled = false;
    btn.classList.remove("loading");
  }
}

el("filter-days").addEventListener("change", fetchOverview);
el("filter-topic").addEventListener("change", fetchOverview);
el("filter-level").addEventListener("change", fetchOverview);
el("filter-query").addEventListener("input", () => {
  activeTokenFilter = "";
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(fetchOverview, 260);
});
el("btn-reset").addEventListener("click", () => {
  el("filter-days").value = "";
  el("filter-topic").value = "";
  el("filter-level").value = "";
  el("filter-query").value = "";
  activeTokenFilter = "";
  fetchOverview();
});
el("btn-process").addEventListener("click", processWishes);

fetchOverview();
