# game-review-walkthrough · 游戏评测与通关攻略

[![tests](https://github.com/Scarecrowemm/game-review-walkthrough/actions/workflows/ci.yml/badge.svg)](https://github.com/Scarecrowemm/game-review-walkthrough/actions/workflows/ci.yml)

一个 **Agent Skill**（SKILL.md 标准），让 AI 产出的游戏评测与攻略**每一条结论都能追溯、能重算、能被证伪**。

它解决的不是"写得像评测"，而是三个具体的信任问题：

| 问题 | 本 skill 的答案 |
|------|----------------|
| 评分凭什么这么给？ | 固定 rubric + 公开权重 + 每维度必须给一个加分锚点和一个扣分锚点，总分由脚本算，不手算 |
| 攻略凭什么可信？ | 五级证据体系（E1–E5），E4/E5 的内容禁止写成祈使句，只能进「待验证清单」 |
| 没素材的时候会编吗？ | 不会。无证据的关卡布局、敌人数值、隐藏要素坐标一个字都不写，拍不到的图留占位框并写明怎么拍到 |

---

## 交付物

一次调用产出两份独立文档：

- **`<游戏名>-评测.html`** — 七维评分卡 + 雷达图 + 加减分锚点 + 证据基础声明 + 公正性自查 + 来源清单
- **`<游戏名>-攻略.html`** — 四层攻略结构 + 证据徽标 + 配图（实拍或「待补图」占位）+ 待验证清单

两份都是**单文件自包含 HTML**，样式内嵌，可直接发聊天窗口或分享。

---

## 安装

克隆到对应宿主的技能目录即可，目录名保持 `game-review-walkthrough`。

```bash
git clone https://github.com/Scarecrowemm/game-review-walkthrough.git ~/.workbuddy/skills/game-review-walkthrough
```

| 宿主 | 技能目录 |
|------|---------|
| WorkBuddy | `~/.workbuddy/skills/` |
| Codex | `~/.agents/skills/` |
| ZCode | `~/.zcode/skills/` |

> 三个宿主都读同一套 SKILL.md 标准。仓库根目录直接就是技能根目录，克隆下来就能用，不需要再嵌套一层。
>
> 如果已经在 WorkBuddy 装好、只是想同步到其他宿主，不用手动复制，跑 `scripts/export_to_agent.py` 一键迁移（见下）。

Windows 下 `~` 即 `C:\Users\<用户名>`。

---

## 使用

直接对 AI 说话即可，skill 靠语义匹配触发，不需要记命令：

```
评测一下《空洞骑士：丝之歌》，Steam 装在 D:\SteamLibrary\steamapps\common\Hollow Knight Silksong
帮我把《艾尔登法环》的攻略补一下，我卡在女武神，全收集也要
《哈迪斯》值不值得买？我有 78 小时存档
```

skill 会先做 **Step 0 澄清**——目标（游戏/平台/版本/语言）、素材（截图/录像/存档路径）、范围（只评测/只攻略/两者）、进度（未开始/卡关中/已通关）。这四项缺一它会先反问，不堆假设。

### 手动跑脚本

```bash
# 只校验数据不渲染，定稿前快速迭代
python scripts/build_report.py report-data.json --check

# 渲染两份报告
python scripts/build_report.py report-data.json --out ./out

# 从游戏录像抽帧，产出真实画面作为证据图（需要 ffmpeg）
python scripts/extract_frames.py C:/caps/boss2.mp4 --at 02:14,03:40 -o screenshots

# 迁移到其他 agent（先预检再落盘）
python scripts/export_to_agent.py --target all --dry-run
python scripts/export_to_agent.py --target all
```

| `build_report.py` 参数 | 用途 |
|------|------|
| `--check` | 只校验不渲染 |
| `--image-mode copy` | 默认。图片复制到 `out/assets/`，分享时连目录一起打包 |
| `--image-mode inline` | 图片转 base64 内嵌，产出单文件 HTML，适合发聊天窗口 |
| `--image-mode link` | 只引用原路径，不复制文件 |

`export_to_agent.py` 的 `--target` 接受 `codex` / `zcode` / `workbuddy` / `all`，可逗号组合。`--force` 覆盖前会先备份到 `skills.backup/`。

只依赖 **Python 3 标准库**，运行时不需要 pip install。

---

## 测试

```bash
python -m pip install -r requirements-dev.txt   # 只需要 pytest，且仅测试用
python -m pytest -q                             # 120 passed, 1 skipped
python tests/mutation_check.py                  # 确认测试套件真的有牙
```

这个技能的价值全部建立在「不合规的产物根本生不出来」之上，所以测试的重点不是
「正常数据能不能渲染」，而是**故意喂坏数据，确认它真的会拦**。

为了验证这一点，仓库里有一个变异测试脚本：它逐条破坏 `build_report.py` 的关键校验
（拆掉分数封顶、放行非法证据等级、让校验失败后仍返回 0……），每次破坏都**必须**导致
测试失败。如果某个变异后测试仍然全绿，说明那块逻辑没有被真正测到。

它已经抓到过一次真实漏洞：「权重和必须等于 1」那层校验被拆掉后原本的用例依然全绿——
因为那条用例实际测的是「权重与 rubric 定值一致」，和这层是两回事。

CI 在两个系统（Ubuntu / Windows）× 两个 Python 版本（3.10 / 3.13）上跑测试，
并在主分支上额外跑一次变异测试。

---

## 证据分级

每条关键事实挂一个等级，`build_report.py` 会强制校验。

| 等级 | 代号 | 定义 | 允许的表述方式 |
|------|------|------|----------------|
| E1 | `verified_play` | 用户素材或可复现步骤直接支撑，形成完整验证闭环 | 直接陈述 |
| E2 | `user_material` | 出现在用户提供的截图/录像/存档中，未构成完整验证 | 陈述 + 标注素材位置 |
| E3 | `sourced` | 来自公开资料，附可点击来源 | 陈述 + `source_url` + 检索日期 |
| E4 | `inferred` | 由已知机制推导，无直接来源 | 标注「推断」+ 推导链 + `how_to_verify` |
| E5 | `unknown` | 无任何依据 | 只能进「待验证清单」，禁止进入正文 |

`tier == "sourced"` 必须给 `source_url`；`inferred` / `unknown` 必须给 `how_to_verify`。缺了就直接报错退出。

**单一二手来源不足以支撑 E3**——至少两个独立来源，或一个一手来源。

### 证据基础与分数上限

评分总分受「证据基础」封顶，防止素材不足却给出高置信度结论：

| 证据基础 | 含义 | 总分上限 |
|---------|------|---------|
| `cleared_15h_plus` | 通关 + 15 小时以上可核素材 | 10.0 |
| `cleared_under_15h` | 通关 + 素材可核但时长较短 | 8.5 |
| `cleared_time_unverified` | 通关，但时长或素材未获本地核实（如玩家自述时长） | 8.5 |
| `core_loop_entered` | 进入核心循环但未通关 | 7.5 |
| `early_under_3h` | 3 小时以内 | 不给出总分 |
| `research_only` | 仅阅读资料，未实际游玩 | 6.5 |

---

## 目录结构

```
game-review-walkthrough/
├── SKILL.md                        # 技能入口：红线、证据分级、六步工作流、输出契约
├── README.md
├── LICENSE                         # MIT
├── references/
│   ├── review-rubric.md            # 七维评分、权重、锚点标准、公正性自查清单
│   ├── evidence-protocol.md        # 证据等级、JSON schema、来源规范、图片来源协议
│   ├── walkthrough-framework.md    # 四层攻略模板、BOSS 阶段表、配图纪律
│   └── report-spec.md              # HTML 结构与视觉规范、字段映射
├── scripts/
│   ├── build_report.py             # 校验 + 算分 + 渲染（唯一允许生成 HTML 的入口）
│   ├── extract_frames.py           # 从录像抽帧，产出证据图（依赖 ffmpeg）
│   └── export_to_agent.py          # 导出到 Codex / ZCode / WorkBuddy
├── assets/
│   ├── report.css                  # 两份报告共用样式
│   ├── review-template.html        # 评测模板
│   ├── walkthrough-template.html   # 攻略模板
│   └── sample-report-data.json     # 完整 schema 示例（含 images 字段）
├── tests/
│   ├── test_build_report.py        # 121 条用例，重点覆盖失败路径
│   └── mutation_check.py           # 破坏源码，验证测试有效
├── pytest.ini
├── requirements-dev.txt            # 仅 pytest（测试用，运行时零依赖）
└── .github/workflows/ci.yml        # 跨平台 × 跨版本测试 + 变异测试
```

新建 `report-data.json` 时照 `assets/sample-report-data.json` 的结构复制——里面的游戏是虚构作品，数值与链接均为占位内容。

---

## 设计红线

八条不可协商，冲突时以 SKILL.md 为准：

1. 禁止凭空生成关卡布局、敌人配置、数值、隐藏要素坐标
2. 无证据内容不得使用祈使句
3. 禁止无源转述（「据说」「很多玩家反馈」一律删除）
4. 打分前必须声明证据基础，缺项不得隐去
5. 版本必须锁定（补丁号 + 检索日期）
6. 禁止绕过校验——HTML 只能由 `build_report.py` 生成
7. 禁止伪造游戏画面——机制示意走 `kind=diagram` 自动带「示意图 · 非游戏画面」水印；**留空优于造假的图**
8. 不承诺「这游戏会火」——只承诺评分可复现、结论有证据路径、攻略有验证方法

> 注意第 7 条的现实后果：如果一个能力（如逐帧手感分析）没有素材支撑，报告会**明确标注该维度证据薄弱并取保守值**，而不是拿公开资料硬撑成强结论。

---

## 适用与不适用

**适合**：具体某款游戏的评测打分、卡关解法、BOSS 打法、全收集清单、值不值得买。

**不适合**：游戏设计、数值平衡、经济系统建模——那是设计工作，不是评测工作。

---

## License

MIT，见 [LICENSE](LICENSE)。
