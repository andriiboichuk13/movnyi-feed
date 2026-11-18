// Запускає справжній background.js і друкує JSON усіх HTTP-запитів, які розширення шле бекенду.
// Використовується backend/tests/test_real_server_contract.py: ці запити проганяються через справжній server.py.
const { loadBackground } = require("../helpers/load-background");

(async () => {
  const bg = loadBackground({
    storage: { shareKidsStats: true, kidsHidden: { "2026-11-13": 9 } },
    fetchImpl: ({ body }) => ({ status: 200, body: body && body.videoIds ? { results: {} } : {} }),
  });
  await bg.install();
  const V = "dQw4w9WgXcQ";
  await bg.send({ type: "alternatives", videoId: V });
  await bg.storage.local.set({ kidsMode: true });
  await bg.send({ type: "alternatives", videoId: V });
  await bg.send({ type: "classify", videoIds: Array.from({ length: 120 }, (_, i) => `id${String(i).padStart(9, "0")}`) });
  await bg.send({ type: "wish", videoId: V, videoTitle: "Назва", level: 4, userText: "дитячий режим: текст має зникнути" });
  await bg.storage.local.set({ kidsMode: false });
  await bg.send({ type: "wish", videoId: V, videoTitle: "Н".repeat(100), level: 5, userText: "я".repeat(400) });
  for (const eventType of ["panel_shown", "alt_clicked", "wish_sent"]) await bg.send({ type: "event", eventType, level: 3 });
  await bg.send({ type: "videoActions", items: Array.from({ length: 230 }, (_, i) => ({ action: i % 2 ? "hid" : "suggested", videoId: `id${String(i).padStart(9, "0")}` })) });
  await bg.alarm();
  const strip = (c) => ({ method: c.method, url: c.url.replace(/^https?:\/\/[^/]+/, ""), body: c.body });
  console.log(JSON.stringify(bg.fetchCalls.map(strip)));
})();
