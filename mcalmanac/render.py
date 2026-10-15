"""渲染层：终端黄历 + HTML 分享卡 + 分享文案。

- 终端渲染自行计算 CJK 显示宽度（全角算 2 列），保证边框对齐；
- 颜色可一键关闭（--no-color / NO_COLOR / 非交互终端）；
- HTML 卡片零外部依赖，单文件可直接发朋友圈/截图。
"""

from __future__ import annotations

import html
import os
import re
import unicodedata

from . import REPO_URL, __version__
from .fortune import Almanac

WIDTH = 64  # 盒内宽度（显示列）

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def display_width(text: str) -> int:
    """按终端显示列宽计算字符串宽度（全角/宽字符 = 2，自动忽略 ANSI 颜色码）。"""
    width = 0
    for ch in _ANSI_RE.sub("", text):
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
    return width


def _pad(text: str, width: int, align: str = "left") -> str:
    space = max(0, width - display_width(text))
    if align == "center":
        left = space // 2
        return " " * left + text + " " * (space - left)
    if align == "right":
        return " " * space + text
    return text + " " * space


def wrap_display(text: str, width: int) -> list[str]:
    """按显示宽度折行（CJK 断行不讲究词边界，够用）。"""
    lines, current, current_w = [], "", 0
    for ch in text:
        ch_w = display_width(ch)
        if current_w + ch_w > width and current:
            lines.append(current)
            current, current_w = "", 0
        current += ch
        current_w += ch_w
    if current:
        lines.append(current)
    return lines or [""]


# ---------------------------------------------------------------------------
# 调色板
# ---------------------------------------------------------------------------

class Palette:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _c(self, text: str, code: str) -> str:
        if not self.enabled or not text:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"

    def red(self, t):     return self._c(t, "1;31")
    def green(self, t):   return self._c(t, "1;32")
    def yellow(self, t):  return self._c(t, "1;33")
    def blue(self, t):    return self._c(t, "1;34")
    def cyan(self, t):    return self._c(t, "36")
    def dim(self, t):     return self._c(t, "2")
    def bold(self, t):    return self._c(t, "1")
    def border(self, t):  return self._c(t, "31")
    def subtitle(self, t): return self._c(t, "2;33")


def auto_palette(force_off: bool = False) -> Palette:
    if force_off or os.environ.get("NO_COLOR"):
        return Palette(False)
    if os.environ.get("FORCE_COLOR"):
        return Palette(True)
    if not getattr(__import__("sys").stdout, "isatty", lambda: False)():
        return Palette(False)
    # Windows 控制台开启 ANSI
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            return Palette(False)
    return Palette(True)


# ---------------------------------------------------------------------------
# 终端黄历
# ---------------------------------------------------------------------------

def _box_line(content: str, palette: Palette, inner: int = WIDTH) -> str:
    return palette.border("║ ") + _pad(content, inner - 2) + palette.border(" ║")


