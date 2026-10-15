"""数据层：MCP 响应的容错解析 + 演示数据。

麦当劳 MCP 各 Tool 的返回结构官方未完全公开，且可能随版本演进。
因此这里的原则是「宽进」：尽最大努力从任意合理结构中提取所需字段，
提取不到就优雅降级，绝不让黄历因为一个字段缺失而崩溃。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from .mcp_client import tool_json, tool_text

# ---------------------------------------------------------------------------
# 容错工具
# ---------------------------------------------------------------------------

_NAME_KEYS = ("couponName", "name", "title", "couponTitle", "productName", "activityName", "goodsName")
_VALUE_KEYS = (
    "discountAmount", "faceValue", "couponValue", "amount", "discount",
    "price", "value", "parValue", "thresholdAmount",
)
_COUNT_KEYS = ("availablePoints", "points", "balance", "available", "pointBalance", "totalPoints")


def _walk(obj):
    """深度遍历任意 JSON 结构。"""
    yield obj
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def _first_key(obj, keys):
    """在 dict（含嵌套 dict）中按优先级寻找第一个存在且非空的键值。"""
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if key in obj and obj[key] not in (None, "", [], {}):
            return obj[key]
    for value in obj.values():
        if isinstance(value, dict):
            found = _first_key(value, keys)
            if found is not None:
                return found
    return None


def _best_dict_list(payload) -> list:
    """找到 payload 里「最像记录列表」的那个 list[dict]。"""
    best: list = []
    best_score = -1
    for node in _walk(payload):
        if not isinstance(node, list) or not node:
            continue
        dicts = [item for item in node if isinstance(item, dict)]
        if not dicts:
            continue
        score = len(dicts)
        for item in dicts[:3]:
            if any(key in item for key in _NAME_KEYS):
                score += 100
        if score > best_score:
            best, best_score = dicts, score
    return best


def _to_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("¥", "").replace("￥", "").replace("元", "")
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(value):
    number = _to_float(value)
    return int(number) if number is not None else None


# ---------------------------------------------------------------------------
# 各 Tool 的提取器
# ---------------------------------------------------------------------------

def extract_coupons(result) -> list[dict]:
    """从 available-coupons / query-my-coupons 的结果中提取券列表。"""
    payload = tool_json(result)
    if payload is None:
        return []
    items = _best_dict_list(payload)
    coupons = []
    for item in items:
        name = _first_key(item, _NAME_KEYS)
        if not isinstance(name, str) or not name.strip():
            continue
        value = _to_float(_first_key(item, _VALUE_KEYS))
        coupons.append({"name": name.strip(), "value": value, "raw": item})
    return coupons


def extract_points(result) -> dict | None:
    """从 query-my-account 的结果中提取积分信息。"""
    payload = tool_json(result)
    if payload is None:
        return None
    available = _to_int(_first_key(payload, _COUNT_KEYS))
    if available is None:
        # 找不到明确键名时，扫描含 "point/积分" 的键
        for node in _walk(payload):
            if isinstance(node, dict):
                for key, value in node.items():
                    if ("point" in key.lower() or "积分" in key) and _to_int(value) is not None:
                        available = _to_int(value)
                        break
            if available is not None:
                break
    if available is None:
        return None
    expire_keys = ("expiringSoon", "expiringPoints", "即将过期积分", "willExpirePoints")
    expiring = _to_int(_first_key(payload, expire_keys))
    return {"available": available, "expiring": expiring}


def extract_lottery(result) -> dict | None:
    """从 query-lottery-info 的结果中提取抽奖信息。"""
    payload = tool_json(result)
    if payload is None:
        return None
    info: dict = {}
    cost = None
    for node in _walk(payload):
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = key.lower()
                if cost is None and ("cost" in lowered or "consume" in lowered or "need" in lowered):
                    cost = _to_int(value)
                if "status" in lowered and isinstance(value, str) and "status" not in info:
                    info["status"] = value
                if ("prize" in lowered or "奖品" in key) and isinstance(value, list) and value:
                    info["prize_count"] = len(value)
    if cost is not None:
        info["cost"] = cost
    info.setdefault("prize_count", None)
    return info or None


def extract_campaigns(result) -> list[dict]:
    """从 campaign-calendar 的结果中提取活动列表。"""
    payload = tool_json(result)
    if payload is None:
        return []
    items = _best_dict_list(payload)
    campaigns = []
    for item in items:
        title = _first_key(item, _NAME_KEYS)
        if not isinstance(title, str) or not title.strip():
            continue
        status = _first_key(item, ("status", "state", "activityStatus", "阶段"))
        campaigns.append(
            {
                "title": title.strip(),
                "status": str(status) if status is not None else "",
                "raw": item,
            }
        )
    return campaigns


def _ongoing(campaign: dict) -> bool:
    status = campaign.get("status", "")
    if not status:
        return True  # 状态未知时保守视为进行中
    lowered = status.lower()
    return any(word in lowered or word in status for word in ("进行", "ongoing", "active", "上线"))


# ---------------------------------------------------------------------------
# 营养数据解析（list-nutrition-foods 的自定义文本格式）
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"\[(\d+)\]\{([^}]*)\}\s*:")
_DATA_FIELD_RE = re.compile(r'"data"\s*:\s*"((?:[^"\\]|\\.)*)"')


def parse_nutrition(text: str) -> list[dict]:
    """解析 list-nutrition-foods 返回的自定义文本格式。

    实际格式（藏在 content[].text 里，外层可能还有说明文字）::

        [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
          猪柳麦满分,null,1288,308,16,16,24,781,213
          中薯条,null,1210,289,4,12,38,165,18

    返回 [{"productName": ..., "energyKcal": int, ...}, ...]。
    """
    if not text:
        return []
    match = _DATA_FIELD_RE.search(text)
    if match:
        text = match.group(1).replace('\\"', '"').replace("\\n", "\n")
    header = _HEADER_RE.search(text)
    if not header:
        return []
    fields = [f.strip() for f in header.group(2).split(",") if f.strip()]
    if "productName" not in fields:
        return []
    rows = []
    for line in text[header.end():].splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("["):
            break  # 进入下一条 header
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < len(fields):
            continue
        item = dict(zip(fields, parts))
        name = item.get("productName", "").strip()
        if not name or name == "null":
            continue
        kcal = _to_float(item.get("energyKcal"))
        if kcal is None:
            kj = _to_float(item.get("energyKj"))
            kcal = round(kj * 0.239) if kj is not None else None
        try:
            item["energyKcal"] = int(kcal) if kcal is not None else None
            item["protein"] = _to_float(item.get("protein"))
            item["fat"] = _to_float(item.get("fat"))
            item["sodium"] = _to_float(item.get("sodium"))
        except (TypeError, ValueError):
            pass
        rows.append(item)
    return rows


# ---------------------------------------------------------------------------
# 汇总数据结构
# ---------------------------------------------------------------------------

@dataclass
class LiveData:
    """一次黄历生成所需的全部数据（真实或演示）。"""

    source: str = "demo"  # "live" | "demo"
    coupons_available: list[dict] = field(default_factory=list)
    coupons_mine: list[dict] = field(default_factory=list)
    points: dict | None = None
    lottery: dict | None = None
    campaigns: list[dict] = field(default_factory=list)
    nutrition: list[dict] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ongoing_campaigns(self) -> list[dict]:
        return [c for c in self.campaigns if _ongoing(c)]

    @property
    def coupon_total_value(self) -> float | None:
        values = [c["value"] for c in self.coupons_available if c.get("value")]
        return round(sum(values), 1) if values else None


# ---------------------------------------------------------------------------
# 演示数据（--demo / 未配置 Token 时的首屏体验）
# ---------------------------------------------------------------------------

_DEMO_NUTRITION_TEXT = (
    '[10]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:\n'
    "  板烧鸡腿堡,null,1650,394,25,14,41,690,110\n"
    "  巨无霸,null,2160,516,26,28,44,960,150\n"
    "  麦辣鸡腿堡,null,2080,497,24,25,47,1040,120\n"
    "  双层吉士汉堡,null,1870,447,25,23,34,1050,230\n"
    "  中薯条,null,1210,289,4,12,38,165,18\n"
    "  麦乐鸡(5块),null,1130,270,15,16,16,540,20\n"
    "  麦辣鸡翅(2块),null,980,234,13,15,13,530,15\n"
    "  香芋派,null,1130,270,3,12,37,180,10\n"
    "  麦旋风,null,1130,270,5,9,43,120,150\n"
    "  中杯可乐,null,560,134,0,0,34,55,5\n"
)


def demo_live_data() -> LiveData:
    """内置演示数据：结构与真实接口一致，用于零配置体验与离线开发。"""
    coupons = [
        {"name": "板烧鸡腿堡 8 元券", "value": 8.0, "raw": {}},
        {"name": "中薯条买一送一券", "value": 5.5, "raw": {}},
        {"name": "麦乐鸡 5 块 4 元券", "value": 4.0, "raw": {}},
        {"name": "麦咖啡中杯 3 元券", "value": 3.0, "raw": {}},
        {"name": "满 30 减 3 元券(麦乐送)", "value": 3.0, "raw": {}},
    ]
    mine = [
        {"name": "麦辣鸡腿堡 5 元券", "value": 5.0, "raw": {}},
        {"name": "甜筒第二支半价券", "value": 2.5, "raw": {}},
    ]
    campaigns = [
        {"title": "麦麦脆脆薯条节", "status": "进行中", "raw": {}},
        {"title": "1024 程序员节 · 麦门福利周", "status": "进行中", "raw": {}},
        {"title": "双十一狂欢预热", "status": "未开始", "raw": {}},
    ]
    return LiveData(
        source="demo",
        coupons_available=coupons,
        coupons_mine=mine,
        points={"available": 3260, "expiring": 120},
        lottery={"cost": 99, "prize_count": 12, "status": "进行中"},
        campaigns=campaigns,
        nutrition=parse_nutrition(_DEMO_NUTRITION_TEXT),
    )
