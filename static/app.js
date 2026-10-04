// Taiwan Like a Local: chat + trip board. The board is drawn only from /chat's tool_calls results.

const $ = (id) => document.getElementById(id);
const messages = $("messages");
const input = $("user-input");
const sendBtn = $("send-btn");

// sessionStorage is per tab: a refresh keeps the conversation, a new tab starts a separate one.
const tabStorage = {
    get(key) { try { return sessionStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { sessionStorage.setItem(key, value); } catch {} },
    remove(key) { try { sessionStorage.removeItem(key); } catch {} },
};
let sessionId = tabStorage.get("session_id");
let pending = false;
let activeRequest = null;
let transcript = [];
let resetGeneration = 0;

function setPending(value) {
    pending = value;
    sendBtn.disabled = value;
    input.disabled = value;
    document.querySelectorAll(".example").forEach((button) => { button.disabled = value; });
}

function persist() {
    try {
        sessionStorage.setItem("trip_view", JSON.stringify({ sessionId, transcript: transcript.slice(-20),
            board: [...boardResults], pins: [...pinData] }));
    } catch {
        // Tool details can be large; reduce the stored transcript before exceeding browser quotas.
        if (transcript.length > 1) { transcript.shift(); persist(); }
    }
}

// How each tool looks in the chat and on the map. Unknown tools still render with defaults.
const TOOL_META = {
    legal_stay_check: { icon: "🏠", color: "#0f766e", label: "Stays", gist: (a) => a.name ? `is "${a.name}" registered?` : `registered ${a.type || "stays"} in ${a.city}` },
    find_local_food: { icon: "🍜", color: "#c8102e", label: "Food", gist: (a) => `${a.keyword || "food"} in ${a.city}` },
    twd_exchange: { icon: "💱", color: "#b45309", gist: (a) => `${a.amount} ${a.direction === "from_twd" ? "TWD → " + (a.currency || "USD") : (a.currency || "USD") + " → TWD"}` },
    find_attractions: { icon: "🏯", color: "#7c3aed", label: "Sights", gist: (a) => `${a.keyword || "sights"} in ${a.city}` },
    hsr_trip_planner: { icon: "🚄", color: "#2563eb", gist: (a) => `${a.origin} → ${a.destination} on ${a.date}` },
    crowd_risk_check: { icon: "📅", color: "#be123c", gist: (a) => `${a.start_date} → ${a.end_date}` },
    typhoon_backup_plan: { icon: "🌀", color: "#0369a1", label: "Weather", gist: (a) => `${a.city} on ${a.date}` },
    get_weather: { icon: "🌤️", color: "#0369a1", gist: (a) => a.location },
};
const metaFor = (name) => TOOL_META[name] || { icon: "🔧", color: "#6b7280", gist: (a) => JSON.stringify(a) };

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
}

function parse(result) {
    try { return JSON.parse(result); } catch { return null; }
}

// --- Chat ---

function addMessage(role, content) {
    $("welcome")?.remove();
    const div = el("div", `msg ${role}`);
    if (role === "assistant") renderAnswer(div, content);
    else div.textContent = content;
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
}

function renderAnswer(node, content) {
    if (typeof DOMPurify !== "undefined" && typeof marked !== "undefined") {
        node.innerHTML = DOMPurify.sanitize(marked.parse(content));
    } else {
        // Plain text stays usable and safe when a formatting CDN is unavailable.
        node.textContent = content;
    }
}

function newSessionId() {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    // randomUUID needs a secure origin; getRandomValues also works on local HTTP addresses.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function renderToolCalls(calls, before) {
    if (!calls.length) return;
    const wrap = el("div", "tools");
    for (const call of calls) {
        const meta = metaFor(call.name);
        const data = parse(call.result);
        const card = el("details", "tool" + (data && data.error ? " error" : ""));
        const summary = el("summary");
        summary.append(el("span", "icon", meta.icon), el("span", "name", call.name));
        let gist = "";
        try { gist = meta.gist(call.args || {}); } catch {}
        if (data && data.error) gist = "⚠ " + data.error;
        summary.append(el("span", "gist", gist));
        card.append(summary, el("div", "label", "arguments"), el("pre", "", JSON.stringify(call.args, null, 2)),
            el("div", "label", "result"), el("pre", "", data ? JSON.stringify(data, null, 2) : call.result));
        wrap.appendChild(card);
    }
    messages.insertBefore(wrap, before);
}

