// Мінімальна імітація chrome.* для тестів без браузера.
const EventEmitter = () => {
  const listeners = [];
  return { addListener: (fn) => listeners.push(fn), listeners, emit: (...a) => listeners.map((fn) => fn(...a)) };
};

const clone = (x) => (x === undefined ? x : JSON.parse(JSON.stringify(x)));

function makeStorage(initial = {}) {
  const data = { ...initial };
  const onChanged = EventEmitter();
  const pick = (keys) => {
    if (keys === null || keys === undefined) return { ...data };
    if (typeof keys === "string") return keys in data ? { [keys]: data[keys] } : {};
    if (Array.isArray(keys)) return Object.fromEntries(keys.filter((k) => k in data).map((k) => [k, data[k]]));
    // об'єкт зі значеннями за замовчуванням
    return Object.fromEntries(Object.entries(keys).map(([k, def]) => [k, k in data ? data[k] : def]));
  };
  return {
    data,
    onChanged,
    local: {
      async get(keys) { return clone(pick(keys)); },
      async set(obj) {
        const changes = {};
        for (const [k, v] of Object.entries(obj)) {
          changes[k] = { oldValue: data[k], newValue: v };
          data[k] = clone(v);
        }
        onChanged.emit(changes, "local");
      },
      async remove(keys) { for (const k of [].concat(keys)) delete data[k]; },
    },
  };
}

function makeChrome(opts = {}) {
  const storage = makeStorage(opts.storage);
  const onMessage = EventEmitter();
  const onInstalled = EventEmitter();
  const onAlarm = EventEmitter();
  const alarmsCreated = [];
  const sent = []; // усе, що content/popup надіслали через runtime.sendMessage
  const chrome = {
    storage: { local: storage.local, onChanged: storage.onChanged },
    runtime: {
      onMessage, onInstalled,
      sendMessage: async (msg) => {
        sent.push(msg);
        if (opts.sendMessage) return opts.sendMessage(msg);
        return undefined;
      },
    },
    alarms: { create: (name, info) => alarmsCreated.push({ name, info }), onAlarm },
    tabs: opts.tabs || { query: async () => [], sendMessage: async () => ({ ok: true }) },
  };
  if (opts.detectLanguage) chrome.i18n = { detectLanguage: opts.detectLanguage };
  return { chrome, storage, sent, alarmsCreated, onMessage, onInstalled, onAlarm };
}

module.exports = { makeChrome, makeStorage };
