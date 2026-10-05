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
    input.disabled = value;
    updateSendState();
}

// Questions can be typed while the lookup budget is empty; only sending waits for a free lookup.
function updateSendState() {
    const blocked = pending || quotaAvailable() === 0;
    sendBtn.disabled = blocked;
    document.querySelectorAll(".example").forEach((button) => { button.disabled = blocked; });
}

// --- Lookup budget: tool calls per rolling minute, shared by every user of the server ---

let quota = null;  // last /quota answer plus the local time it arrived
let quotaTimer = null;
let quotaRefreshing = false;

function setQuota(value) {
    if (!value || typeof value.limit !== "number" || typeof value.remaining !== "number") return;
    quota = { ...value, frees_in_seconds: value.frees_in_seconds || [], at: Date.now() };
    renderQuota();
}

// Slots whose minute has passed since the server answered are free again.
function quotaSlots() {
    if (!quota) return null;
    const elapsed = (Date.now() - quota.at) / 1000;
    const waits = quota.frees_in_seconds.map((s) => s - elapsed);
    const freed = quota.remaining === quota.limit ? 0 : Math.min(quota.limit - quota.remaining, waits.filter((w) => w <= 0).length);
    const upcoming = waits.filter((w) => w > 0);
    return { limit: quota.limit, available: quota.remaining + freed, next: upcoming.length ? Math.ceil(Math.min(...upcoming)) : null, freed };
}

function quotaAvailable() {
    const slots = quotaSlots();
    return slots ? slots.available : null;
}

function quotaWaitText() {
    const slots = quotaSlots();
    return slots && slots.available === 0 && slots.next ? `A lookup renews in ${slots.next} s.` : "";
}

function renderQuota() {
    clearTimeout(quotaTimer);
    const slots = quotaSlots();
    if (!slots) return;
    const meter = $("quota-meter");
    meter.replaceChildren(...Array.from({ length: slots.limit }, (_, i) => el("i", i < slots.available ? "" : "used")));
    // Each lookup renews 60 s after it was used, so lookups come back one at a time.
    let text = `${slots.available} of ${slots.limit} available`;
    if (slots.available === 0) text = slots.next ? `All used · 1 renews in ${slots.next} s` : "All used";
    else if (slots.next && slots.available < slots.limit) text += ` · 1 renews in ${slots.next} s`;
    $("quota-text").textContent = text;
    $("quota").classList.toggle("empty", slots.available === 0);
    input.placeholder = slots.available === 0
        ? "Lookup limit reached. You can type now and send when a lookup frees up."
        : "Ask about a city, a stay, a train or a date";
    updateSendState();
    if (slots.freed) {
        // Count the freed slot locally, then confirm with the server: other people share the budget.
        const elapsed = (Date.now() - quota.at) / 1000;
        quota = { ...quota, remaining: slots.available, at: Date.now(),
            frees_in_seconds: quota.frees_in_seconds.map((s) => s - elapsed).filter((s) => s > 0) };
        refreshQuota();
    }
    if (slots.next) quotaTimer = setTimeout(renderQuota, 1000);
}

async function refreshQuota() {
    if (quotaRefreshing) return;
    quotaRefreshing = true;
    try {
        const response = await fetch("/quota");
        if (response.ok) setQuota(await response.json());
    } catch {
        // The meter is informational; the server still enforces the limit.
    } finally { quotaRefreshing = false; }
}
window.addEventListener?.("focus", refreshQuota);

function persist() {
    try {
        sessionStorage.setItem("trip_view", JSON.stringify({ sessionId, transcript: transcript.slice(-20),
            board: [...boardResults], pins: [...pinData] }));
    } catch {
        // Tool details can be large; reduce the stored transcript before exceeding browser quotas.
        if (transcript.length > 1) { transcript.shift(); persist(); }
    }
}

