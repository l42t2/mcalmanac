---
name: mcalmanac
description: 麦门黄历（McAlmanac）——把麦当劳 MCP 的真实数据（优惠券/积分/抽奖/活动/营养）变成每日电子黄历与省钱雷达。触发词：麦门黄历、今日运势、摇一卦、麦当劳优惠券、领券、麦麦省、积分抽奖、电子木鱼、今天吃什么（麦当劳）。
---

# 麦门黄历 Skill

一个基于麦当劳 MCP 的「玄学 + 实用」Skill：把真实数据包装成每日黄历，同时顺带完成省钱、领券、积分决策。

## 快速使用

在项目根目录运行（零依赖，Python 3.9+）：

```bash
python -m mcalmanac today          # 今日黄历（自动连接麦当劳 MCP）
python -m mcalmanac today --demo   # 无 Token 时的演示模式
python -m mcalmanac save           # 省钱雷达：可领券 + 一键领取（--yes）
python -m mcalmanac points         # 积分玄学：余额 + 是否宜抽奖
python -m mcalmanac fish           # 电子木鱼（互动彩蛋）
python -m mcalmanac card -o card.html   # 生成分享卡片
python -m mcalmanac doctor         # 连接自检
```

JSON 输出（便于智能体二次加工）：`python -m mcalmanac today --json`

## 智能体使用建议

1. 用户问「今天宜吃什么 / 今日运势 / 帮我看看有什么券」时：
   - 优先运行 `python -m mcalmanac today --json`，读取 `yi` / `ji` / `wealth_score` / `verse` 字段，用自然语言转述，不要输出原始 JSON。
2. 用户想领券时：先运行 `python -m mcalmanac save` 展示可领券清单，确认后再加 `--yes` 真实领取。
3. 用户问积分是否值得抽奖：运行 `python -m mcalmanac points`，按其结论转述（注意：真实抽奖有随机性，本工具只做「建议」）。
4. 用户想分享时：`python -m mcalmanac card` 生成 HTML 卡片，提示其截图分享；或 `today --share` 直接给文字版。
5. 保留玄学语气：黄历文案本身是娱乐性质，转述时保持轻松，但涉及「领券 / 抽奖消耗积分」等真实操作必须如实说明后果。

## 前置条件

- 需要麦当劳 MCP Token（免费）：https://open.mcd.cn/mcp 手机号登录 → 控制台 → 激活
- 配置方式（二选一）：
  - 环境变量：`MCD_MCP_TOKEN=你的Token`
  - WorkBuddy 连接器：配置 `mcd-mcp`（见仓库 mcp-config.example.json）
- 未配置 Token 时自动进入演示模式，功能可完整体验（数据为示例）。

## 工具与边界

- 本 Skill 通过麦当劳 MCP 官方服务读取用户自己的券/积分/活动数据，所有计算在本地完成。
- 不存储、不上传任何 Token 或个人数据；`--yes` 领券会真实操作麦当劳账户，执行前需获得用户确认。
- 项目非麦当劳官方产品，输出仅供娱乐参考；餐品、价格以麦当劳官方渠道为准。
