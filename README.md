# 数据中心备用冷却链故障树 HTTP API

用 Python + FastAPI 实现的故障树分析服务。接收一个由 **AND / OR / k-of-n 门**组成的无环故障树，
基于精确有理数（`fractions.Fraction`）计算：

1. **顶事件概率**（约分分数数字符串）；
2. **逐项概率降幅**——把同一基础故障 ID 在所有支路同时置为“不发生”后，顶事件概率的下降量（每个提交的基础故障 ID 一项）；
3. **最小割集**——故障数最少的割集；同数时按“升序 ID 列表”的字典序取最小者。
   该割集中的故障全部发生、其余故障全部不发生时，顶事件必然发生（且去掉割集中任意一个都不再必然发生）。

基础故障 ID 彼此独立；**跨支路出现的同一 ID 视为同一个故障**；门可以被多个父节点复用。

## 算法与限制

- 门只支持 `AND`、`OR`、`KOFN`（至少 k 个输入发生），必须构成有向无环图（DAG）。
- 最多 **60 个基础故障**、**120 个门**。
- 展开顶事件后，出现次数 ≥2 的基础 ID（“重复故障”）最多 **8 个**。
- 枚举这最多 8 个重复 ID 的全部取值（≤ 256 种条件化），条件化后剩余结构是 read-once 公式，
  每次条件化用一遍树 DP 求出概率、各故障的概率降幅与最小割集；k-of-n 的卷积/除卷积结果跨条件化缓存。
- 60 个故障、120 个门、8 个重复 ID 的最坏情形在参考环境下 < 1 秒（约束为 5 秒）。
- 以下输入返回 **422 Unprocessable Entity**：门图存在循环、悬空引用（输入指向未定义节点）、
  空门、重复的节点定义（基础故障重名、门重名或与基础故障同名）、门输入重复、
  k-of-n 的 `k` 越界（须满足 `1 ≤ k ≤ 输入数`）、概率字符串不合法（非 0～1、小数位超过 6 位等）、
  基础故障/门数量超限、展开后重复 ID 超过 8 个、顶事件未定义。

合法概率字符串：`0`、`1`、`0.5`、`0.000001`、`1.000000`（小数点后最多 6 位；不接受 `.5`、`1.2`、`00.5`）。

## 一、Docker Compose 一键启动

需要 Docker（含 Compose 插件）。在项目根目录执行：

```bash
docker compose up --build
```

服务启动后监听：

- 接口基址：<http://localhost:8000>
- 健康检查：<http://localhost:8000/healthz>
- 交互式 API 文档（Swagger UI）：<http://localhost:8000/docs>
- OpenAPI Schema：<http://localhost:8000/openapi.json>

停止：在另一个终端执行 `docker compose down`（或在启动终端按 `Ctrl+C`）。

### 不使用 Docker 的本地启动方式

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 二、接口

### `POST /analyses`

请求体（JSON）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `top_event` | string | 顶事件：一个基础故障 ID 或门 ID |
| `basic_faults` | array | 基础故障列表，元素为 `{ "id": ..., "probability": "0～1、最多 6 位小数的字符串" }` |
| `gates` | array | 门列表，元素为 `{ "id", "type", "inputs", "k" }`；`k` 仅 `KOFN` 需要 |

响应体（JSON）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `top_event_probability` | string | 顶事件概率，约分分数，形如 `"3/8"`、`"0/1"`、`"1/1"` |
| `probability_decreases` | object | 键为每个提交的基础故障 ID，值为“该 ID 在所有支路置为不发生”后的顶事件概率降幅（约分分数） |
| `minimum_cut_set` | array | 故障数最少的割集（ID 已升序）；同数时字典序最小；顶事件不可能发生时为空数组 |

## 三、备用冷却链示例（与实现完全一致）

故障树结构（顶事件 `BACKUP_COOLING_FAIL`）：