async function send(text) {
    text = (text ?? input.value).trim();
    if (!text || pending) return;
    setPending(true);
    const generation = resetGeneration;
    let deadline;
    let loading;
    try {
        const newSession = !sessionId;
        if (newSession) {
            sessionId = newSessionId();
            tabStorage.set("session_id", sessionId);
        }
        input.value = "";
        const controller = new AbortController();
        activeRequest = controller;
        deadline = setTimeout(() => controller.abort(), 190000);
        addMessage("user", text);
        loading = addMessage("assistant", "Asking around like a local…");
        loading.classList.add("loading");
        const res = await fetch("/chat", {
            method: "POST",
            signal: controller.signal,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text, session_id: sessionId, new_session: newSession }),
        });
        if (!res.ok) throw new Error(`Server returned ${res.status}`);
        const data = await res.json();
        if (generation !== resetGeneration) return;
        if (data.session_status === "expired") {
            resetBoard();
            addMessage("assistant", "Your earlier server session expired. This is a new conversation.");
            transcript = [];
        }
        sessionId = data.session_id;
        tabStorage.set("session_id", sessionId);
        renderToolCalls(data.tool_calls || [], loading);
        updateBoard(data.tool_calls || [], data.map_pins || [], data.places || []);
        transcript.push({ user: text, reply: data });
        persist();
        loading.classList.remove("loading");
        renderAnswer(loading, data.response || "(no answer)");
    } catch (e) {
        if (generation !== resetGeneration) return;
        loading = loading || addMessage("assistant", "");
        loading.classList.remove("loading");
        loading.textContent = e.name === "AbortError" ? "The reply took too long. Please try again." : "Could not reach the agent: " + e.message;
    } finally {
        clearTimeout(deadline);
        if (generation === resetGeneration) { activeRequest = null; setPending(false); }
    }
    input.focus();
    messages.scrollTop = messages.scrollHeight;
}

$("composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });
document.querySelectorAll(".example").forEach((b) => b.addEventListener("click", () => send(b.textContent)));

$("new-trip").addEventListener("click", async () => {
    resetGeneration += 1;
    activeRequest?.abort();
    const previousId = sessionId;
    tabStorage.remove("session_id");
    tabStorage.remove("trip_view");
    if (previousId) fetch(`/clear?session_id=${encodeURIComponent(previousId)}`, { method: "POST", keepalive: true }).catch(() => {});
    location.reload();
});

$("panel-toggle").addEventListener("click", () => {
    const panel = $("panel");
    panel.classList.toggle("open");
    $("panel-toggle").textContent = panel.classList.contains("open") ? "Hide trip board" : "Show trip board";
    map?.invalidateSize();
});

// --- Trip board ---

const map = typeof L === "undefined" ? null : L.map("map", { scrollWheelZoom: false }).setView([23.7, 120.95], 7);
// Keep a usable map visible while the optional vector renderer loads.
const raster = map && L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18, attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);
let basemap = raster;

function loadMapScript(url) {
    return new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = url;
        script.async = true;
        script.onload = resolve;
        script.onerror = () => reject(new Error("Map renderer unavailable"));
        document.head.appendChild(script);
    });
}

