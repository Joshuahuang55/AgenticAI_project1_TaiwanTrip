"""Local planning heuristics, separate from reported attraction hours and prices."""

import math

INTERESTS = ("history", "art", "nature", "hiking", "shopping", "culture", "museums", "temples")
SETTINGS = ("any", "indoor", "outdoor")
NEARBY_KM = 2.0
TRANSFER_MINUTES = 20  # Planning allowance, not a measured journey time.


def validate(interests, setting, available_minutes):
    if not isinstance(interests, (list, tuple)) or any(i not in INTERESTS for i in interests):
        raise ValueError(f"interests must be a list drawn from {', '.join(INTERESTS)}")
    if setting not in SETTINGS:
        raise ValueError("setting must be any, indoor, or outdoor")
    if available_minutes is not None and (
        isinstance(available_minutes, bool) or not isinstance(available_minutes, int)
        or not 15 <= available_minutes <= 720
    ):
        raise ValueError("available_minutes must be an integer from 15 to 720 (total outing time)")


def profile(row):
    """Infer broad interests and typical visit ranges; name cues override noisy class codes."""
    name = row.get("AttractionName") or ""
    description = row.get("Description") or ""
    classes = set(row.get("AttractionClasses") or [])
    has = lambda words: any(w in name for w in words)
    creative = has(("文化創意", "文創", "創意產業", "藝術園"))
    memorial = "紀念堂" in name
    museum = has(("博物館", "博物院", "美術館", "文物館", "紀念館", "文化館", "展示館", "教育館", "藝廊"))
    museum = museum or (25 in classes and not creative and not memorial and not has(("步道", "公園", "海灘")))
    temple = has(("廟", "寺", "祠", "代天府")) or ("宮" in name and "故宮" not in name) or 4 in classes
    trail = has(("步道", "登山", "山徑"))
    nature = not museum and (has(("公園", "森林", "瀑布", "海灘", "海岸", "峽谷", "濕地", "花園", "湖", "潭", "觀景"))
                            or bool(classes & {2, 7, 8, 11, 15, 16, 17, 19, 24}) or trail)
    if museum and (2 in classes or any(w in name + description for w in (
        "自然史", "自然歷史", "自然科學", "生物多樣性", "生態展", "地質展", "動植物", "海洋生物"
    ))):
        nature = True  # Indoor nature collections differ from merely having a garden nearby.
    history = memorial or 3 in classes or has(("古蹟", "古堡", "砲臺", "炮臺", "城牆", "歷史", "故居", "老街"))
    art = creative or 5 in classes or has(("美術", "藝術", "藝廊", "雕塑"))
    shopping = 6 in classes or has(("老街", "市場", "市集", "夜市", "商圈"))
    culture = bool(classes & {1, 21, 22}) or museum or temple or history or art
    matched = [i for i, yes in (("history", history), ("art", art), ("nature", nature),
                               ("hiking", trail), ("shopping", shopping), ("culture", culture),
                               ("museums", museum), ("temples", temple)) if yes]
    if museum:
        setting, duration, kind = "indoor", (60, 120), "museum/gallery"
    elif creative:
        setting, duration, kind = "mixed", (60, 120), "creative park/exhibitions"
    elif trail:
        setting, duration, kind = "outdoor", (90, 180), "trail visit"
    elif 20 in classes or has(("樂園", "動物園")):
        setting, duration, kind = "outdoor", (180, 360), "theme park/zoo"
    elif has(("水族館", "海生館")):
        setting, duration, kind = "indoor", (90, 150), "aquarium"
    elif temple:
        setting, duration, kind = "mixed", (30, 60), "temple visit"
    elif shopping:
        setting, duration, kind = "outdoor", (45, 90), "street/market stroll"
    elif history:
        setting, duration, kind = "mixed", (45, 90), "heritage visit"
    elif nature:
        setting, duration, kind = "outdoor", ((120, 240) if 7 in classes else (45, 90)), "scenery/park visit"
    elif art:
        setting, duration, kind = "mixed", (45, 90), "art site"
    else:
        setting, duration, kind = "unknown", (45, 90), "general sightseeing stop"
    return {"interests": matched, "setting": setting,
            "visit_minutes": {"min": duration[0], "max": duration[1], "typical": (sum(duration) // 10) * 5},
            "basis": kind, "status": "estimated", "source": "Category/name planning heuristics"}


def fit(row, interests, setting, available_minutes):
    info = profile(row)
    matched = [i for i in interests if i in info["interests"]]
    setting_fit = ("not_requested" if setting == "any" else "match" if info["setting"] == setting
                   else "partial" if info["setting"] == "mixed" else "unknown" if info["setting"] == "unknown"
                   else "different_setting")
    typical = info["visit_minutes"]["typical"]
    return {"matched_interests": matched, "setting_fit": setting_fit,
            "fits_typical_visit": None if available_minutes is None else typical <= available_minutes}


def _score(row, interests, setting, available_minutes):
    match = fit(row, interests, setting, available_minutes)
    setting_score = {"not_requested": 0, "match": 3, "partial": 2, "unknown": 1, "different_setting": 0}
    return (match["fits_typical_visit"] is not False, setting_score[match["setting_fit"]],
            bool(match["matched_interests"]), len(match["matched_interests"]))


def rank(rows, interests, setting, available_minutes):
    """Stable ranking keeps existing fame/local order for equally suitable candidates."""
    return sorted(rows, key=lambda row: _score(row, interests, setting, available_minutes), reverse=True)


def distance_km(a, b):
    """Straight-line distance; absent/invalid coordinates must not create a nearby claim."""
    points = []
    for row in (a, b):
        lat, lon = row.get("PositionLat"), row.get("PositionLon")
        if (isinstance(lat, bool) or isinstance(lon, bool)
                or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float))
                or not math.isfinite(lat) or not math.isfinite(lon)
                or not -90 <= lat <= 90 or not -180 <= lon <= 180 or (lat == 0 and lon == 0)):
            return None
        points.append((math.radians(lat), math.radians(lon)))
    (lat1, lon1), (lat2, lon2) = points
    value = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(min(1, value)))