def render_terminal(a: Almanac, p: Palette) -> str:
    inner = WIDTH
    top = p.border("╔" + "═" * inner + "╗")
    divider = p.border("╠" + "═" * inner + "╣")
    bottom = p.border("╚" + "═" * inner + "╝")
    lines: list[str] = [top]

    # 标题区
    title = p.red(p.bold("麦 门 黄 历")) + p.dim("  ·  McAlmanac")
    lines.append(_box_line(_pad(title, inner - 2, "center"), p))
    sub = f"{a.day_label} · {a.ganzhi_year}年 · {a.ganzhi_day}日 · {a.weekday}"
    lines.append(_box_line(p.subtitle(_pad(sub, inner - 2, "center")), p))
    tag = "实时数据" if a.source == "live" else "演示数据（mcalmanac --demo）"
    lines.append(_box_line(p.dim(_pad(f"[ {tag} ]", inner - 2, "center")), p))
    lines.append(divider)

    # 宜
    lines.append(_box_line(p.green(p.bold("宜")), p))
    for text, live in a.yi:
        marker = p.cyan("* ") if live else "· "
        body = f"  {marker}{text}"
        for chunk in wrap_display(body, inner - 2):
            lines.append(_box_line(chunk, p))

    # 忌
    lines.append(_box_line(p.red(p.bold("忌")), p))
    for text, live in a.ji:
        marker = p.red("! ") if live else "· "
        body = f"  {marker}{text}"
        for chunk in wrap_display(body, inner - 2):
            lines.append(_box_line(chunk, p))

    lines.append(divider)

    # 财 / 运 区
    bar_len = 20
    filled = round(a.wealth_score / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)
    bar_text = p.yellow(bar) if p.enabled else bar
    wealth_line = f"财运指数  {bar_text}  {p.yellow(str(a.wealth_score))}/100"
    if a.savings_estimate is not None:
        wealth_line += f"   ·  可省约 ¥{a.savings_estimate:g}"
    lines.append(_box_line(f"  {wealth_line}", p))
    if a.savings_note:
        lines.append(_box_line(p.dim(f"  {a.savings_note}"), p))

    food = a.lucky_food.get("productName") if a.lucky_food else None
    if food:
        kcal = f" ({a.lucky_food_kcal} kcal)" if a.lucky_food_kcal else ""
        lines.append(_box_line(f"  幸运数字 {p.yellow(str(a.lucky_number))}  ·  幸运餐品 {p.yellow(food)}{p.dim(kcal)}", p))
    else:
        lines.append(_box_line(f"  幸运数字 {p.yellow(str(a.lucky_number))}", p))

    lines.append(_box_line(f"  麦门吉时 {p.yellow(a.auspicious_time)}  ·  {p.dim(a.auspicious_reason)}", p))
    lines.append(divider)

    # 签
    lines.append(_box_line(p.bold("今日一签") + "   " + p.red(f"「{a.verse_level}签」"), p))
    for chunk in wrap_display(f"「{a.verse}」", inner - 6):
        lines.append(_box_line(f"  {p.cyan(chunk)}", p))
    lines.append(_box_line(p.dim(_pad(f"—— 麦门 · {a.verse_level}签", inner - 4, "right")), p))
    lines.append(divider)

    # 底栏
    if a.errors:
        lines.append(_box_line(p.dim(f"  {len(a.errors)} 项实时数据获取失败，可用 mcalmanac doctor 排查"), p))
    lines.append(_box_line(p.dim(f"  求星还愿 github.com/{REPO_URL.split('github.com/')[-1]}"), p))
    lines.append(_box_line(p.dim("  电子木鱼 mcalmanac fish  ·  隐藏彩蛋 mcalmanac 麦门"), p))
    lines.append(bottom)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 其他终端画面
# ---------------------------------------------------------------------------

FISH_ART = [
    "       _____________",
    "      /            \\",
    "     |   .-\"\"-.    |",
    "     | (  麦门  )  |",
    "     |   `-..-`    |",
    "       \\___________/",
    "        ~~~~~~~~~",
]


def render_fish(knocks: int, p: Palette) -> str:
    art = "\n".join(p.yellow(line) for line in FISH_ART)
    merit = p.green(f"功德 +{knocks}")
    return f"{art}\n        {merit}"


def render_star_egg(p: Palette) -> str:
    star = r"""
        *
       / \
      /___\
     /\   /\
    /__\ /__\
      \ * * /
       \___/
"""
    return (
        p.yellow(star)
        + "\n"
        + p.bold("  检测到一颗赤诚的麦门之心。\n")
        + "  玄学建议：给本项目点一个 Star，\n"
        + p.dim("  明日黄历的运气值加成 +1（麦门说的，科学暂时无法解释）。\n\n")
        + f"  {p.cyan(REPO_URL)}\n"
    )


def render_men_egg(p: Palette) -> str:
    says = [
        "麦门在上，薯条保佑",
        "今日份的快乐摄入，已自动计入功德簿",
        "愿你的套餐永远有餐，愿你的券永远不过期",
    ]
    rail = "─" * 26
    lines = [p.red("   ┌" + rail)]
    for index, text in enumerate(says):
        color = p.red if index == 0 else p.dim
        lines.append(color("   │   " + text))
    lines.append(p.red("   └" + rail))
    return "\n".join(lines) + "\n\n  " + p.dim("（隐藏彩蛋：输入 mcalmanac fish 可敲电子木鱼）") + "\n"


# ---------------------------------------------------------------------------
# HTML 分享卡
# ---------------------------------------------------------------------------