async function loadBasemap() {
    const controller = new AbortController();
    let settled = false;
    let vector;
    const fallback = () => {
        if (settled) return;
        settled = true;
        clearTimeout(deadline);
        controller.abort();
        // Set the message first so even a renderer cleanup error cannot leave "Loading…".
        $("map-note").textContent = "English map unavailable; showing the standard map with local labels";
        if (vector) {
            try { map.removeLayer(vector); } catch {}
        }
    };
    // Covers script downloads, style requests and renderer readiness together.
    const deadline = setTimeout(fallback, 20000);
    try {
        if (!window.maplibregl) {
            const css = document.createElement("link");
            css.rel = "stylesheet";
            css.href = "https://unpkg.com/maplibre-gl@5.6.2/dist/maplibre-gl.css";
            document.head.appendChild(css);
            await loadMapScript("https://unpkg.com/maplibre-gl@5.6.2/dist/maplibre-gl.js");
        }
        if (settled) return;
        if (!L.maplibreGL) await loadMapScript("https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.1.4/leaflet-maplibre-gl.js");
        if (settled) return;
        const response = await fetch("https://tiles.openfreemap.org/styles/liberty", { signal: controller.signal });
        if (!response.ok) throw new Error("Map style unavailable");
        const style = await response.json();
        if (settled) return;
        for (const layer of style.layers) {
            const field = layer.layout?.["text-field"];
            if (!field || !JSON.stringify(field).includes("name")) continue;
            const names = ["name:en", "name_en", "name:latin"];
            layer.layout["text-field"] = ["case", ...names.flatMap((name) => [
                ["!=", ["coalesce", ["get", name], ""], ""], ["get", name],
            ]), ["get", "name"]];
        }
        vector = L.maplibreGL({ style }).addTo(map);
        const renderer = vector.getMaplibreMap();
        const ready = () => {
            if (settled) return;
            settled = true;
            clearTimeout(deadline);
            basemap = vector;
            map.removeLayer(raster);
            $("map-note").textContent = "English labels where available";
        };
        renderer.once("load", ready);
        // Already-loaded renderers and cached styles need no second load event.
        if (renderer.loaded?.()) ready();
        renderer.on("error", fallback);
    } catch { fallback(); }
}
if (map) loadBasemap();
else $("map-note").textContent = "Map unavailable; chat is still available";
const pinLayer = map ? L.layerGroup().addTo(map) : { getLayers: () => [], clearLayers() {}, removeLayer() {} };
const legendSeen = new Set();
const markers = new Map();
const pinData = new Map();
const boardResults = new Map();
let boardTargets = {};
const boardNode = (id) => boardTargets[id] || $(id);
const displayName = (place) => place.label || place.name_en || place.name || "Place";
const CITY_KEYS = {"taipei": "臺北市", "new taipei": "新北市", "taoyuan": "桃園市", "taichung": "臺中市", "tainan": "臺南市", "kaohsiung": "高雄市", "keelung": "基隆市", "hsinchu": "新竹市", "hsinchu county": "新竹縣", "miaoli": "苗栗縣", "changhua": "彰化縣", "nantou": "南投縣", "yunlin": "雲林縣", "chiayi": "嘉義市", "chiayi county": "嘉義縣", "pingtung": "屏東縣", "yilan": "宜蘭縣", "hualien": "花蓮縣", "taitung": "臺東縣", "penghu": "澎湖縣", "kinmen": "金門縣", "matsu": "連江縣", "lienchiang": "連江縣"};
function cityScope(value) {
    const key = String(value || "").trim().toLowerCase().replaceAll("台", "臺").replaceAll("-", " ");
    return CITY_KEYS[key] || CITY_KEYS[key.replace(/ city$| county$/, "")] || key;
}

// Find every {name, lat, lon} object anywhere in a tool result.
function findPlaces(node, out = []) {
    if (Array.isArray(node)) node.forEach((n) => findPlaces(n, out));
    else if (node && typeof node === "object") {
        const lat = node.lat ?? node.PositionLat, lon = node.lon ?? node.PositionLon;
        if (typeof lat === "number" && typeof lon === "number" && lat && lon) out.push({ ...node, lat, lon });
        Object.values(node).forEach((v) => typeof v === "object" && findPlaces(v, out));
    }
    return out;
}