```
BACKUP_COOLING_FAIL = OR(POWER_FAIL, TRAIN_A_FAIL, TRAIN_B_FAIL, TCU_2_OF_3, CTRL_FAIL)

POWER_FAIL    = AND(POWER_LOSS, GEN_FAIL)                       # 外电中断且应急柴油发电机启动失败
TRAIN_A_FAIL  = AND(SWG_A_OPEN, CHILLER_A_FAIL, PUMP_A_FAIL, VLV_A_FAIL)   # A 列任一段断开
TRAIN_B_FAIL  = AND(SWG_B_OPEN, CHILLER_B_FAIL, PUMP_B_FAIL, VLV_B_FAIL)   # B 列任一段断开
TCU_2_OF_3    = KOFN(k=2, TCU_A_FAIL, TCU_B_FAIL, TCU_C_FAIL)   # 3 台温控单元坏 2 台
CTRL_FAIL     = AND(CTRL_BUS_FAIL, SENSOR_BIAS)                 # 控制总线故障且传感器偏置
```

请求（完整文件见 [`examples/cooling_chain_request.json`](examples/cooling_chain_request.json)）：

```json
{
  "top_event": "BACKUP_COOLING_FAIL",
  "basic_faults": [
    {"id": "POWER_LOSS", "probability": "0.001"},
    {"id": "GEN_FAIL", "probability": "0.01"},
    {"id": "SWG_A_OPEN", "probability": "0.005"},
    {"id": "SWG_B_OPEN", "probability": "0.005"},
    {"id": "CHILLER_A_FAIL", "probability": "0.02"},
    {"id": "PUMP_A_FAIL", "probability": "0.02"},
    {"id": "VLV_A_FAIL", "probability": "0.01"},
    {"id": "CHILLER_B_FAIL", "probability": "0.02"},
    {"id": "PUMP_B_FAIL", "probability": "0.02"},
    {"id": "VLV_B_FAIL", "probability": "0.01"},
    {"id": "TCU_A_FAIL", "probability": "0.01"},
    {"id": "TCU_B_FAIL", "probability": "0.01"},
    {"id": "TCU_C_FAIL", "probability": "0.01"},
    {"id": "CTRL_BUS_FAIL", "probability": "0.003"},
    {"id": "SENSOR_BIAS", "probability": "0.004"}
  ],
  "gates": [
    {"id": "POWER_FAIL", "type": "AND", "inputs": ["POWER_LOSS", "GEN_FAIL"]},
    {"id": "TRAIN_A_FAIL", "type": "AND",
     "inputs": ["SWG_A_OPEN", "CHILLER_A_FAIL", "PUMP_A_FAIL", "VLV_A_FAIL"]},
    {"id": "TRAIN_B_FAIL", "type": "AND",
     "inputs": ["SWG_B_OPEN", "CHILLER_B_FAIL", "PUMP_B_FAIL", "VLV_B_FAIL"]},
    {"id": "TCU_2_OF_3", "type": "KOFN", "k": 2,
     "inputs": ["TCU_A_FAIL", "TCU_B_FAIL", "TCU_C_FAIL"]},
    {"id": "CTRL_FAIL", "type": "AND", "inputs": ["CTRL_BUS_FAIL", "SENSOR_BIAS"]},
    {"id": "BACKUP_COOLING_FAIL", "type": "OR",
     "inputs": ["POWER_FAIL", "TRAIN_A_FAIL", "TRAIN_B_FAIL", "TCU_2_OF_3", "CTRL_FAIL"]}
  ]
}
```

用 curl 调用（服务由 `docker compose up` 启动后）：

```bash
curl -X POST http://localhost:8000/analyses \
  -H 'Content-Type: application/json' \
  --data @examples/cooling_chain_request.json
```

响应（完整文件见 [`examples/cooling_chain_response.json`](examples/cooling_chain_response.json)）：

