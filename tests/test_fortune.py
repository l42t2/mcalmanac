"""麦门黄历核心逻辑测试（零依赖，python -m unittest discover 即可运行）。

覆盖点：
- 干支历换算（以两个公认可查的日期做校准）
- 中文日期边界（初十 / 二十 / 三十）
- 黄历确定性（同输入 → 同输出）
- 财运指数取值范围
- 营养接口自定义文本格式解析
- 优惠券提取（多种可能的返回结构）
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcalmanac.data import demo_live_data, extract_coupons, parse_nutrition
from mcalmanac.fortune import build_almanac, cn_date, day_ganzhi, year_ganzhi
from mcalmanac.mcp_client import _parse_sse, tool_text


class TestGanzhi(unittest.TestCase):
    def test_day_ganzhi_calibration(self):
        # 1949-10-01 为甲子日（开国大典，公认可查）
        self.assertEqual(day_ganzhi(date(1949, 10, 1)), "甲子")
        # 2000-01-01 为戊午日（J2000 前后常用校点）
        self.assertEqual(day_ganzhi(date(2000, 1, 1)), "戊午")

    def test_year_ganzhi(self):
        self.assertEqual(year_ganzhi(2026), "丙午")
        self.assertEqual(year_ganzhi(2024), "甲辰")

    def test_cn_date_edges(self):
        self.assertEqual(cn_date(date(2026, 10, 15)), "二〇二六年十月十五日")
        self.assertEqual(cn_date(date(2026, 10, 10)), "二〇二六年十月十日")
        self.assertEqual(cn_date(date(2026, 10, 20)), "二〇二六年十月二十日")
        self.assertEqual(cn_date(date(2026, 10, 30)), "二〇二六年十月三十日")
        self.assertEqual(cn_date(date(2026, 2, 3)), "二〇二六年二月三日")


class TestAlmanac(unittest.TestCase):
    def test_deterministic(self):
        live = demo_live_data()
        first = build_almanac(live, when=date(2026, 10, 15))
        second = build_almanac(live, when=date(2026, 10, 15))
        self.assertEqual(first.verse, second.verse)
        self.assertEqual(first.wealth_score, second.wealth_score)
        self.assertEqual(first.lucky_number, second.lucky_number)

    def test_salt_changes_result(self):
        live = demo_live_data()
        base = build_almanac(live, when=date(2026, 10, 15))
        rerolled = build_almanac(live, when=date(2026, 10, 15), salt="reroll-1")
        # 至少有一项发生变化（理论上几乎必然）
        changed = (
            base.verse != rerolled.verse
            or base.wealth_score != rerolled.wealth_score
            or base.lucky_number != rerolled.lucky_number
        )
        self.assertTrue(changed)

    def test_wealth_score_bounds(self):
        live = demo_live_data()
        for day in range(1, 29):
            almanac = build_almanac(live, when=date(2026, 2, day))
            self.assertGreaterEqual(almanac.wealth_score, 40)
            self.assertLessEqual(almanac.wealth_score, 99)

    def test_verse_from_pool(self):
        from mcalmanac.fortune import VERSES
        live = demo_live_data()
        almanac = build_almanac(live, when=date(2026, 10, 15))
        self.assertIn(almanac.verse, VERSES[almanac.verse_level])

    def test_live_yi_items(self):
        live = demo_live_data()
        almanac = build_almanac(live, when=date(2026, 10, 15))
        live_items = [text for text, is_live in almanac.yi if is_live]
        self.assertTrue(any("薅券" in text for text in live_items))


class TestParsing(unittest.TestCase):
    NUTRITION_SAMPLE = (
        '接口说明文本……{"data": "[160]{productName,nutritionDescription,energyKj,energyKcal,'
        'protein,fat,carbohydrate,sodium,calcium}:\\n  猪柳麦满分,null,1288,308,16,16,24,781,213\\n  '
        '中薯条,null,1210,289,4,12,38,165,18\\n  零度可乐,null,5,,0,0,1,10,0\\n"}'
    )

    def test_parse_nutrition(self):
        rows = parse_nutrition(self.NUTRITION_SAMPLE)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["productName"], "猪柳麦满分")
        self.assertEqual(rows[0]["energyKcal"], 308)
        # energyKcal 为空时用 kJ × 0.239 兜底
        self.assertEqual(rows[2]["energyKcal"], round(5 * 0.239))

    def test_parse_nutrition_plain_format(self):
        rows = parse_nutrition(
            "[2]{productName,energyKj,energyKcal}:\n  巨无霸,2160,516\n  中薯条,1210,289"
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["productName"], "中薯条")

    def test_extract_coupons_variants(self):
        payloads = [
            {"data": [{"couponName": "A 券", "discountAmount": 5}, {"couponName": "B 券", "discountAmount": 3}]},
            [{"name": "A 券", "value": "¥5.0"}, {"name": "B 券", "value": 3}],
        ]
        for payload in payloads:
            import json
            result = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
            coupons = extract_coupons(result)
            self.assertGreaterEqual(len(coupons), 2)
            self.assertTrue(all(c["name"] for c in coupons))

    def test_extract_coupons_noise(self):
        # 找不到券时返回空列表而不是抛错
        result = {"content": [{"type": "text", "text": "抱歉，暂无数据"}]}
        self.assertEqual(extract_coupons(result), [])


class TestMCPClientHelpers(unittest.TestCase):
    def test_parse_sse(self):
        body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
        messages = _parse_sse(body)
        self.assertEqual(messages[0]["result"]["ok"], True)

    def test_tool_text(self):
        result = {"content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]}
        self.assertEqual(tool_text(result), "hello\nworld")


if __name__ == "__main__":
    unittest.main(verbosity=2)