function addPins(call, data) {
    if (!map) return [];
    const meta = metaFor(call.name);
    const places = findPlaces(data);
    if (!places.length) return [];
    if (meta.label && !legendSeen.has(call.name)) {
        legendSeen.add(call.name);
        const item = el("span", "", meta.label);
        item.style.setProperty("--c", meta.color);
        $("legend").appendChild(item);
    }
    return places.map((p) => {
        const popup = el("div");
        popup.append(el("b", "", `${meta.icon} ${displayName(p)}`));
        for (const line of [p.license_number, p.address, p.reference_price_twd && `~${p.reference_price_twd} TWD/night`, p.open_time]) {
            if (line) popup.append(el("br"), document.createTextNode(line));
        }
        const key = p.reference_id || `${call.name}:${p.name}:${p.lat}:${p.lon}`;
        if (markers.has(key)) pinLayer.removeLayer(markers.get(key));
        pinData.set(key, { ...p, kind: call.name });
        if (p.role) popup.append(el("div", "", p.role === "alternative" ? "Alternative" : "Suggested stop"));
        const marker = L.circleMarker([p.lat, p.lon], { radius: 7, color: "#fff", weight: 2, fillColor: meta.color, fillOpacity: 0.95 })
            .bindPopup(popup)
            .addTo(pinLayer);
        markers.set(key, marker);
        return marker;
    });
}

// A check verdict and a list of registered alternatives can arrive in the same reply; show both.
function showStay(data) {
    const slot = data.mode === "check" ? "stay-verdict-slot" : "stay-list-slot";
    let box = boardNode(slot);
    if (!box) {
        box = el("div");
        box.id = slot;
        if (slot === "stay-verdict-slot") boardNode("stay-body").prepend(box);
        else boardNode("stay-body").append(box);
    }
    box.replaceChildren();
    if (data.mode === "check") {
        const ok = data.is_registered;
        const top = ok === true ? data.matches?.[0] : null;
        const v = el("div", "stay-verdict " + (ok ? "ok" : "warn"));
        v.append(el("span", "big", ok ? "✅" : "⚠️"));
        const txt = el("div");
        txt.append(el("b", "", ok ? `Registered: ${displayName(top)}` : `No registration found for "${data.query}"`));
        txt.append(el("small", "", ok ? `${top.license_number} · ${top.license_type}` : "Ask the host for their license number (登記證號)."));
        v.append(txt);
        box.append(v);
        if (data.match_status === "candidates") {
            txt.replaceChildren(el("b", "", "Similar registered properties found; choose the matching address."));
            for (const candidate of data.matches || []) box.append(el("p", "", `${displayName(candidate)} · ${candidate.address || "Address unavailable"}`));
        }
        if (data.note || data.advice) box.append(el("small", "", data.note || data.advice));
    } else if (data.stays) {
        const list = el("ul", "stay-list");
        for (const s of data.stays) {
            const li = el("li");
            li.append(el("b", "", displayName(s)));
            if (s.taiwan_host_certified) li.append(el("span", "badge", "Taiwan Host"));
            li.append(el("div", "lic", `✔ ${s.license_number}`));
            if (s.reference_price_twd) li.append(el("div", "", `~${s.reference_price_twd} TWD / night`));
            list.append(li);
        }
        box.append(list);
        if (!data.stays.length) box.append(el("p", "", "No stays found for this search."));
    }
    boardNode("stay-card").hidden = false;
}

function showBudget(data) {
    const b = el("div", "budget");
    b.append(el("span", "from", `${data.amount.toLocaleString()} ${data.direction === "to_twd" ? data.currency : "TWD"} =`),
        el("span", "to", `${data.converted_amount.toLocaleString()} ${data.converted_currency}`));
    const trend = data.diff_percent > 0 ? `▲ ${data.diff_percent}%` : data.diff_percent < 0 ? `▼ ${Math.abs(data.diff_percent)}%` : "flat";
    boardNode("budget-body").replaceChildren(b, el("div", "budget-meta", `${data.rate_text} on ${data.rate_date} · weekly samples over four weeks: ${data.diff_percent == null ? "Comparison unavailable" : trend}`));
    boardNode("budget-card").hidden = false;
}

function showDates(data) {
    const days = Array.isArray(data) ? data : data.days || [];
    if (!days.length) { boardNode("dates-body").replaceChildren(el("p", "", "No calendar results for this range.")); return; }
    const strip = boardNode("dates-body");
    strip.replaceChildren();
    for (const d of days) {
        const cell = el("div", "day " + (d.risk || ""));
        cell.title = d.reason || d.holiday_name || "";
        cell.append(el("span", "", d.weekday || ""), el("b", "", (d.date || "").slice(5)), el("span", "", d.holiday_name || d.risk || ""));
        strip.append(cell);
    }
    boardNode("dates-note").textContent = data.risk_basis || "";
    boardNode("dates-card").hidden = false;
}

