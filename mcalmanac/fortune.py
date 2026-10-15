"""黄历引擎：干支推算 + 确定性运势算法 + 签文池。

确定性设计：同一天（+同一份数据）必然得到同一份黄历 —— 这是黄历的「玄学底线」。
实现方式：以日期和数据摘要做 SHA-256，播种 random.Random。
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import date, datetime

from .data import LiveData

# ---------------------------------------------------------------------------
# 干支历（校验基准：1949-10-01 为甲子日；2000-01-01 为戊午日）
# ---------------------------------------------------------------------------

_TIANGAN = "甲乙丙丁戊己庚辛壬癸"
_DIZHI = "子丑寅卯辰巳午未申酉戌亥"
_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_CN_NUM = "〇一二三四五六七八九"


def julian_day_number(d: date) -> int:
    """Fliegel-Van Flandern 公式求儒略日数。"""
    a = (14 - d.month) // 12
    y = d.year + 4800 - a
    m = d.month + 12 * a - 3
    return d.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045


def day_ganzhi(d: date) -> str:
    """日干支。JDN(1949-10-01)=2433191 对应甲子，故偏移 49。"""
    index = (julian_day_number(d) + 49) % 60
    return _TIANGAN[index % 10] + _DIZHI[index % 12]


def year_ganzhi(year: int) -> str:
    """年干支（简化处理，不区分立春）。"""
    return _TIANGAN[(year - 4) % 10] + _DIZHI[(year - 4) % 12]


def _cn_number(n: int) -> str:
    """1..31 的中文写法。"""
    if n < 10:
        return _CN_NUM[n]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + _CN_NUM[n - 10]
    if n == 20:
        return "二十"
    if n < 30:
        return "二十" + _CN_NUM[n - 20]
    if n == 30:
        return "三十"
    return "三十" + _CN_NUM[n - 30]


def cn_date(d: date) -> str:
    """二〇二六年十月十五日 这样的中文日期。"""
    year = "".join(_CN_NUM[int(ch)] for ch in str(d.year))
    return f"{year}年{_cn_number(d.month)}月{_cn_number(d.day)}日"


# ---------------------------------------------------------------------------
# 签文池 —— 麦门风味，写作时的心法是「善意 + 好笑 + 有点准」
# ---------------------------------------------------------------------------

VERSES = {
    "上上": [
        "薯条在手，天下我有；可乐加冰，万事可行。",
        "板烧一出，万愁皆无；今日所愿，次第花开。",
        "麦旋风一转，霉运全散；甜筒拿稳，好运扎根。",
        "巨无霸有七层，好事也一层一层来；今日宜大展身手。",
        "今日金拱门下喜气盈门，诸事皆宜，尤其宜多买一盒麦乐鸡。",
        "咖啡最烫的第一口留给明天，好运最近的一班就在今天；宜果断。",
    ],
    "上": [
        "鸡块有五块，好事自会成双；蘸酱要够，心气要足。",
        "一杯咖啡下肚，灵感自会来；今日宜谋大事，忌拖到明天。",
        "汉堡有夹层，人生有转机；今日宜主动开口。",
        "可乐冒泡的样子，就是你今天的状态——向上，且带劲。",
        "早餐的圆满，是薯饼蘸蛋；今日宜早起一回，全天顺遂。",
        "麦辣有辣劲，做事要带劲；今日宜一鼓作气。",
        "冰淇淋不怕化，怕的是你不敢咬第一口；今日宜开始。",
        "薯条要趁热，机会要趁早；今日宜当机立断。",
        "大杯下肚，胆气自来；今日宜谈判，宜表白。",
        "芝士会拉丝，好事会连环；今日宜顺着势头再走一步。",
    ],
    "中": [
        "平平淡淡才是真，一堡一饮正合适；今日宜稳，忌浪。",
        "队伍有点长，好事在路上；今日宜耐心，忌催促。",
        "番茄酱不必挤太多，日子也不用排太满；今日宜留白。",
        "你点的餐还没好，你的时机还没到；今日宜等待，宜攒券。",
        "汽水的气跑了一半，快乐不打折；今日宜乐观，忌计较。",
        "有一根薯条短了一截，无妨；今日宜宽容，尤其对自己。",
        "今日宜收拾桌面，顺手也收拾一下心情。",
        "套餐里的中薯可以换大薯，小小的升级也是升级；今日宜对自己好一点。",
        "你看了热量表，又点了一份鸡块——这叫诚实面对自我；今日宜和解。",
        "今晚早睡十分钟，明天的你会谢谢今天的你；今日宜早睡。",
    ],
    "下": [
        "今日忌空腹逛菜单——看得越多，点得越多；先垫一垫再决定。",
        "优惠券诚可贵，睡眠价更高；今日宜早睡，忌深夜冲动下单。",
        "冰可乐虽好，贪杯易凉；今日宜温，忌贪。",
        "今日诸事不宜催：催餐、催稿、催人——都不如先坐下。",
        "今日或有小波折，像可乐洒了半杯——可惜，但还能再买；宜冷静，忌迁怒。",
        "迷路不必慌，打开门店列表看看；今日宜问路，忌硬撑。",
    ],
    "彩蛋": [
        "据说看见这张签的人，今年的好运会多到需要朋友帮忙分担巨无霸。"
        "——麦门隐藏彩蛋签，看见即转运。"
    ],
}

_VERSE_WEIGHTS = {"上上": 10, "上": 34, "中": 35, "下": 20, "彩蛋": 1}

# ---------------------------------------------------------------------------
# 宜 / 忌 词池（玄学部分，数据部分在 build 时动态补充）
# ---------------------------------------------------------------------------

YI_POOL = [
    "宜加个蛋",
    "宜把中薯换成大薯（今天配得上）",
    "宜帮同事带一杯咖啡（人缘 +10）",
    "宜到店取餐，顺便散步",
    "宜在 11:11 下单（玄学上快 3 分钟）",
    "宜组团拼单，人多力量大",
    "宜对为你点单的人说声谢谢",
    "宜尝试一个没吃过的新品",
]

JI_POOL = [
    "忌深夜浏览菜单，尤其是炸物分类",
    "忌一个人吃两份，还不承认",
    "忌和番茄酱较劲（12 包是够的）",
    "忌开会前吃太饱，犯困会影响发挥",
    "忌忘记用券——凭实力多花的钱最可惜",
    "忌在下午四点发誓减肥（且看明天）",
    "忌羡慕别人的套餐，自己点的就是最好的",
]

AUSPICIOUS_REASONS = [
    "灵感与胃口同时在线的时段",
    "据说这个点下单，队伍最短",
    "麦门钦定的「此时代谢最佳」窗口",
    "适合突然想吃点好的的一切理由",
]

FISH_BLESSINGS = {
    10: "十全十美，今日加个蛋不过分。",
    36: "薯条加成 +1（心理作用，但有效）。",
    66: "六六大顺，顺手把该领的券领了吧。",
    88: "麦门显灵！据说此刻去给 mcalmanac 点 Star 的人会心想事成。",
    100: "功德圆满——恭喜解锁隐藏成就：麦门有缘人。",
}


# ---------------------------------------------------------------------------
# 主构建函数
# ---------------------------------------------------------------------------

@dataclass
class Almanac:
    day: date
    day_label: str
    ganzhi_day: str
    ganzhi_year: str
    weekday: str
    yi: list[tuple[str, bool]] = field(default_factory=list)   # (文案, 是否来自实时数据)
    ji: list[tuple[str, bool]] = field(default_factory=list)
    wealth_score: int = 60
    savings_estimate: float | None = None
    savings_note: str = ""
    lucky_number: int = 7
    lucky_food: dict | None = None
    lucky_food_kcal: int | None = None
    auspicious_time: str = ""
    auspicious_reason: str = ""
    verse_level: str = "中"
    verse: str = ""
    source: str = "demo"
    live_highlights: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    salt: str = ""

    def to_dict(self) -> dict:
        return {
            "date": self.day.isoformat(),
            "day_label": self.day_label,
            "ganzhi_day": self.ganzhi_day,
            "ganzhi_year": self.ganzhi_year,
            "weekday": self.weekday,
            "yi": [{"text": t, "live": live} for t, live in self.yi],
            "ji": [{"text": t, "live": live} for t, live in self.ji],
            "wealth_score": self.wealth_score,
            "savings_estimate": self.savings_estimate,
            "savings_note": self.savings_note,
            "lucky_number": self.lucky_number,
            "lucky_food": self.lucky_food.get("productName") if self.lucky_food else None,
            "lucky_food_kcal": self.lucky_food_kcal,
            "auspicious_time": self.auspicious_time,
            "auspicious_reason": self.auspicious_reason,
            "verse_level": self.verse_level,
            "verse": self.verse,
            "source": self.source,
            "live_highlights": self.live_highlights,
            "errors": self.errors,
        }


def _seed_for(d: date, live: LiveData, salt: str) -> int:
    """由日期 + 数据摘要 + 盐 生成稳定种子。"""
    digest = hashlib.sha256()
    digest.update(d.isoformat().encode("utf-8"))
    digest.update(salt.encode("utf-8"))
    digest.update(str(live.coupon_total_value).encode("utf-8"))
    digest.update(str((live.points or {}).get("available")).encode("utf-8"))
    digest.update(str(len(live.campaigns)).encode("utf-8"))
    return int.from_bytes(digest.digest()[:8], "big")


def pick_verse(rnd: random.Random) -> tuple[str, str]:
    """按权重抽一支签。"""
    levels = list(_VERSE_WEIGHTS.keys())
    weights = [_VERSE_WEIGHTS[level] for level in levels]
    level = rnd.choices(levels, weights=weights, k=1)[0]
    return level, rnd.choice(VERSES[level])


def build_almanac(
    live: LiveData,
    when: date | None = None,
    salt: str = "",
) -> Almanac:
    """构建一份黄历。确定性：同日期 + 同数据 + 同 salt → 同结果。"""
    d = when or datetime.now().date()
    rnd = random.Random(_seed_for(d, live, salt))

    almanac = Almanac(
        day=d,
        day_label=cn_date(d),
        ganzhi_day=day_ganzhi(d),
        ganzhi_year=year_ganzhi(d.year),
        weekday=_WEEKDAYS[d.weekday()],
        source=live.source,
        errors=dict(live.errors),
        salt=salt,
    )

    # ---- 宜：先放 1-2 条实时数据推导，再补玄学词条 ----
    yi_data: list[str] = []
    if live.coupons_available:
        count = len(live.coupons_available)
        total = live.coupon_total_value
        if total:
            yi_data.append(f"宜薅券：「麦麦省」有 {count} 张券可领，约省 ¥{total:g}")
        else:
            yi_data.append(f"宜薅券：「麦麦省」有 {count} 张券等你领")
    if live.points and live.lottery and live.lottery.get("cost"):
        afford = live.points["available"] // live.lottery["cost"]
        if afford >= 1:
            yi_data.append(f"宜抽奖：积分够抽 {afford} 次，今日手气在线")
    ongoing = live.ongoing_campaigns
    if ongoing:
        yi_data.append(f"宜参加「{rnd.choice(ongoing)['title']}」：正在进行中")

    yi = [(text, True) for text in yi_data[:2]]
    for text in rnd.sample(YI_POOL, k=max(2, 4 - len(yi))):
        if len(yi) >= 4:
            break
        yi.append((text, False))

    # ---- 忌：1 条数据推导（若有）+ 2 条玄学 ----
    ji: list[tuple[str, bool]] = []
    if live.points and live.lottery and live.lottery.get("cost"):
        afford = live.points["available"] // live.lottery["cost"]
        if afford < 1:
            ji.append((f"忌抽奖：积分不够一次（还差 {live.lottery['cost'] - live.points['available']} 分），先领券攒着", True))
    if not ji:
        ji.append((rnd.choice(JI_POOL), False))
    for text in rnd.sample([t for t in JI_POOL if (t, False) not in ji], k=2):
        ji.append((text, False))

    almanac.yi = yi
    almanac.ji = ji[:3]

    # ---- 财运指数 ----
    score = 55
    if live.coupons_available:
        score += min(len(live.coupons_available) * 4, 20)
        almanac.live_highlights.append(f"可领券 {len(live.coupons_available)} 张")
    if live.points:
        score += min(live.points["available"] // 500, 10)
    score += min(len(ongoing) * 3, 9)
    score += rnd.randint(0, 12)
    almanac.wealth_score = max(40, min(99, score))

    if live.coupon_total_value:
        almanac.savings_estimate = live.coupon_total_value
        almanac.savings_note = f"来自 {len(live.coupons_available)} 张可领券"
    elif live.coupons_available:
        almanac.savings_estimate = round(3.5 * len(live.coupons_available), 1)
        almanac.savings_note = f"约按 {len(live.coupons_available)} 张券估算"
    else:
        almanac.savings_note = "今日无新增券可领，宜珍惜已有的"

    # ---- 幸运数字 / 幸运餐品 / 吉时 ----
    almanac.lucky_number = rnd.randint(1, 99)

    if live.nutrition:
        food = rnd.choice(live.nutrition)
        almanac.lucky_food = food
        kcal = food.get("energyKcal")
        almanac.lucky_food_kcal = int(kcal) if isinstance(kcal, (int, float)) else None

    start_hour = rnd.randint(10, 19)
    start_minute = rnd.randint(0, 59)
    duration = rnd.randint(23, 66)
    start_total = start_hour * 60 + start_minute
    end_total = start_total + duration
    fmt = lambda m: f"{m // 60:02d}:{m % 60:02d}"
    almanac.auspicious_time = f"{fmt(start_total)} – {fmt(end_total)}"
    almanac.auspicious_reason = rnd.choice(AUSPICIOUS_REASONS)

    # ---- 签 ----
    level, verse = pick_verse(rnd)
    almanac.verse_level = level
    almanac.verse = verse

    return almanac
