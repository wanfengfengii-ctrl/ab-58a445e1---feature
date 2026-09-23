# 档案片段重建台（Archive Fragment Reconstruction Console）

面向档案复核员的字节片段重建工具：导入或编辑带有重叠的十六进制字节片段，
由业务 API 计算可能的原文，页面展示**正文十六进制、采用的片段见证与冲突位置**。
当裁决为 **AMBIGUOUS** 时，复核员可在两份候选正文中选定一份，由服务生成
**证据隔离方案**：在不改动片段内容与权重的前提下，指出应暂不采用哪些片段，
使剩余证据按原规则重算后唯一得到所选正文，并为每个被隔离片段给出逐项反例。

- 前端：TypeScript + React 18 + Vite
- 后端：Python 3.11 + FastAPI（生产镜像中由 FastAPI 静态托管前端产物）
- 部署：多阶段 `Dockerfile` + 带健康检查的 `docker-compose.yml`，宿主机端口可通过 `HOST_PORT` 配置
- 验收：`verify` 一次性服务依次执行 **pytest 代码测试 → 前端构建 → API 冒烟**，自行退出并以退出码报告结果

---

## 1. 数据模型与业务规则

目标正文长度 `target_length` ∈ [1, 512] 字节；片段数 ∈ [2, 28]。

每个片段包含：

| 字段 | 约束 |
| --- | --- |
| `id` | 非空字符串，全局唯一 |
| `offset` | 从零起算的整数，不能为负 |
| `payload` | 非空、偶数长度的十六进制字符串（仅 `0-9 a-f A-F`） |
| `weight` | 整数可信权重，1 至 1,000,000 |

**有效方案**：选出的片段两两在重叠位置字节完全相同（一致覆盖），且其并集覆盖 `[0, target_length)` 的**每一个**字节。

**择优（字典序目标）**：

1. 最大化采用片段的**总权重**；
2. 总权重相同时，最大化采用**片段数**。

API 返回这两个最优值（`optimal.total_weight` / `optimal.fragment_count`）。

**裁决**：

- `UNIQUE` — 所有最优方案还原同一份正文，返回该正文（十六进制大写）与片段见证；
- `AMBIGUOUS` — 最优方案可还原多种正文，按**无符号字节序列**给出最小的两份（rank 1/2），并分别列出各自动员的片段见证；
- `IMPOSSIBLE` — 不存在完整一致的覆盖。`impossible_reason` 区分：
  - `GAP`：存在任何片段都覆盖不到的目标字节（返回具体位置 `uncovered_positions`）；
  - `CONFLICT`：每个字节虽有覆盖，但无法选出一组互不矛盾的片段完成全覆盖。

> 输入层的**片段冲突**（证据互相矛盾的位置）与裁决结果的**正文歧义**（不同最优方案还原不同正文）是两个独立概念：
> 例如高权片段互斥但一边占优时，存在冲突位置却裁决 UNIQUE；页面会同时展示两者，帮助复核员区分。

### 证据隔离规划（仅 AMBIGUOUS）

复核员在两份候选正文中选定一份目标正文 `T`，服务返回一组**暂不采用**（隔离）的片段 `S`：
片段内容与权重均不改动，仅把 `S` 从证据集中拿掉后**重新执行一次完整求解**，
要求重算结果为 `UNIQUE` 且唯一正文正是 `T`。

方案在所有可行隔离集中依次最小化：

1. **隔离片段数**；
2. **隔离权重总和**（被隔离片段的权重之和）；
3. 前两者仍持平时，隔离编号按字符串升序排列后的**列表字典序**。

响应同时给出：

- 重算后的最优值（`optimal.total_weight` / `optimal.fragment_count`）与唯一正文见证 `target_body`；
- 每个被隔离片段的**逐项反例** `counterexamples`：假设单独恢复该片段（其余仍隔离）后
  重新求解，裁决必然重新变为 `AMBIGUOUS`；反例给出此时与 `T` 并列最优的**竞争正文**
  及其见证（该见证必动员被恢复的片段），证明该片段在方案中不可省略。

> 规划器比较的是全部可行的竞争覆盖，而非当前展示的单个见证；判定与反例组装
> 均复用重建求解器的权重/片段数规则与剪枝，保证“重算”语义严格一致。
> 隔离方案可能不存在于 UNIQUE/IMPOSSIBLE 场景，且 `selected_hex` 必须是
> 当前裁决两份候选正文之一，否则返回 422。