_CARD_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: radial-gradient(circle at 50% 0%, #f6e7c8 0%, #efdcb4 40%, #e8d2a4 100%);
  font-family: "Noto Serif SC", "Songti SC", "STSong", "SimSun", serif;
  display: flex; justify-content: center; padding: 28px 16px;
  min-height: 100vh; color: #4a2c1a;
}
.card {
  width: 428px; background:
    repeating-linear-gradient(0deg, rgba(160,110,50,.035) 0 1px, transparent 1px 28px),
    linear-gradient(180deg, #fdf8ec 0%, #faf1dc 100%);
  border: 1px solid #b98d54; outline: 3px double #8c1f1b; outline-offset: -9px;
  padding: 30px 26px 22px; position: relative;
  box-shadow: 0 18px 44px rgba(90, 50, 10, .28);
}
.card::after {
  content: ""; position: absolute; inset: 5px; pointer-events: none;
  border: 1px solid rgba(140, 31, 27, .18);
}
.brand {
  text-align: center; font-size: 13px; letter-spacing: .5em;
  color: #8c1f1b; margin-bottom: 6px; text-indent: .5em;
}
h1 { text-align: center; font-size: 40px; letter-spacing: .18em; color: #8c1f1b;
     font-weight: 900; text-indent: .18em; }
.date-line { text-align: center; margin-top: 8px; font-size: 13px; color: #6b4a2c; }
.ganzhi { text-align: center; font-size: 12px; color: #9a7448; margin-top: 3px; letter-spacing: .1em; }
.source-tag { display: inline-block; font-size: 11px; padding: 1px 8px; border-radius: 99px;
  border: 1px solid #c9a26c; color: #8a6a3c; margin-top: 8px; }
.source-wrap { text-align: center; }
.hr { border: none; border-top: 1px dashed #c9a26c; margin: 16px 0; }
.section-title { display: flex; align-items: center; gap: 8px; font-size: 17px; font-weight: 700; margin-bottom: 8px; }
.seal { width: 22px; height: 22px; border-radius: 5px; color: #fff; display: inline-flex;
  align-items: center; justify-content: center; font-size: 14px; }
.seal-yi { background: #1d7a44; }
.seal-ji { background: #b0322b; }
ul { list-style: none; }
li { font-size: 14.5px; line-height: 1.75; padding-left: 2px; }
li.live { color: #0f6b39; font-weight: 600; }
li .dot { color: #b98d54; margin-right: 6px; }
.wealth { margin-top: 14px; }
.bar { height: 14px; border: 1px solid #b98d54; background: #f3e5c8; border-radius: 8px; overflow: hidden; }
.bar > i { display: block; height: 100%; background: linear-gradient(90deg, #f0b400, #e2762d); }
.wealth-line { display: flex; justify-content: space-between; font-size: 13px; margin-top: 6px; color: #6b4a2c; }
.wealth-line b { color: #8c1f1b; font-size: 15px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 12px; font-size: 13px; color: #6b4a2c; }
.grid2 div { background: rgba(185, 141, 84, .1); border: 1px solid rgba(185, 141, 84, .35); border-radius: 8px; padding: 8px 10px; }
.grid2 b { color: #8c1f1b; }
.verse { margin-top: 14px; border: 1.5px dashed #b98d54; border-radius: 10px; padding: 16px 16px 12px;
  position: relative; background: rgba(255, 252, 242, .7); }
.verse .lvl { position: absolute; top: -12px; right: 14px; background: #8c1f1b; color: #fdf8ec;
  font-size: 12px; padding: 2px 10px; border-radius: 99px; letter-spacing: .1em; }
.verse p { font-size: 15.5px; line-height: 1.9; color: #4a2c1a; }
.verse .from { text-align: right; margin-top: 8px; color: #9a7448; font-size: 12px; }
.foot { margin-top: 18px; text-align: center; font-size: 11px; color: #a8845a; line-height: 1.9; }
.foot a { color: #8c1f1b; text-decoration: none; }
.bless { text-align: center; font-size: 12px; color: #8c1f1b; margin-top: 12px; letter-spacing: .06em; }
"""


def render_html_card(a: Almanac, repo_url: str = REPO_URL) -> str:
    """生成单文件 HTML 分享卡。"""
    esc = html.escape

    def yi_items() -> str:
        rows = []
        for text, live in a.yi:
            cls = ' class="live"' if live else ""
            mark = "★" if live else "·"
            clean = text[1:] if text.startswith("宜") else text
            rows.append(f'<li{cls}><span class="dot">{mark}</span>{esc(clean)}</li>')
        return "".join(rows)

    def ji_items() -> str:
        rows = []
        for text, live in a.ji:
            cls = ' class="live"' if live else ""
            mark = "!" if live else "·"
            clean = text[1:] if text.startswith("忌") else text
            rows.append(f'<li{cls}><span class="dot">{mark}</span>{esc(clean)}</li>')
        return "".join(rows)

    food = a.lucky_food.get("productName") if a.lucky_food else None
    food_cell = (
        f'<div style="grid-column: span 2">幸运餐品<br><b>{esc(food or "—")}</b>'
        + (f' <span style="font-size:12px">({a.lucky_food_kcal} kcal)</span>' if food and a.lucky_food_kcal else "")
        + "</div>"
        if food
        else ""
    )
    savings = f"约省 ¥{a.savings_estimate:g}" if a.savings_estimate is not None else "券面收益"
    source_label = "实时数据 · 麦当劳 MCP" if a.source == "live" else "演示数据 · 未连接 MCP"
    bar_pct = max(4, min(100, a.wealth_score))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="mcalmanac {__version__}">
<title>麦门黄历 · {a.day.isoformat()}</title>
<style>{_CARD_CSS}</style>
</head>
<body>
<div class="card">
  <div class="brand">1024 程序员节 · 每日玄学限定</div>
  <h1>麦门黄历</h1>
  <div class="date-line">{esc(a.day_label)} · {esc(a.weekday)}</div>
  <div class="ganzhi">{esc(a.ganzhi_year)}年 · {esc(a.ganzhi_day)}日</div>
  <div class="source-wrap"><span class="source-tag">{esc(source_label)}</span></div>
  <hr class="hr">
  <div class="section-title"><span class="seal seal-yi">宜</span>今日宜</div>
  <ul>{yi_items()}</ul>
  <div class="section-title" style="margin-top:14px"><span class="seal seal-ji">忌</span>今日忌</div>
  <ul>{ji_items()}</ul>
  <hr class="hr">
  <div class="wealth">
    <div class="bar"><i style="width:{bar_pct}%"></i></div>
    <div class="wealth-line"><span>财运指数 <b>{a.wealth_score}</b> / 100</span><span>{esc(savings)}<span style="font-size:11px"> · {esc(a.savings_note)}</span></span></div>
  </div>
  <div class="grid2">
    <div>幸运数字<br><b>{a.lucky_number}</b></div>
    <div>麦门吉时<br><b>{esc(a.auspicious_time)}</b></div>
    {food_cell}
  </div>
  <div class="verse">
    <span class="lvl">{esc(a.verse_level)}签</span>
    <p>「{esc(a.verse)}」</p>
    <div class="from">—— 麦门 · {esc(a.verse_level)}签</div>
  </div>
  <div class="bless">薯条在手，天下我有 · 今日也请好好吃饭</div>
  <div class="foot">
    <a href="{esc(repo_url)}">mcalmanac</a> · 基于麦当劳 MCP 的每日黄历<br>
    1024 程序员节麦当劳程序员创意开发大赛参赛作品 · 非麦当劳官方产品<br>
    餐品/价格/活动信息以麦当劳官方渠道实时结果为准
  </div>
</div>
</body>
</html>
"""


def share_text(a: Almanac, repo_url: str = REPO_URL) -> str:
    """可复制的分享文案。"""
    yi = "、".join(t.split("：")[0].replace("宜", "") for t, _ in a.yi[:3])
    ji = "、".join(t.replace("忌", "").split("：")[0] for t, _ in a.ji[:2])
    return (
        f"🥠 麦门黄历 · {a.day.month}月{a.day.day}日 · {a.ganzhi_day}日\n"
        f"宜：{yi}\n"
        f"忌：{ji}\n"
        f"财运 {a.wealth_score}/100"
        + (f"，可省约 ¥{a.savings_estimate:g}" if a.savings_estimate is not None else "")
        + f"\n签：{a.verse}\n"
        f"—— 麦门 · {a.verse_level}签\n"
        f"来算算你的 👉 {repo_url}"
    )
