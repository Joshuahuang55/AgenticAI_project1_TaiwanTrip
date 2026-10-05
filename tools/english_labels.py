"""English labels for Chinese text that official feeds return (calendar notes, CWA weather).

Each function returns None when it cannot translate the whole text, so callers never show a
half-translated label; the frontend then uses a generic English label.
"""

import re

# Longest names first so "農曆除夕" wins over "除夕".
HOLIDAYS = sorted({
    "中華民國開國紀念日": "Republic Day", "開國紀念日": "Republic Day", "元旦": "New Year's Day",
    "農曆除夕": "Lunar New Year's Eve", "除夕": "Lunar New Year's Eve",
    "小年夜": "Day before Lunar New Year's Eve", "春節": "Lunar New Year", "農曆新年": "Lunar New Year",
    "和平紀念日": "Peace Memorial Day", "兒童節": "Children's Day", "婦女節": "Women's Day",
    "民族掃墓節": "Tomb Sweeping Day", "清明節": "Tomb Sweeping Day", "勞動節": "Labor Day",
    "端午節": "Dragon Boat Festival", "中秋節": "Mid-Autumn Festival", "國慶日": "National Day",
    "孔子誕辰紀念日": "Teachers' Day", "教師節": "Teachers' Day", "軍人節": "Armed Forces Day",
    "臺灣光復暨金門古寧頭大捷紀念日": "Taiwan Retrocession Day", "臺灣光復節": "Taiwan Retrocession Day",
    "光復節": "Taiwan Retrocession Day", "行憲紀念日": "Constitution Day",
    "原住民族日": "Indigenous Peoples' Day", "農民節": "Farmers' Day",
}.items(), key=lambda item: -len(item[0]))
QUALIFIERS = {"補假": "observed", "調整放假": "bridge holiday", "放假": None, "前一日": "eve", "逢": None}
JOINERS = "及、與和暨,， "


def holiday_en(note: str | None) -> str | None:
    """Translate a DGPA calendar note such as '國慶日補假' or '補行上班'."""
    text = (note or "").strip().replace("台", "臺")
    if not text:
        return None
    if text in ("補行上班", "調整上班"):
        return "Make-up workday"
    if text == "補假":
        return "Day off (observed holiday)"
    if text == "調整放假":
        return "Bridge holiday"
    names, notes = [], []
    while text:
        for zh, en in HOLIDAYS:
            if text.startswith(zh):
                if en not in names:
                    names.append(en)
                text = text[len(zh):]
                break
        else:
            for zh, en in QUALIFIERS.items():
                if text.startswith(zh):
                    if en and en not in notes:
                        notes.append(en)
                    text = text[len(zh):]
                    break
            else:
                if text[0] in JOINERS:
                    text = text[1:]
                    continue
                return None
    if not names:
        return None
    label = " & ".join(names)
    return f"{label} ({', '.join(notes)})" if notes else label


SKY = {"晴": "sunny", "多雲": "partly cloudy", "陰": "overcast"}
# Precipitation and other phenomena; longest first.
PHENOMENA = sorted({
    "短暫陣雨或雷雨": "brief showers or thunderstorms", "陣雨或雷雨": "showers or thunderstorms",
    "短暫雷陣雨": "brief thunderstorms", "雷陣雨": "thunderstorms", "有雷雨": "thunderstorms",
    "雷雨": "thunderstorms", "短暫陣雨": "brief showers", "陣雨": "showers", "短暫雨": "brief rain",
    "局部短暫雨": "brief local rain", "局部雨": "local rain", "有雨": "rain", "雨": "rain",
    "雨天": "rain", "有霧": "fog", "霧": "fog", "有靄": "haze", "有雪": "snow", "雪": "snow", "雨或雪": "rain or snow",
    "大雨": "heavy rain", "豪雨": "torrential rain",
}.items(), key=lambda item: -len(item[0]))
SKY_PATTERN = re.compile(r"(晴|多雲|陰)天?(?:時(晴|多雲|陰))?")


def weather_en(text: str | None) -> str | None:
    """Translate a CWA weather phenomenon such as '多雲時陰短暫陣雨' or '晴午後短暫雷陣雨'."""
    rest = (text or "").strip()
    if not rest:
        return None
    parts = []
    sky = SKY_PATTERN.match(rest)
    if sky:
        first, second = SKY[sky.group(1)], sky.group(2) and SKY[sky.group(2)]
        parts.append(f"{first}, at times {second}" if second else first)
        rest = rest[sky.end():]
    while rest:
        prefix = ""
        for zh, en in (("午後", "afternoon "), ("局部", "local "), ("偶", "occasional ")):
            if rest.startswith(zh) and not any(rest.startswith(p) for p, _ in PHENOMENA):
                prefix, rest = en, rest[len(zh):]
                break
        for zh, en in PHENOMENA:
            if rest.startswith(zh):
                parts.append(prefix + en)
                rest = rest[len(zh):]
                break
        else:
            if rest[0] in "或及、，, ":
                rest = rest[1:]
                continue
            return None
    if not parts:
        return None
    label = " with ".join([parts[0], " and ".join(parts[1:])]) if len(parts) > 1 else parts[0]
    return label[0].upper() + label[1:]