// Chinese text from official feeds is replaced with English; hasHan finds what still needs it.
const hasHan = (text) => /[㐀-鿿]/.test(String(text ?? ""));
const english = (text, fallback = "") => (text && !hasHan(text) ? String(text) : fallback);

// How each tool looks in the chat and on the map. Unknown tools still render with defaults.
// legend marks tools whose places are pinned on the map.
const TOOL_META = {
    legal_stay_check: { label: "Stays", color: "#1f6f5c", legend: true,
        gist: (a) => a.name ? `Registration check${a.city ? " in " + cityLabel(a.city) : ""}` : `Registered ${a.type === "bnb" ? "B&Bs" : a.type === "hotel" ? "hotels" : "stays"} in ${cityLabel(a.city)}` },
    find_local_food: { label: "Food", color: "#b3341f", legend: true, gist: (a) => `${english(a.keyword, "Food")} in ${cityLabel(a.city)}` },
    twd_exchange: { label: "Money", color: "#8a5a00", gist: (a) => `${a.amount} ${a.direction === "from_twd" ? "TWD → " + (a.currency || "USD") : (a.currency || "USD") + " → TWD"}` },
    find_attractions: { label: "Sights", color: "#5b3f8c", legend: true, gist: (a) => `${english(a.keyword, "Sights")} in ${cityLabel(a.city)}` },
    hsr_trip_planner: { label: "Trains", color: "#1f4e79", gist: (a) => `${cityLabel(a.origin)} → ${cityLabel(a.destination)} on ${a.date}` },
    crowd_risk_check: { label: "Dates", color: "#6b4226", gist: (a) => `${a.start_date} → ${a.end_date}` },
    typhoon_backup_plan: { label: "Weather", color: "#2b6c8f", gist: (a) => `${cityLabel(a.city)} on ${a.date}` },
};
const metaFor = (name) => TOOL_META[name] || { label: "Lookup", color: "#77736b", gist: (a) => JSON.stringify(a) };

// Short result size for the lookup list, e.g. "10 results".
function resultCount(data) {
    if (!data) return "";
    if (data.error) return "Failed";
    const list = data.results || data.stays || data.trains || data.days || data.matches;
    if (Array.isArray(list)) return `${list.length} ${list.length === 1 ? "result" : "results"}`;
    return "Done";
}

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
    wrap.append(el("div", "tools-title", calls.length === 1 ? "1 lookup" : `${calls.length} lookups`));
    for (const call of calls) {
        const meta = metaFor(call.name);
        const data = parse(call.result);
        const card = el("details", "tool" + (data && data.error ? " error" : ""));
        const summary = el("summary");
        const kind = el("span", "kind", meta.label);
        kind.style.setProperty("--c", meta.color);
        let gist = "";
        try { gist = meta.gist(call.args || {}); } catch {}
        if (data && data.error) gist = data.error;
        summary.append(kind, el("span", "gist", gist), el("span", "count", resultCount(data)));
        // The raw request and response stay available for checking the agent's work.
        const raw = el("div", "raw");
        const requestLabel = el("div", "label", "Request ");
        requestLabel.append(el("code", "", call.name));
        raw.append(requestLabel, el("pre", "", JSON.stringify(call.args, null, 2)),
            el("div", "label", "Response"), el("pre", "", data ? JSON.stringify(data, null, 2) : call.result));
        card.append(summary, raw);
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
        loading = addMessage("assistant", "Looking this up…");
        loading.classList.add("loading");
        const res = await fetch("/chat", {
            method: "POST",
            signal: controller.signal,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text, session_id: sessionId, new_session: newSession }),
        });
        if (res.status === 429) {
            // The shared lookup budget is spent: nothing ran, so the question can be asked again later.
            const detail = (await res.json().catch(() => ({}))).detail || {};
            if (generation !== resetGeneration) return;
            setQuota(detail.tool_quota);
            loading.classList.remove("loading");
            loading.textContent = `${detail.message || "Lookup limit reached."} ${quotaWaitText()}`.trim();
            input.value = text;
            return;
        }
        if (!res.ok) throw new Error(`Server returned ${res.status}`);
        const data = await res.json();
        if (generation !== resetGeneration) return;
        setQuota(data.tool_quota);
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
    $("panel-toggle").textContent = panel.classList.contains("open") ? "Hide trip notes" : "Show trip notes";
    map?.invalidateSize();
});