```json
{
  "top_event_probability": "10001040976113348955216550447/31250000000000000000000000000000",
  "probability_decreases": {
    "POWER_LOSS": "312403113621375080261250447/31250000000000000000000000000000",
    "GEN_FAIL": "312403113621375080261250447/31250000000000000000000000000000",
    "SWG_A_OPEN": "624799991676477566550447/31250000000000000000000000000000",
    "SWG_B_OPEN": "624799991676477566550447/31250000000000000000000000000000",
    "CHILLER_A_FAIL": "624799991676477566550447/31250000000000000000000000000000",
    "PUMP_A_FAIL": "624799991676477566550447/31250000000000000000000000000000",
    "VLV_A_FAIL": "624799991676477566550447/31250000000000000000000000000000",
    "CHILLER_B_FAIL": "624799991676477566550447/31250000000000000000000000000000",
    "PUMP_B_FAIL": "624799991676477566550447/31250000000000000000000000000000",
    "VLV_B_FAIL": "624799991676477566550447/31250000000000000000000000000000",
    "TCU_A_FAIL": "6187363628247947445245550297/31250000000000000000000000000000",
    "TCU_B_FAIL": "6187363628247947445245550297/31250000000000000000000000000000",
    "TCU_C_FAIL": "6187363628247947445245550297/31250000000000000000000000000000",
    "CTRL_BUS_FAIL": "374884486122120105253800447/31250000000000000000000000000000",
    "SENSOR_BIAS": "374884486122120105253800447/31250000000000000000000000000000"
  },
  "minimum_cut_set": ["CTRL_BUS_FAIL", "SENSOR_BIAS"]
}
```

说明（对照结果）：

- 顶事件概率约为 3.20033×10⁻⁴。
- 各支路概率量级：`POWER_FAIL = 0.001×0.01 = 1×10⁻⁵`；
  单列 `TRAIN_x_FAIL = 0.005×0.02×0.02×0.01 = 2×10⁻⁸`；
  2/3 温控单元约 3×10⁻⁴；`CTRL_FAIL = 0.003×0.004 = 1.2×10⁻⁵`。
- 最小割集大小为 2；大小为 2 的割集有
  `{POWER_LOSS, GEN_FAIL}`、`{CTRL_BUS_FAIL, SENSOR_BIAS}` 及三个 TCU 两两组合，
  按升序 ID 列表字典序最小的是 `["CTRL_BUS_FAIL", "SENSOR_BIAS"]`。
- `POWER_LOSS` 与 `GEN_FAIL` 的降幅相同（二者共同构成电源支路）。

### 跨支路同一 ID 即同一故障（小例子）

```bash
curl -X POST http://localhost:8000/analyses -H 'Content-Type: application/json' -d '{
  "top_event": "G",
  "basic_faults": [
    {"id": "x", "probability": "0.5"},
    {"id": "y", "probability": "0.5"}
  ],
  "gates": [
    {"id": "G", "type": "AND", "inputs": ["x", "H"]},
    {"id": "H", "type": "OR",  "inputs": ["y", "x"]}
  ]
}'
```

`x AND (y OR x)` 在布尔代数下等于 `x`，故响应为：

```json
{
  "top_event_probability": "1/2",
  "probability_decreases": {"x": "1/2", "y": "0/1"},
  "minimum_cut_set": ["x"]
}
```

### 错误示例（422）

```bash
curl -i -X POST http://localhost:8000/analyses -H 'Content-Type: application/json' -d '{
  "top_event": "G",
  "basic_faults": [{"id": "x", "probability": "1.2"}],
  "gates": []
}'
```

```
HTTP/1.1 422 Unprocessable Entity
{"detail":"invalid probability '1.2': expected a string between 0 and 1 with at most 6 decimal places"}
```

## 四、测试

```bash
pip install pytest httpx
python3 -m pytest tests/test_api.py -q        # HTTP 与 422 校验用例
python3 tests/crosscheck.py 1 600             # 随机故障树 vs 全枚举暴力交叉验证
python3 tests/perf.py                         # 60 故障 / 120 门 / 8 重复 ID 性能压测（< 5s）
```

## 目录结构

```
app/
  main.py      # FastAPI 路由、请求/响应模型、422 异常映射
  engine.py    # 校验、精确分数推理、条件化枚举、概率降幅、最小割集
examples/
  cooling_chain_request.json
  cooling_chain_response.json
tests/
  test_api.py  # HTTP 层与边界/422 测试
  crosscheck.py# 随机树对全枚举结果的交叉验证
  perf.py      # 最坏规模性能探针
Dockerfile
docker-compose.yml
requirements.txt
```
