"""麦门黄历 CLI。

命令：
    mcalmanac              摇一卦今日黄历（默认）
    mcalmanac save         省钱雷达 / 一键领券
    mcalmanac points       积分玄学（点券余额 + 抽奖吉凶）
    mcalmanac fish         电子木鱼
    mcalmanac card         生成 HTML 分享卡
    mcalmanac doctor       连接自检（Token / 工具列表）
    mcalmanac star         隐藏彩蛋
    mcalmanac 麦门          隐藏彩蛋
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from . import REPO_URL, __version__
from .data import (
    LiveData,
    demo_live_data,
    extract_campaigns,
    extract_coupons,
    extract_lottery,
    extract_points,
    parse_nutrition,
)
from .fortune import build_almanac
from .mcp_client import MCDClient, MCPAuthError, MCPError, MCPNetworkError, tool_text
from .render import (
    Palette,
    auto_palette,
    render_fish,
    render_html_card,
    render_men_egg,
    render_star_egg,
    render_terminal,
    share_text,
)

DEMO_HINT = "未检测到 MCP Token，本次使用演示数据。申请 Token：https://open.mcd.cn/mcp"


# ---------------------------------------------------------------------------
# 数据获取
# ---------------------------------------------------------------------------

LIVE_CALLS = [
    ("available-coupons", {}),
    ("query-my-coupons", {}),
    ("query-my-account", {}),
    ("query-lottery-info", {}),
    ("campaign-calendar", {}),
    ("list-nutrition-foods", {}),
]


def gather_live_data(force_demo: bool = False, quiet: bool = False) -> LiveData:
    """获取黄历所需的全部数据。失败降级策略：
    - --demo 或未配置 Token → 演示数据
    - 已配置 Token → 并行调用 6 个 Tool，逐项容错
    - 全部失败且属于鉴权/网络问题 → 抛出，由上层给出排查提示
    """
    if force_demo:
        return demo_live_data()

    client = MCDClient()
    if not client.has_token():
        if not quiet:
            print(f"[提示] {DEMO_HINT}", file=sys.stderr)
        return demo_live_data()

    results = client.call_many(LIVE_CALLS)

    errors: dict[str, str] = {}
    for name, outcome in results.items():
        if isinstance(outcome, Exception):
            errors[name] = str(outcome)[:160]

    # 全部失败：判断根因，抛给上层统一提示
    if len(errors) == len(LIVE_CALLS):
        first = next(iter(results.values()))
        if isinstance(first, MCPAuthError):
            raise first
        if isinstance(first, MCPNetworkError):
            raise first
        raise MCPError("所有 MCP 工具调用均失败：" + next(iter(errors.values())))

    def ok(name: str):
        outcome = results.get(name)
        return None if isinstance(outcome, Exception) else outcome

    nutrition_text = tool_text(ok("list-nutrition-foods")) if ok("list-nutrition-foods") else ""

    live = LiveData(
        source="live",
        coupons_available=extract_coupons(ok("available-coupons")) if ok("available-coupons") else [],
        coupons_mine=extract_coupons(ok("query-my-coupons")) if ok("query-my-coupons") else [],
        points=extract_points(ok("query-my-account")) if ok("query-my-account") else None,
        lottery=extract_lottery(ok("query-lottery-info")) if ok("query-lottery-info") else None,
        campaigns=extract_campaigns(ok("campaign-calendar")) if ok("campaign-calendar") else [],
        nutrition=parse_nutrition(nutrition_text),
        errors=errors,
    )
    return live


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------

def cmd_today(args, palette: Palette) -> int:
    live = gather_live_data(force_demo=args.demo)
    almanac = build_almanac(live, salt=("reroll-" + str(args.reroll)) if args.reroll else "")

    if args.json:
        payload = almanac.to_dict()
        payload["share_text"] = share_text(almanac)
        payload["version"] = __version__
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_terminal(almanac, palette))
        if args.share:
            print()
            print(share_text(almanac))

    if args.card:
        path = args.card if isinstance(args.card, str) else "mcalmanac-card.html"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(render_html_card(almanac))
        print(f"\n[分享卡已生成] {path} —— 用浏览器打开，截图发群，玄学传播", file=sys.stderr)
    return 0


def cmd_save(args, palette: Palette) -> int:
    live = gather_live_data(force_demo=args.demo)
    p = palette

    available = live.coupons_available
    mine = live.coupons_mine
    total = live.coupon_total_value

    print(p.red(p.bold("🍟 省钱雷达")) + p.dim(f"  （{live.source == 'live' and '实时数据' or '演示数据'}）"))
    print()
    if available:
        print(p.green(f"可领 {len(available)} 张："))
        for coupon in available:
            value = f"  ￥{coupon['value']:g}" if coupon.get("value") else ""
            print(f"  · {coupon['name']}{p.yellow(value)}")
        if total:
            print(f"\n  合计可省约 {p.yellow('¥' + format(total, 'g'))}  （按券面价值估算）")
    else:
        print("  暂无可领的券——去「麦麦省」看看，或者明天再来。")

    print()
    print(p.dim(f"我的卡包：{len(mine)} 张可用券"))
    for coupon in mine[:8]:
        print(f"  · {coupon['name']}")

    if args.yes and available:
        if live.source == "demo":
            print(p.yellow("\n[演示模式] 已模拟一键领取全部优惠券（真实模式请配置 Token 后运行）"))
            return 0
        client = MCDClient()
        try:
            client.call_tool("auto-bind-coupons")
            print(p.green(f"\n✅ 已自动领取全部 {len(available)} 张券，去麦当劳 App「我的优惠券」核对吧。"))
        except MCPError as exc:
            print(p.red(f"\n领取失败：{exc}"))
            print(p.dim("  可稍后重试，或用 mcalmanac doctor 检查连接。"))
            return 1
    elif available:
        print(p.dim("\n加 --yes 参数可一键领取全部优惠券（会真实操作你的麦当劳账户）"))

    print(p.dim(f"\n玄学提示：领券不积极，思想有问题。"))
    return 0


def cmd_points(args, palette: Palette) -> int:
    live = gather_live_data(force_demo=args.demo)
    p = palette

    print(p.red(p.bold("✦ 积分玄学")))
    print()
    points = live.points
    lottery = live.lottery

    if points:
        print(f"  可用积分：{p.yellow(str(points['available']))}")
        if points.get("expiring"):
            print("  " + p.red(f"即将过期：{points['expiring']} 分——记得花！"))
    else:
        print(p.dim("  未获取到积分信息（可用 mcalmanac doctor 排查）"))

    if lottery:
        cost = lottery.get("cost")
        print(f"  抽奖单次消耗：{p.yellow(str(cost or '未知'))} 分"
              + (f" · 奖池 {lottery['prize_count']} 项" if lottery.get("prize_count") else ""))
        if points and cost:
            afford = points["available"] // cost
            if afford >= 1:
                print(p.green(f"\n  今日宜抽奖：够抽 {afford} 次，手气这种事，试试才知道。"))
            else:
                need = cost - points["available"]
                print(p.red(f"\n  今日忌抽奖：还差 {need} 分，先领券攒着。"))
    else:
        print(p.dim("  未获取到抽奖活动信息"))

    print(p.dim(f"\n  （积分不能换钱，但能换快乐——比例大约 1:1）"))
    return 0


def cmd_fish(args, palette: Palette) -> int:
    p = palette
    from .fortune import FISH_BLESSINGS

    count = args.count if args.count is not None else None
    interactive = count is None and sys.stdin.isatty() and sys.stdout.isatty()

    if count is None and not interactive:
        count = 3
        print(p.dim("[非交互终端] 自动敲 3 下以示诚意，可用 --count N 指定次数\n"))

    print(render_fish(0, p))
    print(p.dim("  [空格] 敲一下  [q] 退出\n"))

    knocks = 0

    def show_knock() -> None:
        print(f"\r  {p.green('功德 +' + str(knocks))}      ", end="", flush=True)

    if interactive:
        try:
            if sys.platform == "win32":
                import msvcrt
                while True:
                    ch = msvcrt.getwch()
                    if ch in ("q", "Q", "\x03"):
                        break
                    knocks += 1
                    show_knock()
                    if knocks in FISH_BLESSINGS:
                        print(f"\n  ✨ {p.yellow(FISH_BLESSINGS[knocks])}\n")
            else:
                import termios
                import tty
                fd = sys.stdin.fileno()
                old = termios.tcgetattr(fd)
                try:
                    tty.setraw(fd)
                    while True:
                        ch = sys.stdin.read(1)
                        if ch in ("q", "Q", "\x03"):
                            break
                        knocks += 1
                        show_knock()
                        if knocks in FISH_BLESSINGS:
                            print(f"\n  ✨ {p.yellow(FISH_BLESSINGS[knocks])}\n")
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except KeyboardInterrupt:
            pass
    else:
        for index in range(1, (count or 3) + 1):
            knocks = index
            show_knock()
            if knocks in FISH_BLESSINGS:
                print(f"\n  ✨ {p.yellow(FISH_BLESSINGS[knocks])}\n")
        print()

    print(f"\n  今日共敲 {p.yellow(str(knocks))} 下，功德圆满。")
    if knocks >= 100:
        print(p.yellow("  （成就已解锁：麦门有缘人）"))
    print(p.dim(f"  据说功德满 88 的时候，会出现神秘提示——没敲到的，{REPO_URL} 见。"))
    return 0


def cmd_card(args, palette: Palette) -> int:
    live = gather_live_data(force_demo=args.demo)
    almanac = build_almanac(live)
    path = args.output or "mcalmanac-card.html"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render_html_card(almanac))
    print(f"✅ 分享卡已生成：{path}")
    print("   用浏览器打开 → 截图 → 发群，今日份的玄学由你主持。")
    if args.share:
        print()
        print(share_text(almanac))
    return 0


def cmd_doctor(args, palette: Palette) -> int:
    p = palette
    client = MCDClient()
    print(p.red(p.bold("🩺 麦门连接自检")))
    print()
    if client.has_token():
        token = client.token
        masked = token[:4] + "…" + token[-4:] if len(token) > 10 else "…"
        print(f"  Token：{p.green('已配置')}（{masked}）")
    else:
        print(f"  Token：{p.red('未配置')} —— 到 https://open.mcd.cn/mcp 手机号登录 → 控制台 → 激活并复制")
        print(p.dim("  配置方式：环境变量 MCD_MCP_TOKEN=你的Token"))
    print(f"  端点：{client.url}")
    print()

    try:
        tools = client.list_tools()
    except MCPAuthError as exc:
        print(p.red(f"  ✗ 鉴权失败：{exc}"))
        return 1
    except MCPNetworkError as exc:
        print(p.red(f"  ✗ 网络失败：{exc}"))
        return 1
    except MCPError as exc:
        print(p.red(f"  ✗ 调用失败：{exc}"))
        return 1

    names = [t.get("name", "") for t in tools]
    print(p.green(f"  ✓ 连接成功：服务端共 {len(names)} 个工具"))
    key_tools = [c[0] for c in LIVE_CALLS]
    missing = [name for name in key_tools if name not in names]
    if missing:
        print(p.yellow(f"  ! 黄历所用的工具中暂缺：{', '.join(missing)}"))
    else:
        print(f"  ✓ 黄历所需 {len(key_tools)} 个工具全部可用")
    if args.raw:
        print()
        print(json.dumps(names, ensure_ascii=False, indent=2))
    return 0


def cmd_star(args, palette: Palette) -> int:
    print(render_star_egg(palette))
    return 0


def cmd_men(args, palette: Palette) -> int:
    print(render_men_egg(palette))
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcalmanac",
        description="🥠 麦门黄历：把麦当劳 MCP 的真实数据变成每天能摇的电子黄历。",
        epilog=f"GitHub: {REPO_URL}",
    )
    parser.add_argument("command", nargs="?", default="today",
                        choices=["today", "save", "points", "fish", "card", "doctor", "star", "麦门"])
    parser.add_argument("--demo", action="store_true", help="使用内置演示数据（无需 Token）")
    parser.add_argument("--yes", action="store_true", help="save 命令：确认一键领取全部优惠券（真实操作账户）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（today 命令）")
    parser.add_argument("--share", action="store_true", help="附上可复制的分享文案")
    parser.add_argument("--no-color", action="store_true", help="关闭颜色")
    parser.add_argument("--reroll", type=int, default=0, metavar="N", help="再摇一卦（娱乐模式，N 为任意整数）")
    parser.add_argument("--card", nargs="?", const=True, default=None, metavar="FILE",
                        help="today 命令顺带生成分享卡")
    parser.add_argument("--output", "-o", default=None, metavar="FILE", help="card 命令的输出文件名")
    parser.add_argument("--count", type=int, default=None, metavar="N", help="fish 命令：敲 N 下（非交互）")
    parser.add_argument("--raw", action="store_true", help="doctor 命令：输出完整工具列表")
    parser.add_argument("--version", action="version", version=f"mcalmanac {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = build_parser()
    args = parser.parse_args(argv)
    palette = auto_palette(force_off=args.no_color)

    handlers = {
        "today": cmd_today,
        "save": cmd_save,
        "points": cmd_points,
        "fish": cmd_fish,
        "card": cmd_card,
        "doctor": cmd_doctor,
        "star": cmd_star,
        "麦门": cmd_men,
    }
    handler = handlers[args.command]

    try:
        return handler(args, palette)
    except MCPAuthError as exc:
        print(f"\n[!] {exc}\n", file=sys.stderr)
        print("    想先看效果：运行  mcalmanac --demo", file=sys.stderr)
        return 1
    except MCPNetworkError as exc:
        print(f"\n[!] {exc}\n", file=sys.stderr)
        print("    检查网络后重试，或先运行  mcalmanac --demo  体验演示数据", file=sys.stderr)
        return 1
    except MCPError as exc:
        print(f"\n[!] {exc}\n", file=sys.stderr)
        print("    先运行  mcalmanac doctor  排查连接", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n  下次再来摇卦。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
