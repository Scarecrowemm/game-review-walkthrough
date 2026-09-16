# 证据协议与数据契约

这份文档定义两件事：**什么样的结论算"有证据"**，以及 **report-data.json 长什么样**。脚本按此校验，不合规的数据无法渲染。

## 一、证据等级

| 等级 | tier 值 | 触发条件 | 强制字段 | 渲染效果 |
|------|---------|----------|----------|----------|
| E1 | `verified_play` | 素材或可复现步骤构成完整验证闭环 | `note`（步骤或素材定位） | 无徽标 |
| E2 | `user_material` | 用户素材中出现，未构成闭环 | `material_ref` | 灰色徽标「素材」 |
| E3 | `sourced` | 公开资料，一手来源或两个独立二手来源 | `source_url` 或 `source[2]` | 无徽标 |
| E4 | `inferred` | 由机制推导，无直接来源 | `basis`（推导链）+ `how_to_verify` | 橙色徽标「推断」 |
| E5 | `unknown` | 无依据 | `how_to_verify` | 红色徽标「未验证」 |

### 升级与降级规则

- **升到 E1** 需要闭环：素材/录像能定位到具体时刻，或步骤可被第三方按描述复现。
- **E3 降 E4** 的触发条件：仅单一二手来源；两个来源在关键事实上互相矛盾；来源本身无日期或已过期（超过一个大版本）。
- **任何等级降 E5** 的触发条件：来源链接失效且无存档。失效链接不得留在数据里。

### 绝对禁止

1. 不得把 E4/E5 的证据字段填成 E1/E3 的样子来绕过校验（如把推断内容挂一个泛泛的首页链接充数）。**来源必须能直接读到该事实，链接到首页不算 E3。**
2. 不得为凑齐证据数量，把同一来源拆成多条。
3. 不得在 `note` 里写"综合多方资料"这类不可核验的表述。

## 二、来源优先级（检索顺序）

1. 官方补丁说明 / 官方 Wiki / 开发者公告（一手，可单独支撑 E3）
2. 平台商店页、官方 FAQ、成就/奖杯列表（一手，可单独支撑 E3，但只能支撑其明示内容）
3. 高可信攻略站与社区 Wiki（二手，需两个独立来源）
4. 玩家论坛帖、视频攻略（二手，需交叉验证；视频可提级为 E2 当且仅当用户提供了该视频且内容可定位到时间戳）

检索时记录 `retrieved` 日期。**攻略类内容时效性极强，超过一个大版本未更新的来源一律降级。**

## 三、report-data.json 结构

新建数据文件时复制 `assets/sample-report-data.json` 再改。字段说明如下，标 `✱` 为必填。

```jsonc
{
  "schema_version": "1.0",

  "game": {
    "title": "✱ 游戏中文名",
    "title_en": "英文名（可选，用于检索来源）",
    "platforms": ["✱ PC", "PS5"],
    "version": "✱ 1.03",
    "version_confirmed": true,              // false 时报告首屏显示「版本未确认」
    "review_date": "✱ 2026-09-16",
    "language": "简体中文",
    "evidence_base": "✱ cleared_15h_plus",  // 见下方枚举
    "playtime_hours": 32.5,
    "completion": "主线通关，支线 70%，未打高难",
    "materials": [
      { "path": "C:/cap/boss3.png", "content": "第三关 BOSS 二阶段", "progress": "第 3 章" }
    ],
    "disclosure": "✱ 无利益相关，自购；无送测"
  },

  "review": {
    "summary": "✱ 一段 3-5 句的总评",
    "dimensions": [
      {
        "id": "✱ core_loop",
        "name": "✱ 核心循环与玩法深度",
        "score": 8,                          // 1-10 整数
        "weight": 0.20,                      // 与 rubric 固定值一致，脚本会校验
        "coverage_note": "未覆盖后期高难内容（可选）",
        "plus":  ["✱ 至少一条加分锚点，必须是可观察现象"],
        "minus": ["✱ 至少一条扣分锚点"],
        "evidence": ["✱ 至少一条 evidence 对象"]
      }
    ],
    "bias_audit": [
      { "risk": "✱ 平台偏好", "finding": "✱ 只测了手柄", "action": "✱ 键鼠适配未评价，不计入 tech_stability" }
    ],
    "series_note": "系列对比——分数不进总分",
    "pros": ["✱ 3-5 条"],
    "cons": ["✱ 3-5 条"],
    "sources": [
      { "title": "...", "url": "...", "retrieved": "2026-09-16", "kind": "official" }
    ]
  },

  "walkthrough": {
    "scope": "✱ 覆盖第 1-6 章主线 + 全部 4 个 BOSS；不含 DLC",
    "layers": {
      "A_main_route":  [ /* 章节对象，见 walkthrough-framework.md */ ],
      "B_blockers":    [ /* 卡关点对象 */ ],
      "C_bosses":      [ /* BOSS 对象 */ ],
      "D_collectibles":[ /* 隐藏要素对象 */ ]
    },
    "unverified": [
      {
        "question": "✱ 第 5 章西侧密道是否通向隐藏结局",
        "priority": "high",
        "how_to_verify": {
          "where": "✱ 第 5 章，拿到钩索后回到中央塔",
          "what": "✱ 对西侧外墙使用钩索，向上两段",
          "watch": "✱ 若出现一段无提示的攀爬动画，则判定存在"
        }
      }
    ]
  }
}
```

