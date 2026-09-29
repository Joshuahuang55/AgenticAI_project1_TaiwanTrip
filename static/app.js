// Taiwan Like a Local: chat + trip board. The board is drawn only from /chat's tool_calls results.

const $ = (id) => document.getElementById(id);
const messages = $("messages");
const input = $("user-input");
const sendBtn = $("send-btn");

// sessionStorage is per tab: a refresh keeps the conversation, a new tab starts a separate one.
let sessionId = sessionStorage.getItem("session_id");

// How each tool looks in the chat and on the map. Unknown tools still render with defaults.
const TOOL_META = {
    legal_stay_check: { icon: "🏠", color: "#0f766e", label: "Stays", gist: (a) => a.name ? `is "${a.name}" registered?` : `registered ${a.type || "stays"} in ${a.city}` },
    find_local_food: { icon: "🍜", color: "#c8102e", label: "Food", gist: (a) => `${a.keyword || "food"} in ${a.city}` },
    twd_exchange: { icon: "💱", color: "#b45309", gist: (a) => `${a.amount} ${a.direction === "from_twd" ? "TWD → " + (a.currency || "USD") : (a.currency || "USD") + " → TWD"}` },
    find_attractions: { icon: "🏯", color: "#7c3aed", label: "Sights", gist: (a) => `${a.keyword || "sights"} in ${a.city}` },
    hsr_trip_planner: { icon: "🚄", color: "#2563eb", gist: (a) => `${a.origin} → ${a.destination} on ${a.date}` },
    crowd_risk_check: { icon: "📅", color: "#be123c", gist: (a) => `${a.start_date} → ${a.end_date}` },
    typhoon_backup_plan: { icon: "🌀", color: "#0369a1", label: "Backup", gist: (a) => `${a.city} on ${a.date}` },
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
    if (role === "assistant") div.innerHTML = DOMPurify.sanitize(marked.parse(content));
    else div.textContent = content;
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
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
    if (!text) return;
    input.value = "";
    sendBtn.disabled = true;
    addMessage("user", text);
    const loading = addMessage("assistant", "Asking around like a local…");
    loading.classList.add("loading");

    try {
        const res = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text, session_id: sessionId }),
        });
        if (!res.ok) throw new Error(`Server returned ${res.status}`);
        const data = await res.json();
        sessionId = data.session_id;
        sessionStorage.setItem("session_id", sessionId);
        renderToolCalls(data.tool_calls || [], loading);
        updateBoard(data.tool_calls || []);
        loading.classList.remove("loading");
        loading.innerHTML = DOMPurify.sanitize(marked.parse(data.response || "(no answer)"));
    } catch (e) {
        loading.classList.remove("loading");
        loading.textContent = "Could not reach the agent: " + e.message;
    }
    sendBtn.disabled = false;
    input.focus();
    messages.scrollTop = messages.scrollHeight;
}

$("composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });
document.querySelectorAll(".example").forEach((b) => b.addEventListener("click", () => send(b.textContent)));

$("new-trip").addEventListener("click", async () => {
    if (sessionId) await fetch(`/clear?session_id=${encodeURIComponent(sessionId)}`, { method: "POST" });
    sessionStorage.removeItem("session_id");
    location.reload();
});

$("panel-toggle").addEventListener("click", () => {
    const panel = $("panel");
    panel.classList.toggle("open");
    $("panel-toggle").textContent = panel.classList.contains("open") ? "Hide trip board" : "Show trip board";
    map.invalidateSize();
});

// --- Trip board ---

const map = L.map("map", { scrollWheelZoom: false }).setView([23.7, 120.95], 7);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);
const pinLayer = L.layerGroup().addTo(map);
const legendSeen = new Set();

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
        popup.append(el("b", "", `${meta.icon} ${p.name || "Place"}`));
        for (const line of [p.license_number, p.address, p.reference_price_twd && `~${p.reference_price_twd} TWD/night`, p.open_time]) {
            if (line) popup.append(el("br"), document.createTextNode(line));
        }
        return L.circleMarker([p.lat, p.lon], { radius: 7, color: "#fff", weight: 2, fillColor: meta.color, fillOpacity: 0.95 })
            .bindPopup(popup)
            .addTo(pinLayer);
    });
}

