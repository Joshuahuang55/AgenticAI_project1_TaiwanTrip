// Behavioral checks for the plain frontend without CDN, browser or model requests.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { test } = require("node:test");
const path = require("node:path");

class Element {
    constructor(tag) {
        this.tag = tag; this.children = []; this.listeners = {}; this.hidden = true;
        this.value = ""; this.text = ""; this.disabled = false;
        this.style = { setProperty() {} };
        this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
    }
    set textContent(value) { this.text = String(value); this.children = []; }
    get textContent() { return this.text + this.children.map((child) => child.textContent).join(" "); }
    set innerHTML(value) { this.textContent = value; }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.append(child); }
    prepend(child) { this.children.unshift(child); }
    replaceChildren(...children) { this.text = ""; this.children = children; }
    insertBefore(child) { this.append(child); }
    addEventListener(event, handler) { this.listeners[event] = handler; }
    remove() {}
    focus() {}
}

function harness(saved = {}, customFetch, vector = false, timers = {}) {
    const elements = new Map();
    const get = (id) => {
        if (!elements.has(id)) elements.set(id, new Element("div"));
        return elements.get(id);
    };
    const examples = [new Element("button"), new Element("button")];
    const storage = new Map(Object.entries(saved));
    const layer = { items: [], addTo() { return this; }, getLayers() { return this.items; },
        removeLayer(marker) { this.items = this.items.filter((item) => item !== marker); }, clearLayers() { this.items = []; } };
    const map = { setView() { return this; }, fitBounds() {}, invalidateSize() {}, removeLayer() {} };
    let vectorStyle;
    const leaflet = { map: () => map, tileLayer: () => ({ addTo() { return this; } }), layerGroup: () => layer,
        featureGroup: () => ({ getBounds: () => ({ pad() {} }) }),
        circleMarker: (position) => ({ position, bindPopup(popup) { this.popup = popup; return this; },
            addTo(target) { target.items.push(this); return this; } }) };
    if (vector) leaflet.maplibreGL = ({ style }) => {
        vectorStyle = style;
        return { addTo() { return this; }, getMaplibreMap: () => ({
            once(_, handler) { if (!vector.stall) handler(); }, on() {}, loaded: () => false,
        }) };
    };
    const context = vm.createContext({
        console, AbortController, AbortSignal, setTimeout: timers.setTimeout || setTimeout,
        clearTimeout: timers.clearTimeout || clearTimeout, crypto: require("node:crypto").webcrypto,
        document: { head: { appendChild(node) { if (node.tag === "script") node.onerror(); } },
            getElementById: get, createElement: (tag) => new Element(tag),
            createTextNode: (text) => Object.assign(new Element("text"), { text }), querySelectorAll: () => examples },
        sessionStorage: { getItem: (key) => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
        location: { reload() {} }, window: vector ? { maplibregl: {} } : {},
        DOMPurify: { sanitize: (text) => text }, marked: { parse: (text) => text },
        fetch: customFetch || (async () => ({ ok: true, json: async () => ({ status: "active" }) })),
        L: leaflet,
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8") +
        "\nglobalThis.testApp = { send, updateBoard, boardResults, markers, pinData, pending: () => pending };", context);
    return { app: context.testApp, get, layer, examples, storage, context, vectorStyle: () => vectorStyle };
}

const call = (name, args, data) => ({ name, args, result: JSON.stringify(data) });

test("separate train legs survive an empty new route", () => {
    const { app, get } = harness();
    const first = { origin: "Taipei", destination: "Tainan", date: "2026-10-12" };
    app.updateBoard([call("hsr_trip_planner", first, { ...first, rail: "THSR", trains: [{ departure: "09:00", arrival: "11:00", train_no: "1" }] })]);
    const second = { origin: "Tainan", destination: "Kaohsiung", date: "2026-10-13" };
    app.updateBoard([call("hsr_trip_planner", second, { ...second, rail: "THSR", trains: [] })]);
    assert.equal(app.boardResults.size, 2);
    assert.match(get("train-body").textContent, /Taipei → Tainan/);
    assert.match(get("train-body").textContent, /No trains found/);
    app.updateBoard([call("hsr_trip_planner", first, { error: "Unavailable" })]);
    assert.match(get("train-body").textContent, /Unavailable/);
    assert.doesNotMatch(get("train-body").textContent, /09:00/);
});

test("weather failure replaces the matching forecast and other days remain", () => {
    const { app, get } = harness();
    app.updateBoard([call("typhoon_backup_plan", { city: "Taipei", date: "2026-10-12" }, { city: "Taipei", date: "2026-10-12", comparison: { reason: "Sunny" } })]);
    app.updateBoard([call("typhoon_backup_plan", { city: "Taipei", date: "2026-10-13" }, { city: "Taipei", date: "2026-10-13", comparison: { reason: "Showers" } })]);
    app.updateBoard([call("typhoon_backup_plan", { city: "Taipei", date: "2026-10-12" }, { error: "Weather unavailable" })]);
    assert.doesNotMatch(get("weather-body").textContent, /Sunny/);
    assert.match(get("weather-body").textContent, /2026-10-13/);
    assert.match(get("weather-body").textContent, /Weather unavailable/);
});

test("pins are deduplicated, English first, and replaced when the proposal changes", () => {
    const { app, layer } = harness();
    const pin = { kind: "find_attractions", city: "Taipei", name: "博物館", name_en: "Museum", lat: 25, lon: 121, reference_id: "one" };
    app.updateBoard([], [pin]); app.updateBoard([], [pin]);
    assert.equal(layer.items.length, 1);
    assert.match(layer.items[0].popup.textContent, /Museum/);
    app.updateBoard([], [{ ...pin, name_en: "Gallery", reference_id: "two" }]);
    assert.equal(layer.items.length, 1);
    assert.match(layer.items[0].popup.textContent, /Gallery/);
    app.updateBoard([call("find_attractions", { city: "Taipei" }, { error: "Unavailable" })]);
    assert.equal(layer.items.length, 0);
});

test("failed English-city search clears pins from canonical Chinese-city results", () => {
    const { app, layer } = harness();
    app.updateBoard([], [{ kind: "find_attractions", city: "臺北市", name_en: "Museum", lat: 25, lon: 121 }]);
    app.updateBoard([call("find_attractions", { city: "Taipei" }, { error: "Unavailable" })]);
    assert.equal(layer.items.length, 0);
});

test("vector labels prefer English and retain street-number labels", async () => {
    const style = { layers: [
        { id: "city", layout: { "text-field": ["get", "name"] } },
        { id: "road-shield", layout: { "text-field": ["get", "ref"] } },
    ] };
    const { get, vectorStyle } = harness({}, async () => ({ ok: true, json: async () => style }), true);
    await new Promise((resolve) => setImmediate(resolve));
    const expression = vectorStyle().layers[0].layout["text-field"];
    function evaluate(value, fields) {
        if (!Array.isArray(value)) return value;
        if (value[0] === "get") return fields[value[1]] ?? null;
        if (value[0] === "coalesce") return value.slice(1).map((item) => evaluate(item, fields)).find((item) => item != null);
        if (value[0] === "!=") return evaluate(value[1], fields) !== evaluate(value[2], fields);
        for (let i = 1; i < value.length - 1; i += 2) if (evaluate(value[i], fields)) return evaluate(value[i + 1], fields);
        return evaluate(value.at(-1), fields);
    }
    assert.equal(evaluate(expression, { name_en: "Taipei", name: "臺北" }), "Taipei");
    assert.equal(evaluate(expression, { name_en: "", "name:latin": "Taipei", name: "臺北" }), "Taipei");
    assert.equal(evaluate(expression, { name: "臺北" }), "臺北");
    assert.deepEqual(style.layers[1].layout["text-field"], ["get", "ref"]);
    assert.match(get("map-note").textContent, /English labels where available/);
});

test("stalled vector readiness exits loading without losing the standard map", async () => {
    let expire;
    const { get } = harness({}, async () => ({ ok: true, json: async () => ({ layers: [] }) }),
        { stall: true }, { setTimeout: (callback) => { expire = callback; return 1; }, clearTimeout() {} });
    await new Promise((resolve) => setImmediate(resolve));
    expire();
    assert.match(get("map-note").textContent, /English map unavailable/);
    assert.doesNotMatch(get("map-note").textContent, /Loading/);
});

test("unavailable renderer scripts show the fallback without blocking chat", async () => {
    const { get, app } = harness();
    await new Promise((resolve) => setImmediate(resolve));
    assert.match(get("map-note").textContent, /standard map/);
    assert.equal(app.pending(), false);
});

test("lodging ambiguity and missing exchange history are explained", () => {
    const { app, get } = harness();
    app.updateBoard([call("legal_stay_check", { city: "Taipei", name: "Example" }, {
        mode: "check", is_registered: null, match_status: "candidates", query: "Example",
        matches: [{ name_en: "Example East Hotel", address: "East Street" }, { name_en: "Example West Hotel", address: "West Street" }],
    }), call("twd_exchange", { currency: "USD" }, {
        amount: 100, direction: "to_twd", currency: "USD", converted_amount: 3200,
        converted_currency: "TWD", rate_text: "1 USD = 32 TWD", rate_date: "2026-10-04", diff_percent: null,
    })]);
    assert.match(get("stay-body").textContent, /matching address/);
    assert.match(get("stay-body").textContent, /Example West Hotel/);
    assert.doesNotMatch(get("stay-body").textContent, /Registered:/);
    assert.match(get("budget-body").textContent, /Comparison unavailable/);
});

test("double sends create only one request and disable example buttons", async () => {
    let finish, requests = 0;
    const waiting = new Promise((resolve) => { finish = resolve; });
    const { app, examples, storage } = harness({}, async () => { requests += 1; return waiting; });
    const first = app.send("Suggest museums.");
    await app.send("Another question.");
    assert.equal(requests, 1);
    assert.ok(examples.every((button) => button.disabled));
    finish({ ok: true, json: async () => ({ session_id: "a", response: "Museum ideas.", tool_calls: [] }) });
    await first;
    assert.equal(app.pending(), false);
    assert.equal(JSON.parse(storage.get("trip_view")).transcript.length, 1);
});

test("missing formatting libraries do not strand Send disabled", async () => {
    let requests = 0;
    const { app, context, get } = harness({}, async () => {
        requests += 1;
        return { ok: true, json: async () => ({ session_id: "a", response: "Museum ideas.", tool_calls: [] }) };
    });
    delete context.marked;
    delete context.DOMPurify;
    await app.send("Suggest museums.");
    assert.equal(requests, 1);
    assert.equal(app.pending(), false);
    assert.match(get("messages").textContent, /Museum ideas/);
});

test("HTTP browsers without randomUUID can send using getRandomValues", async () => {
    let body;
    const { app, context } = harness({}, async (_, options) => {
        body = JSON.parse(options.body);
        return { ok: true, json: async () => ({ session_id: body.session_id, response: "Hello", tool_calls: [] }) };
    });
    context.crypto = { getRandomValues: require("node:crypto").webcrypto.getRandomValues.bind(require("node:crypto").webcrypto) };
    await app.send("Hello");
    assert.match(body.session_id, /^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/);
    assert.equal(app.pending(), false);
});

test("setup failure releases Send and shows the error", async () => {
    const { app, context, get } = harness();
    context.crypto = {};
    await app.send("Hello");
    assert.equal(app.pending(), false);
    assert.equal(get("send-btn").disabled, false);
    assert.match(get("messages").textContent, /Could not reach the agent/);
});

test("blocked session storage still permits questions", async () => {
    let requests = 0;
    const { app, context } = harness({}, async () => {
        requests += 1;
        return { ok: true, json: async () => ({ session_id: "a", response: "Hello", tool_calls: [] }) };
    });
    context.sessionStorage.setItem = () => { throw new Error("Storage blocked"); };
    await app.send("Hello");
    assert.equal(requests, 1);
    assert.equal(app.pending(), false);
});

test("the composer submits a typed question and re-enables Send", async () => {
    let posted;
    const { get } = harness({}, async (_, options) => {
        posted = JSON.parse(options.body);
        return { ok: true, json: async () => ({ session_id: posted.session_id, response: "Taipei ideas.", tool_calls: [] }) };
    });
    get("user-input").value = "What can I do in Taipei?";
    get("composer").listeners.submit({ preventDefault() {} });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(posted.message, "What can I do in Taipei?");
    assert.match(get("messages").textContent, /Taipei ideas/);
    assert.equal(get("send-btn").disabled, false);
});

test("refresh restores the transcript and scoped board", async () => {
    const saved = { sessionId: "a", transcript: [{ user: "Hello", reply: { response: "Hi", tool_calls: [] } }],
        board: [["weather", { call: call("typhoon_backup_plan", { city: "Taipei", date: "2026-10-12" }, {}),
            data: { city: "Taipei", date: "2026-10-12", comparison: { reason: "Showers" } } }]], pins: [] };
    const { app, get } = harness({ session_id: "a", trip_view: JSON.stringify(saved) });
    await new Promise((resolve) => setImmediate(resolve));
    assert.match(get("messages").textContent, /Hello/);
    assert.match(get("weather-body").textContent, /Showers/);
    assert.equal(app.boardResults.size, 1);
});
