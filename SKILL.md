---
name: game-review-walkthrough
description: Produces evidence-graded, bias-checked reviews of a specified video game plus layered walkthroughs covering main-route progression, difficulty spikes, boss fights, and collectibles. This skill should be used when the user asks to review, rate, score, or judge a game (评测/打分/评分/值不值得买/优缺点), asks how to clear a level, beat a boss, or find hidden items (卡关/怎么过/打不过/BOSS 打法/全收集/隐藏要素/通关攻略), or requests a game review report or walkthrough document. Delivers two separate documents — a weighted-rubric review and a layered walkthrough — optionally rendered as a self-contained HTML report. Not intended for game design, balance, or economy work.
agent_created: true
---

# 游戏评测与通关攻略

## Overview

对用户指定的游戏产出两类交付物：一份**可复现的加权评分评测**，一份**分层的通关攻略**。两者共享同一套证据体系，任何一条结论都必须挂得上证据等级；挂不上证据的内容不得写成攻略步骤，只能写成"待验证问题 + 验证方法"。

这个 skill 的核心不是"写得像评测"，而是**让结论能被追溯、被重算、被证伪**。公平来自固定 rubric 与权重公开，准确来自证据分级与拒绝编造。

## 硬性红线

以下八条不可协商，任何一条冲突时以本节为准。

1. **禁止凭空生成关卡布局、敌人配置、数值、隐藏要素坐标。** 没有证据的关卡细节，一个字都不写。
2. **无证据内容不得使用祈使句。** "先往左走，跳上平台"是 E4/E5 等级禁止的写法；正确写法是"从 X 机制推断应向左，建议在第 N 关验证"。
3. **禁止无源转述。** "据说""很多玩家反馈""网上有人提到"一律删除，或替换为可点击来源。
4. **打分前必须声明证据基础。** 逐项声明：实际游玩时长、完成进度、是否看过完整流程录像、是否仅阅读资料。**缺项不得隐去，按 `references/review-rubric.md` 的证据基础上限表封顶**，并在报告首屏写明缺的是哪一项。玩家自述的时长不算可核证据，不得据此走 10.0 那一档。
5. **版本必须锁定。** 评测结论绑定补丁版本号与检索日期。版本不明时标注"版本未确认"，并声明结论可能随版本失效。
6. **禁止绕过校验。** HTML 必须由 `scripts/build_report.py` 生成。校验失败时补齐数据，不得手写 HTML 或修改脚本来放行。
7. **禁止伪造游戏画面。** 不得用 AI 生成图或任何合成图冒充游戏截图、官方素材。机制示意必须走 `kind=diagram`（渲染时自动带「示意图 · 非游戏画面」水印）。拍不到就留「待补图」占位框并写明怎么拍到——**留空优于造假的图**，因为玩家会拿着假图去找一个不存在的东西。
8. **不承诺"这游戏会火"。** 只承诺评分可复现、结论有证据路径、攻略有验证方法。

## 证据分级（Evidence Tier）

每条关键事实必须标注等级。渲染时 E4/E5 会强制带可视徽标。

| 等级 | 代号 | 定义 | 允许的表述方式 |
|------|------|------|----------------|
| E1 | `verified_play` | 用户素材或可复现步骤直接支撑，形成完整验证闭环 | 直接陈述 |
| E2 | `user_material` | 出现在用户提供的截图/录像/存档中，未构成完整验证 | 陈述 + 标注素材位置（文件名/时间戳） |
| E3 | `sourced` | 来自公开资料，附可点击来源 | 陈述 + `source_url` + 检索日期 |
| E4 | `inferred` | 由已知机制推导，无直接来源 | 标注"推断" + 推导链 + `how_to_verify` |
| E5 | `unknown` | 无任何依据 | 只能写入"待验证清单"，禁止进入正文步骤 |

`tier == "sourced"` 必须提供 `source_url`；`tier` 为 `inferred` 或 `unknown` 必须提供 `how_to_verify`。脚本会强制校验。

## 工作流

### Step 0 — 受理与澄清

四项信息缺一即先反问，不在假设上堆假设：

| 项 | 说明 |
|----|------|
| 目标 | 游戏名 + 平台 + 版本/补丁号 + 游玩语言 |
| 素材 | 有无截图/录像/存档/游戏内数据，路径在哪 |
| 范围 | 只评测 / 只攻略 / 两者；攻略要哪几层 |
| 进度 | 未开始 / 进行中卡在某关 / 已通关 |

用户明确表示"信息就这样，先做"时，按最保守假设推进，并在交付物开头标明假设清单与"假设变化时的重做范围"。

### Step 1 — 证据采集

1. 枚举素材目录，建立清单：`文件名 → 内容 → 对应游戏进度`。存档与录像优先于截图，截图优先于转述。
2. 联网检索优先级：官方补丁说明与官方 Wiki → 平台商店页与官方公告 → 高可信攻略站/社区。**单一二手来源不足以支撑 E3**，至少两个独立来源或一个一手来源。
3. 输出 `report-data.json`，每条关键事实挂 `tier` 与 `source`。schema 见 `references/evidence-protocol.md`。
4. 检索不到的部分，不写。直接进"待验证清单"。

### Step 2 — 评测