// A check verdict and a list of registered alternatives can arrive in the same reply; show both.
function showStay(data) {
    const slot = data.mode === "check" ? "stay-verdict-slot" : "stay-list-slot";
    let box = $(slot);
    if (!box) {
        box = el("div");
        box.id = slot;
        if (slot === "stay-verdict-slot") $("stay-body").prepend(box);
        else $("stay-body").append(box);
    }
    box.replaceChildren();
    if (data.mode === "check") {
        const ok = data.is_registered;
        const top = ok ? data.matches[0] : null;
        const v = el("div", "stay-verdict " + (ok ? "ok" : "warn"));
        v.append(el("span", "big", ok ? "✅" : "⚠️"));
        const txt = el("div");
        txt.append(el("b", "", ok ? `Registered: ${top.name}` : `No registration found for "${data.query}"`));
        txt.append(el("small", "", ok ? `${top.license_number} · ${top.license_type}` : "Ask the host for their license number (登記證號)."));
        v.append(txt);
        box.append(v);
    } else if (data.stays) {
        const list = el("ul", "stay-list");
        for (const s of data.stays) {
            const li = el("li");
            li.append(el("b", "", s.name));
            if (s.taiwan_host_certified) li.append(el("span", "badge", "Taiwan Host"));
            li.append(el("div", "lic", `✔ ${s.license_number}`));
            if (s.reference_price_twd) li.append(el("div", "", `~${s.reference_price_twd} TWD / night`));
            list.append(li);
        }
        box.append(list);
    }
    $("stay-card").hidden = false;
}

function showBudget(data) {
    const b = el("div", "budget");
    b.append(el("span", "from", `${data.amount.toLocaleString()} ${data.direction === "to_twd" ? data.currency : "TWD"} =`),
        el("span", "to", `${data.converted_amount.toLocaleString()} ${data.converted_currency}`));
    const trend = data.diff_percent > 0 ? `▲ ${data.diff_percent}%` : data.diff_percent < 0 ? `▼ ${Math.abs(data.diff_percent)}%` : "flat";
    $("budget-body").replaceChildren(b, el("div", "budget-meta", `${data.rate_text} on ${data.rate_date} · vs 30-day avg: ${trend}`));
    $("budget-card").hidden = false;
}

function showDates(data) {
    const days = Array.isArray(data) ? data : data.days || [];
    if (!days.length) return;
    const strip = $("dates-body");
    strip.replaceChildren();
    for (const d of days) {
        const cell = el("div", "day " + (d.risk || ""));
        cell.title = d.reason || d.holiday_name || "";
        cell.append(el("span", "", d.weekday || ""), el("b", "", (d.date || "").slice(5)), el("span", "", d.holiday_name || d.risk || ""));
        strip.append(cell);
    }
    $("dates-card").hidden = false;
}

function showTrains(data) {
    const body = $("train-body");
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
    $("train-card").hidden = false;
}

function showWeather(data) {
    const bad = data.is_bad_weather || (data.typhoon_alert && data.typhoon_alert !== "none");
    const f = data.forecast || {};
    const text = bad
        ? `⚠ ${typeof data.typhoon_alert === "string" ? data.typhoon_alert : "Bad weather expected"}. Indoor backups are on the map.`
        : `☀ ${f.weather || "Looks fine"}${f.rain_chance !== undefined ? ` · rain ${f.rain_chance}%` : ""}`;
    $("weather-body").replaceChildren(el("div", "alert" + (bad ? "" : " calm"), text));
    $("weather-card").hidden = false;
}

function updateBoard(calls) {
    const newPins = [];
    for (const call of calls) {
        const data = parse(call.result);
        if (!data || data.error) continue;
        newPins.push(...addPins(call, data));
        if (call.name === "legal_stay_check") showStay(data);
        if (call.name === "twd_exchange") showBudget(data);
        if (call.name === "crowd_risk_check") showDates(data);
        if (call.name === "hsr_trip_planner" && data.trains?.length) showTrains(data);
        if (call.name === "typhoon_backup_plan") showWeather(data);
    }
    if (newPins.length) map.fitBounds(L.featureGroup(newPins).getBounds().pad(0.3), { maxZoom: 14 });
    const anyCard = ["stay-card", "dates-card", "train-card", "weather-card", "budget-card"].some((id) => !$(id).hidden);
    $("panel-hint").hidden = anyCard || pinLayer.getLayers().length > 0;
}

input.focus();