## 2. HTTP API

### `POST /api/reconstruct`

请求：

```json
{
  "target_length": 4,
  "fragments": [
    {"id": "A", "offset": 0, "payload": "1122", "weight": 10},
    {"id": "B", "offset": 2, "payload": "2233", "weight": 20}
  ]
}
```

成功响应（节选）：

```json
{
  "status": "UNIQUE",
  "target_length": 4,
  "optimal": {"total_weight": 30, "fragment_count": 2},
  "bodies": [
    {
      "rank": 1,
      "hex": "11222233",
      "witness_fragment_ids": ["A", "B"],
      "adopted_fragments": [
        {"id": "A", "offset": 0, "payload": "1122", "weight": 10},
        {"id": "B", "offset": 2, "payload": "2233", "weight": 20}
      ]
    }
  ],
  "conflicts": [],
  "conflict_positions": [],
  "uncovered_positions": [],
  "impossible_reason": null
}
```

### 错误响应（HTTP 422）

重复编号、十六进制格式错、越界等一律返回 422，并在 `detail[].loc` 给出**字段位置**：

```json
{
  "detail": [
    {"loc": ["body", "fragments", 0, "payload"], "msg": "含有非十六进制字符…", "type": "value_error.hex"},
    {"loc": ["body", "fragments", 1, "id"], "msg": "编号重复: 'A' 首次出现于 fragments[0]", "type": "value_error.duplicate"},
    {"loc": ["body", "fragments", 1, "offset"], "msg": "片段越界: …", "type": "value_error.bounds"}
  ]
}
```

其他端点：`GET /healthz`（容器健康检查）、`GET /api/health`。

### `POST /api/isolate`

在 `/api/reconstruct` 同构请求上追加 `selected_hex`（选定的候选正文，大写或小写十六进制均可）：

```json
{
  "target_length": 2,
  "fragments": [
    {"id": "A", "offset": 0, "payload": "00", "weight": 100},
    {"id": "B", "offset": 0, "payload": "01", "weight": 100},
    {"id": "Z", "offset": 1, "payload": "FF", "weight": 1}
  ],
  "selected_hex": "00FF"
}
```

成功响应（节选）：

```json
{
  "selected_hex": "00FF",
  "isolated_fragment_ids": ["B"],
  "isolated_fragments": [
    {"id": "B", "offset": 0, "payload": "01", "weight": 100}
  ],
  "isolated_weight": 100,
  "optimal": {"total_weight": 101, "fragment_count": 2},
  "target_body": {"rank": 1, "hex": "00FF", "witness_fragment_ids": ["A", "Z"], "..." : "..."},
  "counterexamples": [
    {
      "fragment_id": "B",
      "restored_verdict": "AMBIGUOUS",
      "selected_hex": "00FF",
      "rival_hex": "01FF",
      "rival_witness_fragment_ids": ["B", "Z"],
      "optimal": {"total_weight": 101, "fragment_count": 2}
    }
  ]
}
```

422 场景（同样带字段位置）：输入本身不合法；当前裁决不是 `AMBIGUOUS`
（`value_error.plan_context`）；`selected_hex` 缺失/格式错/长度不符
（`missing` / `value_error.hex`）；所选正文不是两份候选之一
（`value_error.candidate`）。

## 3. 本地开发

```bash
# 后端
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000

# 前端(另开终端; /api 与 /healthz 自动代理到 :8000)
cd frontend
npm install
npm run dev
```

## 4. Docker 部署

```bash
# 宿主机端口默认 8080; 可自行配置
HOST_PORT=9000 docker compose up -d --build web
# 打开 http://localhost:9000
docker compose ps        # 查看健康状态 (healthy)
```

`web` 服务内置 Docker `HEALTHCHECK` 与 compose `healthcheck`，探测 `/healthz`。

## 5. verify 一次性验收服务

`verify` 是**一次性**服务（非守护）：构建后运行测试、前端构建与 API 冒烟，
全部通过退出码 `0`，任一步失败立即非零退出。

```bash
docker compose --profile verify build verify
docker compose --profile verify run --rm verify
echo "exit code = $?"   # 0 表示全部通过
```

容器内执行的三步与本地一致：