1. 读 `references/review-rubric.md`，取得维度、权重、锚点定义与定级区间。
2. 逐维度打 1-10 整数分，**每维度至少给出一个加分锚点和一个扣分锚点**。锚点必须是可观察的行为或现象，不是形容词。
3. 跑公正性自查清单（`references/review-rubric.md` 末节），逐条记录风险项与处置。
4. 加权总分由脚本计算，不手算。

### Step 3 — 攻略

1. 读 `references/walkthrough-framework.md`，按四层结构组织：
   - **Layer A 主线路线图** — 章节级目标与推进顺序，不含细节走位
   - **Layer B 卡关点** — 机制拆解、思路、容错窗口、失败信号
   - **Layer C BOSS** — 阶段表、出招读法、安全输出窗口、低容错风险点
   - **Layer D 隐藏要素** — 位置、触发条件、错过后能否补救、证据等级
2. 每层使用统一模板，字段定义见框架文档。
3. 确认不了的段落写入"待验证清单"，格式为「在哪一关 / 试什么 / 看什么现象」，禁止写"自行摸索"。
4. **配图**：每个章节与每个 BOSS 至少一张图。用户有录像就先抽帧（`scripts/extract_frames.py`），抽不到或没有录像就留「待补图」占位框并写明获取方式。章节推进骨架与 BOSS 阶段进度条会从已有数据**自动生成**，不需要额外准备素材；不确定空间位置的东西不要画。

用户明确不需要配图时跳过本步，但要在交付说明里点明"本版无配图"。

### Step 4 — 渲染

```bash
# 抽帧：把录像里的关键瞬间变成证据图（需要 ffmpeg，缺了会给出安装指引）
python scripts/extract_frames.py C:/caps/boss2.mp4 --at 02:14,03:40 -o screenshots

# 渲染两份报告
python scripts/build_report.py report-data.json --out ./out
```

脚本会：校验 schema 与证据字段 → 校验权重和为 1 → 计算加权总分 → 生成两份独立 HTML（评测、攻略）→ 打印未验证条目与待补图清单。校验失败会列出全部错误并非零退出。

| 参数 | 用途 |
|------|------|
| `--check` | 只校验不渲染，用于数据定稿前的快速迭代 |
| `--image-mode copy` | 默认。图片复制到 `out/assets/`，分享时连目录一起打包 |
| `--image-mode inline` | 图片转 base64 内嵌，产出单文件 HTML，适合发到聊天窗口 |
| `--image-mode link` | 只引用原路径，不复制文件 |

### Step 5 — 交付

两份文档分别落盘，命名 `<游戏名>-评测.html` 与 `<游戏名>-攻略.html`。评测含：评分卡 + 雷达图 + 加分/扣分锚点 + 公正性声明 + 来源清单。攻略含：四层结构 + 证据徽标 + 待验证清单。

向用户汇报时，必须点明：证据基础上限、未验证条目数、结论绑定到哪个版本。

### Step 6 — 迁移到其他 agent（按需）

用户要把本技能挪到 Codex / ZCode 之类的其他 agent 时，**不要手抄内容**，跑导出脚本：

```bash
python scripts/export_to_agent.py --target all --dry-run   # 先预检
python scripts/export_to_agent.py --target all             # 再落盘
```

各宿主读技能的目录不同（Codex `~/.agents/skills/`、ZCode `~/.zcode/skills/`），但都吃同一套 SKILL.md 标准。技能本体不依赖任何宿主专有工具，脚本只用 Python 标准库，可直接迁移。

## 输出契约

| 交付物 | 必含 | 禁止出现 |
|--------|------|----------|
| 评测 | 维度分+权重+锚点、加权总分、证据基础声明、公正性自查、来源清单 | 无锚点的形容词、单一来源支撑的强结论、"神作/雷作"式定性 |
| 攻略 | 四层结构、每层证据等级、容错窗口、失败信号、待验证清单、章节与 BOSS 配图（实拍或待补图占位） | 无来源的走位指令、编造的数值、"多试几次就好"、伪造的游戏画面 |

## Resources

### references/
- `review-rubric.md` — 评分维度、权重、锚点锚定标准、定级区间、公正性自查清单。Step 2 前必读。
- `evidence-protocol.md` — 证据等级定义、JSON schema、来源标注规范、检索优先级、**图片来源协议**。Step 1 前必读。
- `walkthrough-framework.md` — 四层攻略模板与字段定义、BOSS 阶段表、待验证清单写法、**配图纪律**。Step 3 前必读。
- `report-spec.md` — HTML 报告结构与视觉规范、数据字段映射。

### scripts/
- `build_report.py` — 校验 + 算分 + 渲染。唯一允许生成 HTML 的入口。
- `extract_frames.py` — 从游戏录像按时间戳抽帧，产出真实画面作为证据图。依赖 ffmpeg，缺失时给出安装指引而非静默失败。
- `export_to_agent.py` — 把本技能导出到 Codex（`~/.agents/skills/`）、ZCode（`~/.zcode/skills/`）等其他 agent 的技能目录，先预检可移植性再复制。改过技能内容后重跑一次即可同步；`--force` 覆盖前会先备份到 `skills.backup/`。

### assets/
- `report.css` — 两份报告共用的样式。
- `review-template.html` / `walkthrough-template.html` — 渲染模板，占位符见 `references/report-spec.md`。
- `sample-report-data.json` — 完整 schema 示例（含 images 字段），新建数据文件时照此结构复制。示例中的游戏为虚构作品，数值与链接均为占位内容。
