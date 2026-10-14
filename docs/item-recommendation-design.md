# 英雄装备推荐数据设计

状态：设计草案，待确认后实施

更新时间：2026-08-31

## 1. 结论

新增一条独立的 OP.GG 装备数据采集链路，复用现有英雄映射、Chrome/Selenium 和更新框架，不新增第三方依赖。

每个英雄从以下页面读取数据：

```text
https://op.gg/zh-cn/lol/modes/aram-mayhem/{champion_slug}/build
```

只采集三个区域：

- 出门装 `starter`
- 鞋子 `boots`
- 核心装备 `core`

OP.GG 当前展示的是按顺序排列的“装备方案行”，没有发现可直接证明为单件装备优先级的数值字段。因此本项目采用可复现的稳定排序规则：

1. 方案行从上到下；
2. 同一方案内从左到右；
3. 以装备 ID 为唯一键，保留第一次出现的位置；
4. 重新生成连续的 `priority`，数值越小优先级越高。

去重只在同一区域内进行，不跨区域去重。例如“鞋子”同时出现在出门装和鞋子区域时，两处都保留。

## 2. 已确认事实

2026-08-31 对以下页面进行了只读验证：

```text
https://op.gg/zh-cn/lol/modes/aram-mayhem/missfortune/build
```

- HTTP 状态为 200，最终 URL 和 canonical URL 均为上述 `/build` 地址。
- 页面返回服务端渲染数据，三个区域分别对应 `Items Table`、`Boots Table` 和 `Builds Table`。
- 装备节点包含稳定的数字装备 ID、中文名称、图片地址和页面顺序。
- 当前页面没有发现明确的单件装备胜率、选取率或独立排名字段；能够确认的优先级只有方案行顺序与行内顺序。

Miss Fortune 当前页面示例：

| 区域 | 页面方案顺序 |
| --- | --- |
| 出门装 | `鞋子 + 生命药水×2 + 锯齿短匕`；`女神之泪 + 锯齿短匕` |
| 鞋子 | `狂战士胫甲`；`明朗之靴` |
| 核心装备 | `收集者 → 公理圆弧 → 赛瑞尔达的怨恨`；`收集者 → 无尽之刃 → 多米尼克领主的致意`；其余三组 |

按稳定去重规则，结果应为：

```text
starter: 鞋子, 生命药水×2, 锯齿短匕, 女神之泪
boots:   狂战士胫甲, 明朗之靴
core:    收集者, 公理圆弧, 赛瑞尔达的怨恨, 无尽之刃,
         多米尼克领主的致意, 饮血剑, 魔切
```

这里不按“出现次数”重新排序，因为出现次数是从多个方案推导出的新指标，不是 OP.GG 明示的单件优先级，可能改变第一套方案表达的顺序。

## 3. 数据来源与英雄 URL

继续使用现有 Riot Data Dragon 英雄映射：

```text
中文名 -> Data Dragon 英文 ID -> OP.GG URL
赏金猎人 -> MissFortune -> .../missfortune/build
```

- 英雄映射仍存放在 `data/champions.json`。
- URL slug 默认由 Data Dragon 英文 ID 转为小写得到。
- 如果发现 OP.GG 与 Data Dragon ID 不一致的真实案例，再新增显式例外映射；当前不提前增加映射表。

## 4. 数据文件

新增 `data/hero_items.json`。装备数据天然包含三个有序数组，JSON 比 CSV 更直接，也不会把组合定位字段拆散成大量重复行。

建议结构：

```json
{
  "schema_version": 1,
  "source": "op.gg/zh-cn/lol/modes/aram-mayhem",
  "fetched_at": "2026-08-31T00:00:00Z",
  "champions": {
    "MissFortune": {
      "cn_name": "赏金猎人",
      "starter": [
        {
          "item_id": 1001,
          "name": "鞋子",
          "quantity": 1,
          "priority": 1,
          "source_row": 1,
          "source_slot": 1
        }
      ],
      "boots": [],
      "core": []
    }
  }
}
```

字段语义：

| 字段 | 语义 |
| --- | --- |
| `item_id` | Riot/OP.GG 数字装备 ID，也是区域内去重键 |
| `name` | 页面中文名称，仅用于展示和诊断，不作为去重键 |
| `quantity` | 第一次出现时页面显示的数量；未显示时为 1 |
| `priority` | 稳定去重后的连续优先级，从 1 开始 |
| `source_row` | 第一次出现时的方案行，从 1 开始 |
| `source_slot` | 第一次出现时在方案内的位置，从 1 开始 |

不在第一版下载装备图片。运行时如需展示图片，可由 `item_id` 与游戏版本生成 Data Dragon 图片地址；是否做 GUI 展示另行设计。