1. `pytest`：求解器、隔离规划器与 API 共 59 项测试，覆盖高权片段互斥、等分正文、缺口、冲突致不可行、
   等权双正文的无符号字节序、同权重按片段数决胜、非法输入 422 与字段定位；隔离规划另覆盖三级择优
   （隔离数 → 隔离权重和 → 编号字典序）、逐项反例、rank-1/rank-2 选择，以及与全子集暴力枚举的随机交叉验证；
2. `npm run build`：TypeScript 严格类型检查 + Vite 生产构建；
3. `scripts/smoke_api.py`：容器内启动真实 uvicorn，发起 HTTP 冒烟（健康检查、静态首页、
   UNIQUE / AMBIGUOUS / IMPOSSIBLE 与 422 字段定位，以及 AMBIGUOUS 下的隔离规划成功路径、
   rank-2 选择、非 AMBIGUOUS 拒绝与非候选正文 422）。

本地也可直接运行：

```bash
APP_DIR=$PWD bash scripts/verify.sh
```

## 6. 前端复核视图

- **输入区**：内联编辑编号/偏移/载荷/权重（2–28 行），或"导入 JSON"批量导入；
  字段级红框即时提示；样例芯片一键载入（等分正文 / 高权互斥 / 正文歧义 / 缺口 / 冲突致不可行）。
- **修改即失效**：任何输入改动都会立即撤下旧裁决并显示提示，必须重新提交——避免新输入配旧结论。
- **正文十六进制视图**：每行 16 字节，带偏移与 ASCII 列；悬停见证片段高亮其覆盖区间，
  绿色底色标出多片段一致重叠位置。
- **裁决横幅**：绿（UNIQUE）/ 琥珀（AMBIGUOUS）/ 红（IMPOSSIBLE），并附最大总权重与最优片段数。
- **歧义对照**：AMBIGUOUS 时并列展示字节序最小的两份正文、首个差异偏移，以及各自片段见证；
  每张正文卡片提供"以此正文规划证据隔离"按钮。
- **证据隔离方案**：从歧义正文发起规划后，展示隔离片段清单（编号升序，内容与权重原样可读）、
  隔离片段数与权重总和、重算后的最优值与唯一正文见证；**逐项反例**可逐条展开，
  显示单独恢复该片段后的 AMBIGUOUS 裁决、竞争正文及其见证（动员该片段者高亮）。
  任何输入改动或新的重建结果都会立即撤下旧方案与在途请求。
- **冲突与缺口扫描栅格**：逐字节标注冲突（琥珀）与缺口（红），点击冲突格查看各片段主张的字节，
  使"片段冲突"与"正文歧义"清晰可分。

## 7. 求解算法

`backend/solver.py` 将片段建模为**冲突图**上的带权独立集 + 覆盖约束：

- 两片段区间相交且存在字节分歧则连冲突边（不可同时采用）；
- 每个片段对应一个区间覆盖位掩码（目标 ≤ 512 字节，用 Python 大整数表示）；
- DFS 按"偏移升序、权重降序"分支，含分支做包含/排除搜索；
- 剪枝：若剩余所有相容片段全取后仍无法补全覆盖，或权重/计数上界不优于当前最优，立即回溯；
- 找到最优解后按正文内容归并，区分 UNIQUE / AMBIGUOUS，并以字节字典序（等价无符号字节序）取最小两份。

求解器已通过 300 组随机小实例与暴力枚举的交叉验证（零分歧），28 片段对抗实例在毫秒级完成。

证据隔离规划（`backend/planner.py`）在同一冲突图上求解：

- 与目标正文逐字节一致的片段两两相容且权重为正，目标阵营的基准值（总权重、片段数）固定为"全取一致片段"；
- 于是只需隔离**不一致片段**，问题归约为最小代价击中集：每个追平基准、还原异正文的可行覆盖都必须被至少一个被隔离片段击中；
- 分支定界枚举隔离集，覆盖判定复用求解器同构的 DFS（枚举全部可行覆盖而非单个见证）；
- 次序按"隔离数 → 隔离权重和 → 编号字典序"剪枝与比较；逐项反例强制竞争见证动员被恢复片段，
  并再次调用求解器权威重算，验证恢复后确实重回 AMBIGUOUS；
- 规划器同样通过 700+ 组随机实例（rank-1/rank-2）与全子集暴力枚举的交叉验证。