### evidence_base 枚举与总分上限

脚本按此截断总分，并在报告首屏显著标注。

| 值 | 含义 | 总分上限 |
|----|------|----------|
| `cleared_15h_plus` | 通关主线，时长 ≥ 15h，素材可核 | 10.0 |
| `cleared_under_15h` | 通关主线，时长 < 15h | 8.5 |
| `cleared_time_unverified` | 通关主线，但时长或素材未获本地核实 | 8.5 |
| `core_loop_entered` | 未通关，已进入核心循环 | 7.5 |
| `early_under_3h` | 游玩 < 3h 或未进入核心循环 | 不输出总分 |
| `research_only` | 未游玩，纯资料评测 | 6.5 |

**可核性优先于数字好看。** 通关进度若来自第一方数据（成就缓存、存档、跳杯记录），可标 E1；但游玩时长若只有玩家自述，**不得**据此走 `cleared_15h_plus`——自述时长上不了 10.0 那一档，改用 `cleared_time_unverified`，并在 `game.playtime_note` 里写清"用户自述、未获本地核实"。

### evidence 对象

```jsonc
{
  "tier": "✱ sourced",
  "note": "读法或简短说明",
  "source_url": "https://...",              // tier=sourced 且为一手来源时必填
  "source": [                               // tier=sourced 且为二手来源时必填两条及以上
    { "title": "...", "url": "...", "retrieved": "2026-09-16" },
    { "title": "...", "url": "...", "retrieved": "2026-09-16" }
  ],
  "material_ref": "C:/cap/boss3.png @ 00:32", // tier=user_material 必填
  "basis": "由钩索冷却 3s 与两段跳高度推断可达平台",  // tier=inferred 必填
  "how_to_verify": { "where": "...", "what": "...", "watch": "..." }  // inferred / unknown 必填
}
```

### images 数组可以挂在哪里

| 位置 | 用途 |
|------|------|
| `game.images` | 封面图（首屏展示，评测与攻略共用） |
| `review.dimensions[].images` | 该维度的佐证截图 |
| `walkthrough.layers.A_main_route[].images` | 该章场景图 |
| `walkthrough.layers.B_blockers[].images` | 卡关点的位置图解 |
| `walkthrough.layers.C_bosses[].images` | BOSS 出招前摇/阶段图 |
| `walkthrough.layers.D_collectibles[].images` | 隐藏要素所在位置 |

字段定义见第五节。`images` 全部可选，不写也能出报告。

## 四、校验规则（由 build_report.py 执行）

