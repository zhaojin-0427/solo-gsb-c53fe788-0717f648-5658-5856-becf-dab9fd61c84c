# 数据中心备用冷却链故障树分析 HTTP API

用 **Python + FastAPI** 实现的故障树（Fault Tree）分析服务。给定一棵以“门 +
基础故障”描述的无环故障树，服务精确计算：

1. **顶事件发生概率**；
2. **逐项概率降幅**——把某个基础故障 ID 在所有支路中统一置为“不发生”后，
   顶事件概率下降的绝对量；
3. **最小割集**——故障数最少的割集；同故障数时，取“升序 ID 列表”字典序
   最小者。割集内故障全部发生、其余基础故障全部不发生时，顶事件必然触发。

所有概率结果均为 **约分后的精确分数字符串**（`"分子/分母"`），内部全程使用
`fractions.Fraction` 做精确有理运算，不使用浮点。

---

## 目录

- [一、快速开始（Docker Compose 一键启动）](#一快速开始docker-compose-一键启动)
- [二、接口说明](#二接口说明)
- [三、备用冷却链示例（请求与响应）](#三备用冷却链示例请求与响应)
- [四、字段与校验规则](#四字段与校验规则)
- [五、算法与规模/性能](#五算法与规模性能)
- [六、本地开发（不用 Docker）](#六本地开发不用-docker)
- [七、测试与性能试算](#七测试与性能试算)
- [八、项目结构](#八项目结构)

---

## 一、快速开始（Docker Compose 一键启动）

前置条件：已安装 Docker 与 Docker Compose v2（`docker compose version` 可用）。

```bash
# 1. 进入项目目录
cd datacenter-cooling-faulttree

# 2. 构建镜像并后台启动
docker compose up -d --build

# 3. 健康检查
curl http://localhost:8000/health
# {"status":"ok"}

# 4. 停止服务
docker compose down
```

**接口访问地址：**

| 用途 | 方法 | 地址 |
| --- | --- | --- |
| 健康检查 | GET | `http://localhost:8000/health` |
| 故障树分析 | POST | `http://localhost:8000/analyses` |
| 交互式 API 文档（Swagger UI） | GET | `http://localhost:8000/docs` |
| OpenAPI 描述 | GET | `http://localhost:8000/openapi.json` |

> 容器内服务监听 `0.0.0.0:8000`，并映射到宿主机 `8000` 端口。如端口被占用，
> 可修改 `docker-compose.yml` 中 `"8000:8000"` 左侧端口。

---

## 二、接口说明

### `POST /analyses`

请求头：`Content-Type: application/json`

请求体字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `top_event` | string | 顶事件节点 ID，可以是基础故障 ID 或门 ID |
| `basic_events` | array | 基础故障列表，每项 `{"id", "probability"}` |
| `basic_events[].id` | string | 基础故障 ID，全局唯一；同一 ID 跨支路表示**同一个**故障 |
| `basic_events[].probability` | string | 0~1 之间、**最多六位小数**的十进制字符串，如 `"0.000025"` |
| `gates` | array | 门列表，门可被任意多个上级门引用（**门可复用**） |
| `gates[].id` | string | 门 ID，全局唯一，且不得与基础故障 ID 冲突 |
| `gates[].type` | string | `AND` / `OR` / `KOFN` |
| `gates[].inputs` | array | 输入节点 ID 列表（非空、不可重复），可引用基础故障或其他门 |
| `gates[].k` | integer | 仅 `KOFN` 需要：n 个输入中至少 k 个发生即触发，要求 `1 ≤ k ≤ n` |

门语义：

- `AND`：全部输入发生才发生；
- `OR`：任一输入发生即发生；
- `KOFN`（k-out-of-n 投票门）：n 个输入中**至少 k 个**发生即发生。
  `k=1` 等价于 `OR`，`k=n` 等价于 `AND`。

响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `top_event_probability` | string | 顶事件概率，约分分数，如 `"1/4"` |
| `probability_reductions` | object | 每个基础故障 ID 对应“将其在所有支路置为不发生”后顶事件概率的**绝对降幅**（约分分数，非负） |
| `minimum_cut_set` | array | 最小割集：故障数最少，同数时升序 ID 列表字典序最小；元素为升序基础故障 ID |

任何非法输入返回 **HTTP 422**，响应体形如 `{"detail": "错误原因"}`。

---

## 三、备用冷却链示例（请求与响应）

### 场景

数据中心备用冷却链的失效（顶事件 `G_top`）来自两条并联冗余支路，任一支路
失效即触发顶事件：

- 两条支路共享同一 **双路供电** `Pow` 与同一组冷却水泵；
- 三台冷却水泵 `P1/P2/P3` 中至少 **2 台**失效（2-of-3 投票门 `G_pumps`）才
  失去输水能力；
- 支路 A：供电失效 **且** 水泵失效 **且** 冷却塔组失效 `CTS` **且** 支路 A
  电动阀失效 `E3`；
- 支路 B：供电失效 **且** 水泵失效 **且** 板换侧失效 `HXS` **且** 支路 B
  电动阀失效 `E7` **且** 共用电动阀失效 `E3`。

`Pow`、水泵门、`E3` 等被两条支路复用，因此 `Pow/P1/P2/P3/E3` 在展开后的
故障树中重复出现，是“同一个故障在多条支路上的同一个随机变量”。

### 请求

请求体见 [`examples/cooling_chain.json`](examples/cooling_chain.json)：

```json
{
  "top_event": "G_top",
  "basic_events": [
    {"id": "Pow", "probability": "0.005"},
    {"id": "P1",  "probability": "0.01"},
    {"id": "P2",  "probability": "0.02"},
    {"id": "P3",  "probability": "0.04"},
    {"id": "CTS", "probability": "0.001"},
    {"id": "HXS", "probability": "0.002"},
    {"id": "E3",  "probability": "0.03"},
    {"id": "E7",  "probability": "0.07"}
  ],
  "gates": [
    {"id": "G_pumps",   "type": "KOFN", "k": 2, "inputs": ["P1", "P2", "P3"]},
    {"id": "G_branchA", "type": "AND",  "inputs": ["Pow", "G_pumps", "CTS", "E3"]},
    {"id": "G_branchB", "type": "AND",  "inputs": ["Pow", "G_pumps", "HXS", "E7", "E3"]},
    {"id": "G_top",     "type": "OR",   "inputs": ["G_branchA", "G_branchB"]}
  ]
}
```

用 `curl` 调用：

```bash
curl -X POST http://localhost:8000/analyses \
  -H 'Content-Type: application/json' \
  --data @examples/cooling_chain.json
```

### 响应

```json
{
  "top_event_probability": "29579367/125000000000000000",
  "probability_reductions": {
    "Pow": "29579367/125000000000000000",
    "P1":  "12481467/125000000000000000",
    "P2":  "21030417/125000000000000000",
    "P3":  "6326223/31250000000000000",
    "CTS": "25946367/125000000000000000",
    "HXS": "3629367/125000000000000000",
    "E3":  "29579367/125000000000000000",
    "E7":  "3629367/125000000000000000"
  },
  "minimum_cut_set": ["CTS", "E3", "P1", "P2", "Pow"]
}
```

结果解读：

- 顶事件概率为 `29579367/125000000000000000 ≈ 2.366×10⁻¹⁰`。
- `Pow`、`E3` 的降幅与顶事件概率相同：这两个故障在两条支路上都是“共同
  前置条件”，把它们置为不发生，顶事件即不可能发生。
- `P1/P2/P3` 降幅不同，符合 2-of-3 投票门中各泵失效概率的差异。
- 最小割集 `["CTS","E3","P1","P2","Pow"]`：这 5 个故障同时发生、其余
  故障（`P3/HXS/E7`）均不发生时，水泵门因 `P1,P2` 失效而触发，支路 A 的
  四个条件全部满足，顶事件必然触发；且不存在 4 个及更少故障的割集。

### 非法输入示例（422）

```bash
# 循环引用：G1 -> G2 -> G1
curl -i -X POST http://localhost:8000/analyses \
  -H 'Content-Type: application/json' \
  -d '{"top_event":"G1","basic_events":[{"id":"A","probability":"0.1"}],
       "gates":[{"id":"G1","type":"AND","inputs":["A","G2"]},
                {"id":"G2","type":"OR","inputs":["G1"]}]}'
# HTTP/1.1 422 Unprocessable Entity
# {"detail":"故障树存在循环引用：门 'G1' 处于环中"}
```

其余会返回 422 的情形见 [第四节](#四字段与校验规则)。

---

## 四、字段与校验规则

以下情况一律返回 **422 Unprocessable Entity**：

- **循环引用**：门之间（含门直接引用自身）构成环；
- **悬空引用**：门输入或顶事件引用了既非基础故障、也未定义的门；
- **空门**：`inputs` 为空数组；
- **重复定义**：基础故障 ID 重复、门 ID 重复、门 ID 与基础故障 ID 冲突；
- **重复门输入**：同一个门的 `inputs` 中同一节点出现多次；
- **越界 k**：`KOFN` 缺 `k`、`k` 非整数，或 `k` 不在 `[1, 输入数]` 内；
- **概率格式错误**：不是字符串、超出 0~1、小数位超过 6 位、使用科学计数法
  或分数等；合法示例如 `"0"`、`"1"`、`"0.5"`、`"0.000001"`、`"1.000000"`；
- **门类型非法**：`type` 不是 `AND`/`OR`/`KOFN`；
- **超限输入**：基础故障超过 60 个、门超过 120 个；
- **展开后重复基础 ID 超过 8 个**：把故障树沿门复用关系展开成树后，出现次数
  大于 1 的基础故障 ID 超过 8 个；
- 请求体不是合法 JSON 或不是 JSON 对象。

说明：

- 门引用未定义节点、门的环即使从顶事件不可达，也会被检出。
- 基础故障即便定义了但从顶事件不可达，其降幅为 `"0/1"`，也不会进入割集。
- `AND/OR` 门携带的 `k` 字段会被忽略。

---

## 五、算法与规模/性能

### 为什么不能简单按独立叶子相乘

基础故障彼此独立，但同一个基础故障 ID 可经“可复用的门”出现在展开树的多条
支路上，这些支路共享同一个随机变量，并不独立。直接把叶子当独立量做乘积/
求和会得到错误结果。

### 算法

1. 自顶向下统计每个基础 ID 在“展开成树”后的出现次数；出现次数 > 1 的至多
   8 个，称为**共享变量 S**。
2. 对 S 的全部 `2^|S| ≤ 256` 种 0/1 取值枚举。每种取值下，复用门都成为确定
   常量，剩余部分是一棵叶子互不相同、子树相互独立的树：
   - **顶事件概率**：树上自底向上的概率 DP（AND 取积、OR 取补
     `1−Π(1−p)`、KOFN 用“发生数分布”递推）；
   - **概率降幅**：对“唯一叶子”用一次反向自动微分（伴随法）求
     `∂P(顶)/∂p`，再乘叶子自身概率；对“共享变量”用 do-干预（强制为 0）后
     按各取值权重精确累加；
   - **最小割集**：树上的布尔 DP，状态为（恒成立 / 恒不成立 / 需要发生的
     有序叶子集合），KOFN 用组合 DP 选最优子集；再把本取值下置 1 的共享
     故障并入候选，跨全部取值取全局最优。
3. 全程 `Fraction` 精确有理运算，输出约分分数。

### 规模与性能

- 支持 **至多 60 个基础故障、120 个门**；
- 展开顶事件后重复的基础 ID **至多 8 个**；
- 极限规模（60 故障、120 门、8 个重复 ID、深度复用）单次试算约 **1 秒**，
  满足 **5 秒内**完成交互试算的要求。可运行
  [`scripts/perf_check.py`](scripts/perf_check.py) 复现。

---

## 六、本地开发（不用 Docker）

需要 Python 3.11+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 启动开发服务器（带热重载）
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

然后访问 `http://localhost:8000/docs`。

---

## 七、测试与性能试算

```bash
# 安装测试依赖
pip install -r requirements.txt pytest httpx

# 运行全部测试（含与朴素 2^n 全枚举的随机对拍、422 校验、边界条件）
python3 -m pytest -q

# 极限规模性能试算（应在 5 秒内）
PYTHONPATH=. python3 scripts/perf_check.py
```

测试中包含一个朴素的全状态空间枚举参照实现，对随机生成的数百棵故障树
（含门复用、KOFN、深层嵌套）逐一比对顶事件概率、逐项降幅与最小割集，保证
引擎实现正确。

---

## 八、项目结构

```
.
├── app/
│   ├── __init__.py
│   ├── fault_tree.py      # 校验、概率/降幅/割集核心引擎
│   └── main.py            # FastAPI 应用与 POST /analyses
├── examples/
│   └── cooling_chain.json # 备用冷却链示例请求
├── scripts/
│   └── perf_check.py      # 60 故障 / 120 门 / 8 重复 ID 性能试算
├── tests/
│   ├── test_bruteforce.py # 与朴素全枚举的随机对拍
│   └── test_validation.py # 422 校验与边界条件
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```
