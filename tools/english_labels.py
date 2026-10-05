"""English labels for Chinese text that official feeds return (calendar notes, CWA weather,
listing names).

holiday_en and weather_en return None when they cannot translate the whole text, so callers
never show a half-translated label. english_name always returns Latin text: known words are
translated and the rest is romanized with Hanyu Pinyin, Taiwan's official romanization.
"""

import re

from pypinyin import Style, lazy_pinyin

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


# Words translated inside listing names; longest first so "大飯店" wins over "飯店".
NAME_WORDS = sorted({
    # Places
    "臺北": "Taipei", "新北": "New Taipei", "桃園": "Taoyuan", "臺中": "Taichung", "臺南": "Tainan",
    "高雄": "Kaohsiung", "基隆": "Keelung", "新竹": "Hsinchu", "苗栗": "Miaoli", "彰化": "Changhua",
    "南投": "Nantou", "雲林": "Yunlin", "嘉義": "Chiayi", "屏東": "Pingtung", "宜蘭": "Yilan",
    "花蓮": "Hualien", "臺東": "Taitung", "澎湖": "Penghu", "金門": "Kinmen", "馬祖": "Matsu",
    "墾丁": "Kenting", "九份": "Jiufen", "淡水": "Tamsui", "日月潭": "Sun Moon Lake", "阿里山": "Alishan",
    "太魯閣": "Taroko", "安平": "Anping", "西門": "Ximen", "鹿港": "Lukang", "礁溪": "Jiaoxi",
    "北投": "Beitou", "士林": "Shilin", "信義": "Xinyi", "大稻埕": "Dadaocheng", "知本": "Zhiben",
    # Stays
    "國際觀光旅館": "International Hotel", "觀光大飯店": "Hotel", "大飯店": "Hotel", "飯店": "Hotel",
    "大酒店": "Hotel", "酒店": "Hotel", "青年旅館": "Hostel", "青年旅舍": "Hostel", "背包客棧": "Hostel",
    "膠囊旅館": "Capsule Hotel", "商務旅館": "Business Hotel", "商旅": "Business Hotel", "旅館": "Hotel",
    "旅店": "Inn", "旅社": "Inn", "客棧": "Inn", "會館": "Hotel", "渡假村": "Resort", "度假村": "Resort",
    "民宿": "B&B", "文旅": "Hotel", "溫泉": "Hot Spring",
    # Food
    "牛肉湯": "Beef Soup", "牛肉麵": "Beef Noodles", "擔仔麵": "Danzai Noodles", "肉圓": "Bawan",
    "滷肉飯": "Braised Pork Rice", "魯肉飯": "Braised Pork Rice", "雞肉飯": "Chicken Rice",
    "蚵仔煎": "Oyster Omelet", "臭豆腐": "Stinky Tofu", "豆花": "Douhua", "刈包": "Gua Bao",
    "小籠包": "Xiaolongbao", "水煎包": "Pan-fried Buns", "鍋貼": "Potstickers", "水餃": "Dumplings",
    "粥": "Congee", "米糕": "Rice Cake", "碗粿": "Wa Gui", "火鍋": "Hot Pot", "燒烤": "BBQ",
    "海鮮": "Seafood", "素食": "Vegetarian", "早餐": "Breakfast", "小吃": "Snacks", "冰品": "Shaved Ice",
    "剉冰": "Shaved Ice", "茶飲": "Tea", "咖啡": "Cafe", "餐廳": "Restaurant", "餐館": "Restaurant",
    "食堂": "Diner", "麵館": "Noodle House", "麵店": "Noodle Shop", "飯館": "Restaurant", "夜市": "Night Market",
    "老店": "Old Shop", "總店": "Main Store", "本店": "Main Store", "分店": "Branch", "店": "Shop",
    # Sights
    "老街": "Old Street", "博物館": "Museum", "美術館": "Art Museum", "紀念館": "Memorial Hall",
    "國家公園": "National Park", "公園": "Park", "步道": "Trail", "古道": "Trail", "廟": "Temple",
    "寺": "Temple", "宮": "Temple", "教堂": "Church", "車站": "Station", "港": "Harbor", "濕地": "Wetland",
    "瀑布": "Waterfall", "海灘": "Beach", "燈塔": "Lighthouse", "觀景台": "Viewpoint", "樓": "Tower",
    # Streets
    "路": "Rd.", "街": "St.", "巷": "Ln.",
}.items(), key=lambda item: -len(item[0]))
_HAN = re.compile(r"[\u3400-\u9fff]")
_PUNCT = str.maketrans({"（": " (", "）": ")", "　": " ", "、": ", ", "‧": " ", "・": " ", "－": "-", "＆": "&"})


def english_name(name: str | None) -> str | None:
    """Latin-script name for a listing, e.g. '你來花蓮民宿' -> 'Nilai Hualien B&B'."""
    if not name:
        return name
    text = re.sub(r"(\d+)\s*號", r" No. \1 ", name.strip().replace("台", "臺").translate(_PUNCT))
    if not _HAN.search(text):
        return text
    words, run, latin = [], "", ""

    def flush():
        nonlocal run, latin
        if run:
            words.append("".join(lazy_pinyin(run, style=Style.NORMAL, v_to_u=True)).capitalize())
        if latin:
            words.append(latin)
        run = latin = ""

    i = 0
    while i < len(text):
        match = next(((zh, en) for zh, en in NAME_WORDS if text.startswith(zh, i)), None)
        if match:
            flush()
            words.append(match[1])
            i += len(match[0])
        elif _HAN.match(text[i]):
            if latin:
                flush()
            run += text[i]
            i += 1
        else:
            if run:
                flush()
            latin += text[i]
            i += 1
    flush()
    # Join translated words with spaces, keeping punctuation and Latin text attached.
    out = ""
    for word in words:
        if out and not out[-1].isspace() and (word[0].isalnum() or word[0] in "(&") and out[-1] not in "(-":
            out += " "
        out += word
    return re.sub(r"\s+", " ", out).replace("( ", "(").strip()