1. 必填字段缺失 → 报错。
2. `dimensions` 的 `id` 必须来自 rubric 的七个 id，且权重与 rubric 一致。
3. 权重和 ≠ 1.00（容差 0.001）→ 报错。
4. `score` 必须是 1-10 的整数。
5. 每个维度的 `plus` 与 `minus` 各至少一条；`evidence` 至少一条。
6. `tier == "sourced"` 时，`source_url` 或 `source`（≥2 条）必须有其一。
7. `tier == "user_material"` 时必须有 `material_ref`。
8. `tier` 为 `inferred` / `unknown` 时必须有 `how_to_verify`。
9. 攻略各层条目必须带 `evidence` 数组且非空；`tier == "unknown"` 的条目不得出现在 `A_main_route` 的步骤正文里（只能进 `unverified`）。
10. `evidence_base` 必须是上述五个枚举值之一。
11. `images[].kind` 必须是 `user_capture` / `official` / `diagram` 之一；`is_illustration: true` 的图不得标为前两者。
12. `images[].caption` 必填。
13. `kind=official` 必须提供 `source_url` 与 `credit`。
14. `kind=diagram` 必须显式写 `is_illustration: true`。
15. 声明了 `path` 但文件不存在 → **警告**（不报错），渲染为「待补图」占位框。
16. `A_main_route` 与 `C_bosses` 的条目无配图 → 警告，提示补图或改用 diagram。

校验失败会一次性列出全部问题并非零退出。**修正数据，不要改脚本。**

## 五、图片来源协议

**图片是证据，不是装饰。** 一条"BOSS 在砸地前会短暂消失"的文字，配一张砸地前摇的实拍帧，可信度完全不同——但前提是那张帧是真的。

### 三类图，一个禁区

| kind | 是什么 | 强制字段 | 渲染表现 |
|------|--------|----------|----------|
| `user_capture` | 玩家自己的截图 / 录像抽帧 | `path` | 正常嵌入 |
| `official` | 官方素材（商店页、新闻图、官方截图） | `path` + `source_url` + `credit` | 带「官方」徽标 + 来源链接 |
| `diagram` | 机制示意图，由代码或手绘生成 | `is_illustration: true` | 强制带「示意图 · 非游戏画面」水印 |
| ~~AI 生成冒充截图~~ | — | — | **校验拒绝** |

**禁区只有一条，但是硬的**：任何 `is_illustration: true` 的图都不允许标成 `user_capture` 或 `official`。想放示意图就用 `kind=diagram`，它会自动带上水印。用 AI 生成的图冒充游戏截图，比写错一句攻略恶劣得多——玩家会照着图去找一个不存在的东西。

### 图注纪律

`caption` 必填，而且必须回答「**这张图让玩家看什么**」。

- ✅「右上方暗角的墙面（无高亮）就是钩索点，左下角已点亮的普通点位作对比」
- ❌「第 3 章截图」——玩家看了等于没看

需要指位置时用 `anchors` 数组列出图中要点，渲染成图下方的要点列表。

### 没有图怎么办：待补图占位

**不要用生成图填坑。** 声明了 `path` 但文件不存在时，校验只警告、不报错，报告里渲染成「待补图」占位框，写明：需要什么图、路径是什么、怎么拿到。报告因此同时是一份采图清单。

`how_to_get` 字段写获取方式，最有价值的一条是抽帧命令：

```jsonc
{
  "kind": "user_capture",
  "path": "screenshots/boss2_phase2_slam.png",
  "timestamp": "02:14",
  "caption": "二阶段砸地前摇：BOSS 短暂离开画面下方，同时地面火焰颜色转深",
  "how_to_get": "对 C:/caps/boss2_phase2.mp4 运行 extract_frames.py --at 02:14"
}
```

### 什么时候可以什么都不放

`diagram` 不依赖任何外部素材，脚本会**自动**从已有数据生成两类示意图，无需你额外声明：

| 位置 | 自动生成 | 数据来源 |
|------|----------|----------|
| Layer A 顶部 | 章节推进骨架 | 各章的 `chapter` / `goal` / `time_estimate` / `missable` |
| Layer C 每个 BOSS | 阶段与血量进度条 | `phases` 的 `hp_range` |

进度条只按血量区间等比例绘制，**不虚构任何坐标或位置**——不确定 BOSS 站在哪，就不画它站在哪。

## 六、素材清单建立方法

1. 枚举素材目录，按修改时间排序，与游戏进度对齐。
2. 每条素材记录三要素：文件名 → 内容摘要 → 对应游戏进度节点。
3. 截图需记关键坐标或 UI 元素（用于后续核对数值）；录像需记可定位时间戳。
4. 素材无法与进度对齐时，标记为"进度未知"，不得用于 E1。