// --- Trip board ---

const map = typeof L === "undefined" ? null : L.map("map", { scrollWheelZoom: false }).setView([23.7, 120.95], 7);
// A label-free basemap shows while the English vector map loads, and stays if it fails, so the
// map never shows Chinese labels. Pins carry their own English names.
const raster = map && L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 16, attribution: "Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors",
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
        $("map-note").textContent = "English map unavailable; showing the standard map without labels";
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
            // English, then romanized names (name_int is Latin in OpenMapTiles); never the local script.
            const names = ["name:en", "name_en", "name_int", "name:latin"];
            layer.layout["text-field"] = ["case", ...names.flatMap((name) => [
                ["!=", ["coalesce", ["get", name], ""], ""], ["get", name],
            ]), ""];
        }
        vector = L.maplibreGL({ style }).addTo(map);
        const renderer = vector.getMaplibreMap();
        const ready = () => {
            if (settled) return;
            settled = true;
            clearTimeout(deadline);
            basemap = vector;
            map.removeLayer(raster);
            $("map-note").textContent = "English labels; places without an English name are unlabeled";
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
// Prefer an English name; the server romanizes Chinese-only listings into name_en.
const displayName = (place) => english(place.label) || english(place.name_en) || english(place.name)
    || place.name_en || place.label || place.name || "Place";
const CITY_KEYS = {"taipei": "臺北市", "new taipei": "新北市", "taoyuan": "桃園市", "taichung": "臺中市", "tainan": "臺南市", "kaohsiung": "高雄市", "keelung": "基隆市", "hsinchu": "新竹市", "hsinchu county": "新竹縣", "miaoli": "苗栗縣", "changhua": "彰化縣", "nantou": "南投縣", "yunlin": "雲林縣", "chiayi": "嘉義市", "chiayi county": "嘉義縣", "pingtung": "屏東縣", "yilan": "宜蘭縣", "hualien": "花蓮縣", "taitung": "臺東縣", "penghu": "澎湖縣", "kinmen": "金門縣", "matsu": "連江縣", "lienchiang": "連江縣"};
function cityScope(value) {
    const key = String(value || "").trim().toLowerCase().replaceAll("台", "臺").replaceAll("-", " ");
    return CITY_KEYS[key] || CITY_KEYS[key.replace(/ city$| county$/, "")] || key;
}
// English display names for the official Chinese city and county names.
const CITY_NAMES = {};
for (const [en, zh] of Object.entries(CITY_KEYS)) CITY_NAMES[zh] ??= en.replace(/\b\w/g, (c) => c.toUpperCase());
function cityLabel(value) {
    const text = String(value ?? "").trim();
    if (!hasHan(text)) return text;
    const key = text.replaceAll("台", "臺");
    return CITY_NAMES[key] || CITY_NAMES[key + "市"] || CITY_NAMES[key + "縣"] || text;
}

// Drop Chinese glosses such as "Registered B&B (民宿)"; a fully Chinese text becomes the fallback.
function withoutHan(text, fallback = "") {
    const cleaned = String(text ?? "").replace(/\s*[(（][^)）]*[㐀-鿿][^)）]*[)）]/g, "").trim();
    return english(cleaned, fallback);
}