def nearby_groups(rows):
    """Every pair in a group is within the threshold; avoid chains spanning the city."""
    groups = []
    for row in rows:
        target = next((group for group in groups if all(
            (d := distance_km(row, other)) is not None and d <= NEARBY_KM for other in group)), None)
        if target is None:
            groups.append([row])
        else:
            target.append(row)
    return [{"names": [r["AttractionName"] for r in group],
             "max_straight_line_km": round(max(distance_km(a, b) for i, a in enumerate(group)
                                               for b in group[i + 1:]), 2),
             "basis": "Coordinates, not walking routes"}
            for group in groups if len(group) > 1]


def _reason(row, interests, setting, available_minutes):
    info, match = profile(row), fit(row, interests, setting, available_minutes)
    parts = []
    if interests:
        parts.append("Matches " + ", ".join(match["matched_interests"]) if match["matched_interests"]
                     else "A broader sightseeing alternative to your requested interests")
    if setting != "any":
        parts.append({"match": f"Suited to your {setting} preference", "partial": "Has indoor and outdoor parts",
                      "unknown": "Setting is unclear from its category", "different_setting": f"Mostly {info['setting']}"}
                     [match["setting_fit"]])
    parts.append(f"Allow roughly {info['visit_minutes']['min']}–{info['visit_minutes']['max']} minutes")
    if available_minutes is not None and not match["fits_typical_visit"]:
        parts.append("A typical visit would need more than your available time")
    return "; ".join(parts)


def _transfer(a, b):
    distance = distance_km(a, b)
    minutes = (45 if distance is None else TRANSFER_MINUTES if distance <= 2 else 35 if distance <= 6
               else 45 if distance <= 12 else 60)
    return {"from": a["AttractionName"], "to": b["AttractionName"], "estimated_minutes": minutes,
            "straight_line_km": None if distance is None else round(distance, 2),
            "status": "estimated", "basis": "City transfer allowance, not a checked transit route"}


