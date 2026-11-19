const API_OVERVIEW = "/api/creators/overview";

const charts = {};
let activeSubjectFilter = "";
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
  const q = el("filter-query").value.trim() || activeSubjectFilter;

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
    pill.textContent = "Дані актуальні";
    pill.classList.remove("error");
    renderAll(data);
  } catch (err) {
    console.error(err);
    pill.textContent = "Немає зв'язку";
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

function renderKpis(summary, topics) {
  const total = summary.total_wishes ?? 0;
  const unmet = summary.unmet_demand ?? 0;
  const unmetPct = total > 0 ? Math.round((unmet / total) * 100) : 0;

  el("kpi-total").textContent = total;
  el("kpi-with-text").textContent = `З детальним коментарем: ${summary.with_user_text ?? 0}`;
  el("kpi-installs").textContent = summary.unique_installs ?? 0;
  el("kpi-unmet").textContent = unmet;
  el("kpi-unmet-share").textContent = `${unmetPct}% без української альтернативи`;
  el("kpi-top-topic").textContent = summary.top_topic || "Недостатньо даних";
  const topTopicObj = (topics || [])[0];
  el("kpi-top-share").textContent = topTopicObj
    ? `${topTopicObj.share}% від усіх запитів · дефіцит ${topTopicObj.unmet_rate}%`
    : "Найвищий попит аудиторії";
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
          label: "Бракує укр. відео",
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
          label: "Усі запити",
          data: timeline.map((d) => d.count),
          borderColor: "#3b82f6",
          backgroundColor: "rgba(59, 130, 246, 0.16)",
          fill: true,
          tension: 0.3,
        },
        {
          label: "Дефіцит укр. контенту",
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
          backgroundColor: ["#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6", "#64748b"],
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

function renderTopicsDeepDive(topics) {
  el("topics-count").textContent = `${topics.length} тем`;
  const container = el("topics-deep-grid");
  if (!topics.length) {
    container.innerHTML = '<div class="empty-state">За обраними фільтрами тем не знайдено.</div>';
    return;
  }

  container.innerHTML = topics
    .map((t) => {
      const subtopicsHtml = (t.subtopics || [])
        .map((s) => `<span class="subtopic-pill">${escapeHtml(s.name)} · <strong>${s.count}</strong> (${s.share}%)</span>`)
        .join("");

      const subjectsHtml = (t.key_subjects || [])
        .map((s) => {
          const isActive = activeSubjectFilter.toLowerCase() === s.name.toLowerCase();
          return `<button type="button" class="subject-chip ${isActive ? "active" : ""}" data-subject="${escapeHtml(s.name)}">${escapeHtml(s.name)} ×${s.count}</button>`;
        })
        .join("");

      const sentimentsHtml = (t.sentiments || [])
        .map((s) => `<span class="pill-sentiment">${escapeHtml(s.label)}: ${s.share}%</span>`)
        .join("");

      const narrativesHtml = (t.narratives || [])
        .map((n) => {
          const videosHtml = (n.sample_videos || [])
            .map((v) => {
              const url = `https://www.youtube.com/watch?v=${encodeURIComponent(v.video_id)}`;
              return `<div>• <a href="${url}" target="_blank" rel="noopener">${escapeHtml(v.video_title || v.video_id)}</a></div>`;
            })
            .join("");

          return `
            <div class="narrative-box">
              <div class="narrative-head">
                <span class="narrative-title">${escapeHtml(n.title)}</span>
                <span class="badge">${n.demand} запит(ів) · дефіцит ${n.unmet_rate}%</span>
              </div>
              <div class="narrative-meta">Намір: ${escapeHtml(n.viewer_intent)}</div>
              <div class="narrative-videos">${videosHtml}</div>
            </div>
          `;
        })
        .join("");

      return `
        <article class="topic-deep-card">
          <div class="topic-deep-head">
            <div>
              <div class="topic-deep-title">${escapeHtml(t.name)}</div>
              <div class="topic-deep-metrics">
                <span>Запитів: <strong>${t.count}</strong> (${t.share}%)</span>
                <span>Глядачів: <strong>${t.unique_viewers ?? 0}</strong></span>
              </div>
            </div>
            <div class="pill-row">
              <span class="badge badge-opportunity">Можливість: ${t.opportunity_score ?? 0}/100</span>
              <span class="badge badge-deficit">Дефіцит: ${t.unmet_rate ?? 0}%</span>
            </div>
          </div>

          ${subtopicsHtml ? `<div><div class="topic-section-label">Підтеми</div><div class="pill-row">${subtopicsHtml}</div></div>` : ""}

          ${subjectsHtml ? `<div><div class="topic-section-label">Що саме шукають у цій темі</div><div class="pill-row">${subjectsHtml}</div></div>` : ""}

          ${sentimentsHtml ? `<div><div class="topic-section-label">Реакція глядачів</div><div class="pill-row">${sentimentsHtml}</div></div>` : ""}

          <div>
            <div class="topic-section-label">Наративи та ідеї для відео</div>
            <div class="narratives-list">${narrativesHtml || '<div class="sub-text">Недостатньо даних</div>'}</div>
          </div>
        </article>
      `;
    })
    .join("");

  container.querySelectorAll(".subject-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      const subj = btn.dataset.subject || "";
      if (activeSubjectFilter.toLowerCase() === subj.toLowerCase()) {
        activeSubjectFilter = "";
        el("filter-query").value = "";
      } else {
        activeSubjectFilter = subj;
        el("filter-query").value = subj;
      }
      fetchOverview();
    });
  });
}