function showTrains(data) {
    const body = boardNode("train-body");
    const route = el("div", "train-route", `${data.rail} · ${data.origin} → ${data.destination} · ${data.date}`);
    const list = el("ul", "train-list");
    for (const train of data.trains || []) {
        const item = el("li");
        item.append(el("div", "train-time", `${train.departure} → ${train.arrival}`));
        item.append(el("div", "train-type", `${train.train_type || "Type unavailable"} · Train ${train.train_no}`));
        item.append(el("div", "train-fare", train.fare_twd == null ? "Fare unavailable" : `${train.fare_twd.toLocaleString()} TWD`));
        list.append(item);
    }
    body.replaceChildren(route, list);
    if (!data.trains?.length) body.append(el("p", "", "No trains found for this search."));
    boardNode("train-card").hidden = false;
}

function showWeather(data) {
    const comparison = data.comparison || {};
    const bad = data.is_bad_weather === true;
    const f = data.forecast || {};
    const text = comparison.reason || data.seasonal_note || (data.typhoon_alert
        ? "An active typhoon warning is in effect. Follow official updates."
        : f.weather || "Forecast unavailable.");
    const details = [el("div", "alert" + (bad ? "" : " calm"), `${bad ? "⚠ " : ""}${text}`)];
    details.unshift(el("b", "", `${data.city || ""} · ${data.date || ""}`));
    if (comparison.outing_window) {
        const w = comparison.outing_window;
        details.push(el("div", "", `Outing: ${w.start}–${w.end} (Taiwan time)`));
    }
    for (const p of comparison.periods || []) {
        const rain = p.rain_chance == null ? "Rain chance unavailable" : `Rain chance ${p.rain_chance}%`;
        details.push(el("div", "", `${p.outing_start}–${p.outing_end}: ${rain} · ${p.activity_preference}`));
    }
    if (comparison.warning_note) details.push(el("div", "", comparison.warning_note));
    boardNode("weather-body").replaceChildren(...details);
    boardNode("weather-card").hidden = false;
}

const boardKinds = {
    legal_stay_check: ["stay", showStay], twd_exchange: ["budget", showBudget],
    crowd_risk_check: ["dates", showDates], hsr_trip_planner: ["train", showTrains],
    typhoon_backup_plan: ["weather", showWeather],
};

function scopeKey(call) {
    const a = call.args || {};
    const fields = {
        legal_stay_check: ["city", "district", "name", "type"],
        hsr_trip_planner: ["rail", "origin", "destination", "date", "depart_after", "depart_before", "arrive_by"],
        typhoon_backup_plan: ["city", "date", "start_time", "end_time", "available_minutes"],
        crowd_risk_check: ["start_date", "end_date"], twd_exchange: ["currency", "direction"],
    }[call.name] || ["city"];
    return JSON.stringify([call.name, ...fields.map((key) => ["city", "origin", "destination"].includes(key) ? cityScope(a[key]) : a[key] ?? null)]);
}

function resetBoard() {
    boardResults.clear(); pinData.clear(); markers.clear(); pinLayer.clearLayers();
    for (const [kind] of Object.values(boardKinds)) $(kind + "-card").hidden = true;
    legendSeen.clear(); $("legend").replaceChildren();
}

function renderBoard() {
    for (const [name, [kind, render]] of Object.entries(boardKinds)) {
        const entries = [...boardResults.values()].filter((entry) => entry.call.name === name);
        const body = $(kind + "-body");
        body.replaceChildren();
        $(kind + "-card").hidden = !entries.length;
        for (const { call, data } of entries) {
            const section = el("section", "board-result");
            let title = "";
            try { title = metaFor(name).gist(call.args || {}); } catch {}
            section.append(el("h4", "", title));
            const content = el("div"); section.append(content); body.append(section);
            boardTargets = { [kind + "-body"]: content };
            if (name === "legal_stay_check") {
                for (const slot of ["stay-verdict-slot", "stay-list-slot"]) {
                    boardTargets[slot] = el("div"); content.append(boardTargets[slot]);
                }
            }
            if (name === "crowd_risk_check") {
                boardTargets["dates-note"] = el("small"); section.append(boardTargets["dates-note"]);
            }
            if (data.error) content.append(el("p", "alert", data.error));
            else render(data);
            const stale = (data.data_freshness || []).filter((entry) => entry.stale);
            if (stale.length) section.append(el("small", "", "Cached data: last retrieved " + stale.map((entry) => entry.retrieved_at).join(", ")));
        }
    }
    boardTargets = {};
}

