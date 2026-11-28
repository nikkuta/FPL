# 项目进度与决策记录（Progress & Decisions）

> **当前阶段**：**Phase 1.1（第一条最小垂直闭环）** 进行中  
> **当前任务**：Task 2 已完成并验证（31/31 单测通过）→ 即将进入 Task 3（原生 Agent 编排循环与确定性护栏）

---

## 1. Phase 0 → Phase 1 路线图进度

- [x] **仓库初始化与协作规范**：`AGENTS.md`（根目录）与 `docs/prompts.md` 已入库
- [x] **Phase 0.0 领域规则与竞品深度调研**：`docs/00-fpl-domain-research.md` 与 `docs/00-competitor-research.md` 已确认
- [x] **Phase 0.1 一页产品定义（v0.3 真 Agent 版）**：`docs/01-product-one-pager.md` 已确认
- [x] **Phase 0.2 核心用户流程与状态机**：`docs/02-user-flow-and-state-machine.md` 已确认
- [x] **Phase 0.3 系统架构、确定性/Agent 边界与关键数据对象**：`docs/03-architecture-and-data-model.md` 与 `docs/03-architecture-explained.md` 已确认
- [ ] **Phase 1.1 最小垂直闭环**：进行中
  - [x] **Task 1：核心数据契约与 FPL 快照适配器**（`docs/specs/01-snapshot-and-ft-prd.md`、`backend/models.py`、`backend/fpl_client.py`、`tests/test_fpl_client.py`）
  - [x] **Task 2：5 个确定性工具与硬规则校验器**（`docs/specs/02-deterministic-tools-prd.md`、`backend/tools.py`、`tests/test_tools.py`）
  - [ ] **Task 3：原生 Agent 编排循环与确定性护栏**
  - [ ] **Task 4：SQLite 持久化与赛后复盘计分**
  - [ ] **Task 5：FastAPI 接口层与 SSE 流式编排**
  - [ ] **Task 6：轻量单页工作台前端与端到端联调**
- [ ] **Phase 1.2 第一阶段验收用例与离线评测基线**：未开始

---

## 2. 已决事项（Decided）

| 编号 | 决策项 | 结论 | 依据 / 备注 |
| :--- | :--- | :--- | :--- |
| D-01 | 用户准入方式 | **无登录，输入公开 `Team ID` 即用** | 降低演示门槛，聚焦决策与评测闭环，不做账号权限体系 |
| D-02 | FPL 核心规则处理 | **纳入确定性规则校验 + 极简人话卡片展示** | 自动读取银行余额、基于 `started_event` 推算 FT、校验 -4 罚分与同队 3 人限制 |
| D-03 | 卖出价处理 | **公开 API 估算 + 手动校准兜底** | Phase 1 不绑定官方账号/Cookie，避免隐私与合规风险 |
| D-04 | 芯片与全网爬虫 | **Phase 1 明确不做** | 不做芯片跨周规划；新闻先支持手动粘贴文本与样例输入 |
| D-05 | 技术栈大方向 | **FastAPI + 极简单页 HTML/JS + SQLite** | 支持渐进式首屏与 SSE 流式展示推理步骤，零多余前端/数据库包袱 |
| D-06 | 操作执行权（HITL） | **用户手动去官网操作，绝不自动提交** | `AGENTS.md` 核心红线 |
| D-07 | One Move 动作粒度 | **1 个核心转会决策 + 配套零成本首发/队长** | 主推“单次转会+配套排阵 / 不买人仅调阵 / 完全不动”；用户追问时才算多转会并标明 -4 罚分 |
| D-08 | Agent 架构模式 | **受控真 Agent 内核 + 确定性工具箱（路线 1）** | AI 作为大脑自主编排工具、提出假设并在校验失败后自我修正；规则退居工具箱与硬护栏 |
| D-09 | 首屏加载体验 | **渐进式渲染（先秒出快照，决策区流式加载）** | 避免打开即全屏 Loading 转圈，同时让用户感知 Agent 实时推理步骤 |
| D-10 | 约束冲突无解处理 | **严守合法底线，保留上一版建议 + 指明冲突点** | 绝不拼凑非法方案，给出放宽约束建议供用户调整重算 |
| D-11 | 外部 API 异常处理 | **直接明确报错 + 重试按钮（不擅自切样例）** | 避免查自己球队超时却看到别人样例产生歧义；样例仅作为入口显式按钮 |
| D-12 | Agent 编排方式 | **原生轻量 Tool-Calling 循环（上限 8 步）** | 零黑盒框架（不用 LangGraph），方便 PM 走查工具调用轨迹与做离线 Evals |
| D-13 | 大模型接入协议 | **统一 OpenAI 兼容接口（默认 DeepSeek API）** | 性价比高，且跑 Evals 时可零改代码横向对比不同模型 |

---

## 3. 待定事项（Open Questions）

| 编号 | 待定项 | 预计拍板时机 | 说明 |
| :--- | :--- | :--- | :--- |
| Q-01 | 异常状态与重算状态流转细节 | Phase 0.2（流程与状态机） | 例如：输入无效 Team ID、API 超时、加约束后无合法解时界面如何流转 |
| Q-02 | “不动”的基线收益模型如何定义 | Phase 0.3（系统架构） | 保留 1 个 FT 的多轮机会价值如何量化，避免乱推荐或死守 |
| Q-03 | 轻量 Web 工作台具体选型与存储方式 | Phase 0.3（系统架构） | 例如 FastAPI + 极简 HTML/JS 或 Streamlit，以及本地 JSON/SQLite 存储 |
| Q-04 | 离线回放评测集（Offline Replay）来源 | Phase 1.2（评测基线） | 固定历史 Gameweek 快照样例的构造与评测指标阈值 |