def outing(rows, interests, setting, available_minutes):
    """Build a short sequential outing, allowing city transfers beyond the nearby-group radius."""
    eligible = [r for r in rows if fit(r, interests, setting, available_minutes)["fits_typical_visit"]]
    if eligible and fit(eligible[0], interests, setting, available_minutes)["matched_interests"]:
        eligible = [r for r in eligible if fit(r, interests, setting, available_minutes)["matched_interests"]]
    if setting != "any" and any(profile(r)["setting"] in (setting, "mixed") for r in eligible):
        eligible = [r for r in eligible if profile(r)["setting"] in (setting, "mixed")]
    picked, transfers, total = [], [], 0
    while eligible and len(picked) < 3:
        if picked:
            covered = {i for r in picked for i in profile(r)["interests"]}
            # Complement the first stop's interests, then prefer a short transfer.
            eligible.sort(key=lambda r: (
                -len((set(profile(r)["interests"]) & set(interests)) - covered),
                _transfer(picked[-1], r)["estimated_minutes"]))
        next_row = None
        for row in eligible:
            transfer = _transfer(picked[-1], row) if picked else None
            if transfer and transfer["straight_line_km"] is not None and transfer["straight_line_km"] > 25:
                continue
            added = profile(row)["visit_minutes"]["typical"] + (transfer["estimated_minutes"] if transfer else 0)
            break_allowance = 30 if available_minutes >= 240 and picked else 0
            if total + added + break_allowance <= available_minutes:
                next_row = row
                break
        if next_row is None:
            break
        if picked:
            transfers.append(_transfer(picked[-1], next_row))
        picked.append(next_row)
        total += added
        eligible.remove(next_row)
    visits = [profile(r)["visit_minutes"]["typical"] for r in picked]
    break_minutes = 30 if len(picked) >= 2 and available_minutes >= 240 else 0
    total += break_minutes
    # A half-day permits a fuller visit within the category range, rather than one quick stop.
    if available_minutes >= 240:
        while total + 5 <= available_minutes:
            changed = False
            for index, row in enumerate(picked):
                if total + 5 <= available_minutes and visits[index] + 5 <= profile(row)["visit_minutes"]["max"]:
                    visits[index] += 5
                    total += 5
                    changed = True
            if not changed:
                break
    timeline, elapsed = [], 0
    for index, row in enumerate(picked):
        if index:
            transfer = transfers[index - 1]
            duration = transfer["estimated_minutes"]
            timeline.append(dict(transfer, kind="transfer", start_minute=elapsed, end_minute=elapsed + duration))
            elapsed += duration
        duration = visits[index]
        info = profile(row)
        timeline.append({"kind": "visit", "name": row["AttractionName"], "estimated_minutes": duration,
                         "start_minute": elapsed, "end_minute": elapsed + duration, "setting": info["setting"],
                         "focus": "Indoor exhibitions; outdoor grounds optional" if setting == "indoor"
                         and info["setting"] == "mixed" and "art" in info["interests"] else info["basis"]})
        elapsed += duration
        if index == 0 and break_minutes:
            timeline.append({"kind": "break", "estimated_minutes": break_minutes,
                             "start_minute": elapsed, "end_minute": elapsed + break_minutes,
                             "activity": "Coffee/snack break"})
            elapsed += break_minutes
    for step in timeline:
        start, end = step["start_minute"], step["end_minute"]
        step["relative_time"] = f"{start // 60}:{start % 60:02d}–{end // 60}:{end % 60:02d}"
    return {"names": [r["AttractionName"] for r in picked], "timeline": timeline,
            "estimated_minutes": elapsed, "available_minutes": available_minutes,
            "remaining_minutes": available_minutes - elapsed,
            "transfer_allowance_minutes": sum(t["estimated_minutes"] for t in transfers),
            "break_minutes": break_minutes, "status": "estimated",
            "basis": "Estimated visits and city transfers; excludes travel to/from the area and opening-hours checks"}


def compare(rows, interests, setting, available_minutes):
    if not rows:
        return {"recommended_name": None, "alternatives": []}
    best = rows[0]
    groups = nearby_groups(rows)
    out = {"recommended_name": best["AttractionName"],
           "reason": _reason(best, interests, setting, available_minutes),
           "alternatives": [], "nearby_groups": groups,
           "planning_note": "Visit durations are category estimates; distances are straight-line, not walking routes."}
    best_info = profile(best)
    for row in rows[1:]:
        info = profile(row)
        differences = []
        tied = bool(interests or setting != "any" or available_minutes is not None) and (
            _score(row, interests, setting, available_minutes) == _score(best, interests, setting, available_minutes))
        if tied:
            differences.append("Equally matches your preferences; existing listing ranking breaks the tie")
        if info["setting"] != best_info["setting"]:
            differences.append(f"{info['setting']} setting instead of {best_info['setting']}")
        extra = [i for i in info["interests"] if i not in best_info["interests"]]
        if extra:
            differences.append("Adds " + ", ".join(extra))
        delta = info["visit_minutes"]["typical"] - best_info["visit_minutes"]["typical"]
        if delta:
            differences.append(f"Typical visit about {abs(delta)} minutes {'longer' if delta > 0 else 'shorter'}")
        match = fit(row, interests, setting, available_minutes)
        if interests and len(match["matched_interests"]) < len(fit(best, interests, setting, available_minutes)["matched_interests"]):
            differences.append("Matches fewer of your interests")
        d = distance_km(best, row)
        if d is not None:
            differences.append(f"About {d:.1f} km from the first pick in a straight line")
        out["alternatives"].append({"name": row["AttractionName"],
                                    "equally_suitable": tied,
                                    "reason": _reason(row, interests, setting, available_minutes),
                                    "trade_offs": differences or ["Similar fit; lower existing popularity/local ranking"]})
    if available_minutes is not None:
        out["suggested_visit"] = outing(rows, interests, setting, available_minutes)
    return out