// "花蓮縣民宿2170號" -> "Hualien B&B licence No. 2170".
function licenseLabel(number) {
    if (!number || !hasHan(number)) return number || "";
    const digits = String(number).match(/\d+/g);
    if (!digits) return "Registered licence";
    const city = String(number).match(/^(..[縣市])/);
    const kind = /民宿/.test(number) ? "B&B licence" : /觀光旅館/.test(number) ? "Tourist hotel licence" : /旅館/.test(number) ? "Hotel licence" : "Licence";
    return `${city ? cityLabel(city[1]) + " " : ""}${kind} No. ${digits.join("-")}`;
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
    if (meta.legend && !legendSeen.has(call.name)) {
        legendSeen.add(call.name);
        const item = el("span", "", meta.label);
        item.style.setProperty("--c", meta.color);
        $("legend").appendChild(item);
    }
    return places.map((p) => {
        const popup = el("div");
        popup.append(el("b", "", displayName(p)));
        // Chinese addresses and opening-hour notes are left to the map link.
        for (const line of [licenseLabel(p.license_number), english(p.address), p.reference_price_twd && `From ${p.reference_price_twd} TWD a night`, english(p.open_time)]) {
            if (line) popup.append(el("br"), document.createTextNode(line));
        }
        const key = p.reference_id || `${call.name}:${p.name}:${p.lat}:${p.lon}`;
        if (markers.has(key)) pinLayer.removeLayer(markers.get(key));
        pinData.set(key, { ...p, kind: call.name });
        if (p.role) popup.append(el("div", "popup-role", p.role === "alternative" ? "Alternative" : "Suggested stop"));
        const link = el("a", "", "Open in Google Maps");
        link.href = p.map_url || `https://www.google.com/maps/search/?api=1&query=${p.lat},${p.lon}`;
        link.target = "_blank";
        link.rel = "noopener";
        popup.append(el("br"), link);
        const marker = L.circleMarker([p.lat, p.lon], { radius: 7, color: "#fffdf8", weight: 2, fillColor: meta.color, fillOpacity: 1 })
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
        const candidates = data.match_status === "candidates";
        const v = el("div", "stay-verdict " + (ok ? "ok" : "warn"));
        v.append(el("span", "tag", ok ? "Verified" : candidates ? "Check address" : "Not found"));
        if (candidates) {
            v.append(el("b", "", "Similar registered properties found; choose the matching address."));
        } else {
            v.append(el("b", "", ok ? `Registered: ${displayName(top)}` : `No registration found for "${english(data.query, "this stay")}"`));
            v.append(el("small", "", ok ? [licenseLabel(top.license_number), withoutHan(top.license_type, "Registered lodging")].filter(Boolean).join(" · ")
                : "Ask the host for their licence number."));
        }
        box.append(v);
        if (candidates) {
            for (const candidate of data.matches || []) {
                box.append(el("div", "candidate", `${displayName(candidate)} · ${english(candidate.address, cityLabel(candidate.district) || "Address in Chinese only")}`));
            }
        }
        const note = withoutHan(data.note || data.advice);
        if (note) box.append(el("small", "", note));
    } else if (data.stays) {
        const list = el("ul", "stay-list");
        for (const s of data.stays) {
            const li = el("li");
            const name = el("b", "", displayName(s));
            if (s.taiwan_host_certified) name.append(el("span", "badge", "Taiwan Host"));
            li.append(name);
            li.append(el("span", "price", s.reference_price_twd ? `from ${s.reference_price_twd} TWD` : "Rate not listed"));
            li.append(el("div", "lic", licenseLabel(s.license_number)));
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
    const trend = data.diff_percent > 0 ? `up ${data.diff_percent}%` : data.diff_percent < 0 ? `down ${Math.abs(data.diff_percent)}%` : "flat";
    boardNode("budget-body").replaceChildren(b, el("div", "budget-meta", `${data.rate_text} on ${data.rate_date} · against weekly samples over four weeks: ${data.diff_percent == null ? "Comparison unavailable" : trend}`));
    boardNode("budget-card").hidden = false;
}

const RISK_LABELS = { high: "Busy", medium: "Moderate", low: "Normal" };

// Holiday names come translated from the server; anything left in Chinese gets a generic label.
function dayLabel(d) {
    if (d.is_holiday || d.holiday_name) return english(d.holiday_name_en, english(d.holiday_name, "Public holiday"));
    if (d.calendar_pattern === "working_weekend") return english(d.calendar_note_en, "Make-up workday");
    return RISK_LABELS[d.risk] || "";
}

function showDates(data) {
    const days = Array.isArray(data) ? data : data.days || [];
    if (!days.length) { boardNode("dates-body").replaceChildren(el("p", "", "No calendar results for this range.")); return; }
    const strip = boardNode("dates-body");
    strip.replaceChildren();
    for (const d of days) {
        const cell = el("div", "day " + (d.risk || ""));
        const label = dayLabel(d);
        cell.title = [label, RISK_LABELS[d.risk] && `Travel pressure: ${RISK_LABELS[d.risk].toLowerCase()}`, english(d.reason)].filter(Boolean).join(". ");
        cell.append(el("span", "wd", d.weekday || ""), el("b", "", (d.date || "").slice(5)), el("span", "what", label));
        strip.append(cell);
    }
    boardNode("dates-note").textContent = english(data.risk_basis);
    boardNode("dates-card").hidden = false;
}

function showTrains(data) {
    const body = boardNode("train-body");
    // The section heading names the route and date; this line adds the service.
    const route = el("div", "train-route", data.rail === "TRA" ? "Taiwan Railway (TRA)" : data.rail === "THSR" ? "High Speed Rail (THSR)" : english(data.rail));
    const list = el("ul", "train-list");
    for (const train of data.trains || []) {
        const item = el("li");
        item.append(el("div", "train-time", `${train.departure} → ${train.arrival}`));
        item.append(el("div", "train-type", `${withoutHan(train.train_type, "Train")} ${train.train_no}`));
        item.append(el("div", "train-fare", train.fare_twd == null ? "Fare unavailable" : `${train.fare_twd.toLocaleString()} TWD`));
        list.append(item);
    }
    body.replaceChildren(route, list);
    if (!data.trains?.length) body.append(el("p", "", "No trains found for this search."));
    boardNode("train-card").hidden = false;
}

// Food and sights: the answer's picks first, then other returned listings, all in English.
const PLACE_LIMIT = 6;
const AWARD_NAMES = [["500碗", "500 Bowls"], ["500盤", "500 Dishes"]];

function placeList(data, describe) {
    const results = [...(data.results || [])].sort((a, b) => Number(Boolean(b.suggested)) - Number(Boolean(a.suggested)));
    const list = el("ul", "place-list");
    for (const r of results.slice(0, PLACE_LIMIT)) {
        const item = el("li", r.suggested ? "suggested" : "");
        const name = el("b", "", displayName(r));
        if (r.suggested) name.append(el("span", "badge", "Suggested"));
        item.append(name, el("div", "place-meta", describe(r).filter(Boolean).join(" · ")));
        list.append(item);
    }
    const nodes = [list];
    if (!results.length) nodes.push(el("p", "", "No places found for this search."));
    else if (results.length > PLACE_LIMIT) nodes.push(el("small", "place-more", `${results.length - PLACE_LIMIT} more in the lookup details`));
    return nodes;
}

function showFood(data) {
    const award = (text) => withoutHan(AWARD_NAMES.reduce((t, [zh, en]) => t.replace(zh, en), String(text)));
    const nodes = placeList(data, (r) => [r.district_en, r.price, ...(r.awards || []).slice(0, 2).map(award), english(r.open_time)]);
    // Rotating night markets: which evenings each one opens.
    const tips = (data.local_tips || []).filter((t) => t.open_days);
    if (tips.length) {
        const days = el("ul", "place-list");
        for (const t of tips) {
            const item = el("li");
            item.append(el("b", "", withoutHan(t.name, "Night market")), el("div", "place-meta", english(t.open_days)));
            days.append(item);
        }
        nodes.push(el("div", "label-row", "Night market days"), days);
    }
    boardNode("food-body").replaceChildren(...nodes);
    boardNode("food-card").hidden = false;
}

function showSights(data) {
    boardNode("sights-body").replaceChildren(...placeList(data, (r) => [r.district_en, (r.categories || []).slice(0, 2).join(", ")]));
    boardNode("sights-card").hidden = false;
}

const OUTING_ADVICE = { indoor: "Indoors", flexible: "Flexible", outdoor: "Outdoors fine" };

function showWeather(data) {
    const comparison = data.comparison || {};
    const bad = data.is_bad_weather === true;
    const f = data.forecast || {};
    const text = comparison.reason || data.seasonal_note || (data.typhoon_alert
        ? "An active typhoon warning is in effect. Follow official updates."
        : english(f.weather_en, "See the answer for the forecast."));
    // The section heading names the city and date.
    const head = el("div", "weather-head");
    head.append(el("b", "", english(f.weather_en, data.forecast ? "Forecast" : "No forecast yet")));
    if (f.min_temp_c != null && f.max_temp_c != null) head.append(el("span", "weather-temp", `${f.min_temp_c}–${f.max_temp_c} °C`));
    const details = [head];
    details.push(el("div", "alert" + (bad ? "" : " calm"), english(text, "Forecast unavailable.")));
    if (comparison.outing_window) {
        const w = comparison.outing_window;
        details.push(el("div", "weather-extra", `Outing: ${w.start}–${w.end} (Taiwan time)`));
    }
    const periods = el("ul", "periods");
    for (const p of comparison.periods || []) {
        const row = el("li");
        row.append(el("span", "", `${p.outing_start}–${p.outing_end}`),
            el("span", "advice", [english(p.weather_en), OUTING_ADVICE[p.activity_preference]].filter(Boolean).join(" · ")),
            el("span", "rain", p.rain_chance == null ? "n/a" : `${p.rain_chance}%`));
        periods.append(row);
    }
    if (periods.children.length) details.push(el("div", "label-row", "Rain chance by forecast period"), periods);
    if (comparison.warning_note) details.push(el("div", "weather-extra", comparison.warning_note));
    boardNode("weather-body").replaceChildren(...details);
    boardNode("weather-card").hidden = false;
}

const boardKinds = {
    legal_stay_check: ["stay", showStay], twd_exchange: ["budget", showBudget],
    crowd_risk_check: ["dates", showDates], hsr_trip_planner: ["train", showTrains],
    typhoon_backup_plan: ["weather", showWeather],
    find_local_food: ["food", showFood], find_attractions: ["sights", showSights],
};

function scopeKey(call) {
    const a = call.args || {};
    const fields = {
        legal_stay_check: ["city", "district", "name", "type"],
        hsr_trip_planner: ["rail", "origin", "destination", "date", "depart_after", "depart_before", "arrive_by"],
        typhoon_backup_plan: ["city", "date", "start_time", "end_time", "available_minutes"],
        crowd_risk_check: ["start_date", "end_date"], twd_exchange: ["currency", "direction"],
        find_local_food: ["city", "keyword", "district", "names", "dietary", "style"],
        find_attractions: ["city", "keyword", "district", "names", "interests", "setting"],
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
        const suggested = new Set([...places, ...mapPins].filter((p) => (p.tool || p.kind) === call.name).map((p) => p.name));
        const enriched = { ...data };
        for (const key of ["stays", "matches"]) if (data[key]) enriched[key] = data[key].map((p) => ({ ...p, name_en: english(labels.get(p.name)) || p.name_en }));
        // Mark the listings the answer recommended so the panel can show them first.
        if (data.results) enriched.results = data.results.map((r) => ({ ...r, suggested: suggested.has(r.name) }));
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
refreshQuota();
restore();