## 5. 采集模块设计

新增 `scripts/item_scraper.py`，职责仅包含装备页面采集和稳定去重：

```text
crawl_item_recommendations(target_list)
  -> 单次启动 Chrome
  -> 逐英雄访问 /build
  -> extract_item_sections(driver)
  -> stable_dedupe(section_rows)
  -> 返回 {英文 ID: 英雄装备数据} 与失败列表
```

实现约束：

- 复用 `scripts.hero_scraper.setup_driver()`，不引入第二套 ChromeDriver 初始化逻辑。
- 不修改现有海克斯解析函数的返回结构，避免装备功能影响已工作的海克斯链路。
- 以三个表格的结构和装备图片中的数字 ID 定位数据，不使用易变的 Tailwind 样式类名作为唯一选择器。
- 先定位目标表格，再读取其 `tbody > tr`；禁止在整页范围抓取所有装备图片，以免混入导航、其他推荐或广告内容。
- 同一浏览器会话处理全部目标英雄，每 30 个英雄重启浏览器的现有策略可复用。
- 每个英雄最多重试 3 次，并继续遵守现有 `stop_event` 停止语义。

## 6. 更新链路

装备数据独立采集、统一发布：

1. `full`：采集全部官方英雄。
2. `smart` / `patch`：仅采集新增英雄、改名英雄或本地缺失英雄，其他英雄保留历史数据。
3. `spot_check`：第一版不使用装备差异触发全量更新，避免一次页面波动同时放大两条爬虫链路；后续有真实需要再增加。
4. `one_click`：与现有三个数据文件一起从 GitHub 下载 `hero_items.json`，仍采用临时文件全部成功后原子替换。
5. 维护者全量更新成功后，将 `hero_items.json` 纳入校验和推送文件列表。

需要同步修改的位置：

- `scripts/config.py`：新增装备数据路径常量。
- `scripts/updater.py`：接入采集、历史合并和一键下载。
- `scripts/maintain_data.py`：加入发布前校验和 Git 暂存范围。
- `README.md`：补充数据文件说明。
- `build.py`：当前会整体复制 `data/`，原则上无需改变；实施后只验证新文件确实进包。

## 7. 正确性与失败策略

### 7.1 稳定去重

对每个区域单独执行：

```python
seen = set()
result = []

for source_row, row in enumerate(rows, start=1):
    for source_slot, item in enumerate(row, start=1):
        if item.item_id in seen:
            continue
        seen.add(item.item_id)
        result.append(item.with_priority(len(result) + 1))
```

### 7.2 页面异常

以下情况将该英雄标记为失败，不写入空数据：

- 页面超时或进入错误页；
- 三个目标表格缺失任意一个；
- 装备节点无法解析数字 `item_id`；
- 已存在历史数据时，本次结果三个区域全部为空。

单英雄失败时保留该英雄的历史记录。首次建立数据文件时，如果英雄覆盖数未达到官方英雄数，维护者发布脚本必须阻止推送，不能把残缺快照发布给一键更新用户。

写文件继续采用“内存合并 -> 临时文件 -> 校验 -> 原子替换”，停止或异常时不部分覆盖正式数据。

## 8. 验证标准

实施完成至少验证：

1. 固定页面结构样本：三个区域均能准确隔离，导航或其他装备图片不会混入。
2. 重复装备：同一区域同一 `item_id` 只保留一次，保留第一次出现的位置与数量。
3. 区域边界：同一装备出现在两个区域时，两边各自保留。
4. 排序：结果严格按 `(source_row, source_slot)` 首次出现顺序排列，`priority` 连续。
5. Miss Fortune 回归：结果与本文第 2 节的已确认列表一致。
6. 失败保护：超时、表格缺失、空结果和用户停止都不能覆盖历史数据。
7. 数据完整性：英雄 key 全部来自 `champions.json`，无未知英雄、重复 key 或非数字装备 ID。
8. 打包检查：`hero_items.json` 存在于最终运行目录，内容与源码数据哈希一致。
9. Python 改动完成后执行项目规定的 AST 语法检查；真实 OP.GG 抓取结果与静态检查分开记录。

## 9. 实施边界

本设计只解决“抓取、去重、排序、保存和发布装备推荐数据”。暂不包含：

- GUI 中如何展示装备推荐；
- 游戏内自动购买或写入客户端配置；
- 根据海克斯、阵容或对局状态动态调整出装；
- 自行计算胜率、选取率或综合评分；
- 缓存或再分发装备图片。

这些能力没有当前已确认的交互需求，不能在本次实现中顺带加入。