function updateBoard(calls, mapPins = [], places = []) {
    const newPins = [];
    // Replace proposals within the same city/category; keep other cities in a multi-city trip.
    const changed = new Set(mapPins.map((p) => JSON.stringify([p.kind, cityScope(p.city)])));
    for (const call of calls) {
        const data = parse(call.result);
        if (!data) continue;
        if (["find_attractions", "find_local_food", "legal_stay_check"].includes(call.name) &&
            (data.error || (data.results || data.stays || []).length === 0)) {
            changed.add(JSON.stringify([call.name, cityScope(data.city || call.args?.city)]));
        }
        if (!boardKinds[call.name]) continue;
        const labels = new Map(places.map((p) => [p.name, p.label]));
        const enriched = { ...data };
        for (const key of ["stays", "matches"]) if (data[key]) enriched[key] = data[key].map((p) => ({ ...p, name_en: labels.get(p.name) || p.name_en }));
        const key = scopeKey(call);
        boardResults.delete(key); boardResults.set(key, { call, data: enriched });
        while (boardResults.size > 30) boardResults.delete(boardResults.keys().next().value);
    }
    for (const [key, p] of pinData) if (changed.has(JSON.stringify([p.kind, cityScope(p.city)]))) {
        pinLayer.removeLayer(markers.get(key)); markers.delete(key); pinData.delete(key);
    }
    for (const kind of new Set(mapPins.map((p) => p.kind || "find_attractions"))) {
        newPins.push(...addPins({ name: kind }, { results: mapPins.filter((p) => (p.kind || "find_attractions") === kind) }));
    }
    while (markers.size > 70) {
        const key = markers.keys().next().value; pinLayer.removeLayer(markers.get(key)); markers.delete(key); pinData.delete(key);
    }
    renderBoard();
    if (newPins.length) map.fitBounds(L.featureGroup(newPins).getBounds().pad(0.3), { maxZoom: 14 });
    $("panel-hint").hidden = boardResults.size > 0 || markers.size > 0;
}

async function restore() {
    setPending(true);
    try {
        const saved = JSON.parse(tabStorage.get("trip_view") || "null");
        if (!sessionId) return;
        const response = await fetch(`/session/${encodeURIComponent(sessionId)}`, { signal: AbortSignal.timeout(5000) });
        if (!response.ok) throw new Error("Session check failed");
        const status = await response.json();
        if (status.status !== "active") {
            sessionId = null; tabStorage.remove("session_id"); tabStorage.remove("trip_view");
            addMessage("assistant", "Your earlier conversation expired. Start a new trip here.");
            return;
        }
        if (!saved || saved.sessionId !== sessionId) return;
        transcript = saved.transcript || [];
        for (const turn of transcript) {
            addMessage("user", turn.user);
            const reply = addMessage("assistant", turn.reply.response || "");
            renderToolCalls(turn.reply.tool_calls || [], reply);
        }
        for (const [key, value] of saved.board || []) boardResults.set(key, value);
        for (const [, p] of saved.pins || []) addPins({ name: p.kind }, { results: [p] });
        renderBoard();
        if (markers.size) map.fitBounds(L.featureGroup([...markers.values()]).getBounds().pad(0.3), { maxZoom: 14 });
        $("panel-hint").hidden = boardResults.size > 0 || markers.size > 0;
    } catch {
        // Keep the session ID during a transient outage rather than silently losing memory.
        addMessage("assistant", "Could not restore the earlier conversation. Please refresh to retry.");
    } finally { setPending(false); input.focus(); }
}
restore();