function renderWishesTable(wishes) {
  el("wishes-count").textContent = `${wishes.length} записів`;
  const tbody = el("wishes-tbody");
  if (!wishes.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-state">Записів за обраними фільтрами не знайдено.</td></tr>';
    return;
  }

  tbody.innerHTML = wishes
    .map((w) => {
      const dateStr = w.created_at
        ? new Date(w.created_at).toLocaleString("uk-UA", {
            day: "2-digit",
            month: "2-digit",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          })
        : "—";
      const url = `https://www.youtube.com/watch?v=${encodeURIComponent(w.video_id)}`;
      const title = escapeHtml(w.video_title || "Відео без назви");

      return `
        <tr>
          <td>${escapeHtml(dateStr)}</td>
          <td>
            <a class="video-link" href="${url}" target="_blank" rel="noopener">${title}</a>
          </td>
          <td>
            <div><strong>${escapeHtml(w.topic_name || "—")}</strong></div>
            <div class="sub-text">${escapeHtml(w.subtopic_name || "")}</div>
          </td>
          <td><span class="pill-sentiment">${escapeHtml(w.sentiment || "—")}</span></td>
          <td><span class="sub-text">${escapeHtml(w.level_label || "—")}</span></td>
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
  banner.innerHTML = `Точних збігів за запитом «<strong>${escapeHtml(suggestion.query)}</strong>» не знайдено. Показано найближчу за назвою тему: <strong>${escapeHtml(suggestion.matched_topic_name)}</strong> (схожість: ${simPct}%).`;
}

function renderAll(data) {
  renderSearchSuggestion(data.search_suggestion || null);
  renderTopicSelect(data.available_topics || []);
  renderKpis(data.summary || {}, data.topics || []);
  renderCharts(data);
  renderTopicsDeepDive(data.topics || []);
  renderWishesTable(data.wishes || []);
}

el("filter-days").addEventListener("change", fetchOverview);
el("filter-topic").addEventListener("change", fetchOverview);
el("filter-level").addEventListener("change", fetchOverview);
el("filter-query").addEventListener("input", () => {
  activeSubjectFilter = "";
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(fetchOverview, 260);
});
el("btn-reset").addEventListener("click", () => {
  el("filter-days").value = "";
  el("filter-topic").value = "";
  el("filter-level").value = "";
  el("filter-query").value = "";
  activeSubjectFilter = "";
  fetchOverview();
});

fetchOverview();
