# 模块 PRD · Task 2：5 个确定性工具与硬规则校验器

> **状态**：已确认（进入 Task 2 实现）  
> **所属阶段**：Phase 1.1（第一条最小垂直闭环 · 第 2 步）  
> **关联基线**：[01-product-one-pager.md](../01-product-one-pager.md)、[03-architecture-and-data-model.md](../03-architecture-and-data-model.md)、[01-snapshot-and-ft-prd.md](./01-snapshot-and-ft-prd.md)  
> **实现边界**：100% 确定性程序，零大模型调用。

---

## 1. 业务目标与范围

### 1.1 解决什么问题
1. **给 AI 配上“电子秤与计算器”**：大模型不能靠脑子心算预算、同队人数或盲猜球员身价。本模块把 FPL 2026/27 规则封装成 5 个标准化工具，所有查人、算钱、判违规、排首发均由确定性代码执行。
2. **让 AI 撞墙后能自我修正**：当 Agent 提出的方案违反规则（如超预算 £0.3m、阿森纳已满 3 人）时，工具返回**清晰的人话报错**，让 Agent 读懂原因并主动换方案重试。
3. **落定 Q-02（“不动”的基线参考模型）**：用公开、透明、可解释的简化公式，为“原封不动”和“不买人但调到最佳首发/队长”算出客观参考锚点，并量化保留 FT 的多轮机会价值，供 Agent 平等权衡 `hold` 与 `transfer`。

### 1.2 本步做什么 vs 不做什么
* **做什么（In-Scope）**：
  * 实现 5 个确定性工具函数及对应的 OpenAI Tool-Calling JSON Schema 描述；
  * 实现 FPL 硬规则校验器（预算、FT 与 -4 罚分、同队 ≤ 3 人、2/5/5/3 配额、首发阵型）；
  * 实现透明的单周参考预期分与保留 FT 期权价值参考模型（含 `lineup_only` 与 `hold` 对比）。
* **不做什么（Out-of-Scope）**：
  * 不调用 DeepSeek API（属于 Task 3）；
  * 不做跨多周芯片规划（WC/FH/BB/TC）；
  * 不持久化到 SQLite（属于 Task 4）。

---

## 2. 透明参考分与“不动”基线模型（Q-02 定稿口径）

> **定位声明（规则 vs Agent 边界）**：本节公式只负责算出**客观参考锚点（Reference Anchor）**，保证可复现、防大模型心算出错，**不等于最终拍板**。最终建议由 **Agent 大脑**结合参考分、赛前突发新闻、用户追问约束综合评估后拍板（若新闻与公式冲突，Agent 可调用 `suggest_best_lineup` 屏蔽风险球员并推翻参考分）。

### 2.1 单名球员下轮参考预期分（`expected_pts`）
1. **基础实力分**：`base = 0.6 * form + 0.4 * ppg`（近 30 天状态占 6 成，赛季场均占 4 成；官方返回的 `form` 与 `ppg` 已包含历史 DefCon +2 得分，不再额外重复加分）。
2. **健康出场系数（`avail`）**：
   * 状态 `a`（可用）：`1.0`
   * 状态 `d`（存疑）：取 `chance_of_playing / 100`（若为空则按 `0.5`）
   * 状态 `i` / `s` / `u` / `n`（伤停/停赛/离队）：`0.0`
3. **赛程难度系数（`fdr_mult`，取下轮对应官方 FDR 难度值）**：
   * `FDR 1 → 1.15`、`FDR 2 → 1.08`、`FDR 3 → 1.00`、`FDR 4 → 0.92`、`FDR 5 → 0.85`
   * 若下轮轮空（BGW）记为 `0`；若双赛周（DGW）则把两场系数相加。
4. **最终单轮参考分**：`expected_pts = round(base * avail * fdr_mult, 2)`。

### 2.2 “不买人”基线对比与 FT 出手门槛（对齐 FPL Review 基准）
* **为什么要区分“原封不动”和“不买人但调阵（`lineup_only`）”**：
  哪怕一个人都不用买，把替补席健康高分球员换上首发、重新指定最佳队长（得分 × 2），本身就是零成本的正确动作。因此基线工具会同时算出两档：
  1. **原封不动得分（`as_is_score`）**：按上周锁定的当前 11 人首发 + 当前队长（队长分 × 2）直接加总。
  2. **不买人最佳排阵得分（`best_no_transfer_score`）**：在不做任何转会前提下，调用 `suggest_best_lineup` 重新排出最优 11 人首发与正副队长（含最佳队长 × 2）后的总分。
* **纯净分差（不混减虚拟分，一眼看懂）**：
  `纯净净收益（net_pts_gain） = (转会后最佳首发含队长×2总分 - 扣分 hit_points) - 不买人最佳排阵得分`
* **FT 出手参考门槛（对齐 FPL Review `FT Value` 基准）**：
  * 当 `free_transfers < 5` 时：每消耗 1 个 FT 的参考门槛为 **`2.0 分`**（仅作提示，不从纯净分差里硬减）。即：若纯净净收益 `< 2.0 分`，工具会提示“提升较小，优先考虑保留 FT 不动”；
  * 当 `free_transfers == 5` 时：已达官方 5 个上限，再省也无法累积，出手门槛为 **`0.0 分`**。

> **具体算例**：
> 假设你现有阵容不买人但调到最佳首发+队长后，参考总分为 **`55.0` 分**（当前手握 `1 FT`，参考门槛 `2.0 分`）。
> * **情况 A（刚需换伤员）**：卖掉受伤替补 `Iroegbunam` 买 `Palmer`，新阵最佳首发+队长参考总分升至 **`61.8` 分**（不扣分）。纯净净收益 = `61.8 - 0 - 55.0 = +6.8` 分（远超 `2.0 分`门槛）→ 明显优于不动。
> * **情况 B（小幅折腾）**：把 `5.2` 分的健康中场换成 `5.8` 分的新人，新阵总分 **`55.6` 分**。纯净净收益 = `55.6 - 0 - 55.0 = +0.6` 分（低于 `2.0 分`门槛）→ 工具提示“提升不足以抵消消耗 FT 的机会成本”，Agent 倾向推荐 **“不动（Hold）或仅调阵”**。

---

## 3. 5 个确定性工具契约

### 工具 1：`search_transfer_candidates`（查候选球员）
* **入参**：
  * `position`: `"GKP" | "DEF" | "MID" | "FWD"`（必填）
  * `max_price`: `float`（最高可承受买入价 £m，必填）
  * `exclude_teams`: `list[str]`（可选，如 `["ARS"]`）
  * `limit`: `int = 10`（默认返回 Top 10）
* **确定性行为**：从 `FPLUniverse` 过滤掉已在当前 15 人阵容中的球员、不可用球员（`status in ("i", "s", "u", "n")`）、价格高于 `max_price` 的球员、以及 `exclude_teams` 中的球队；按参考分降序返回 Top 10 精简信息（`id, name, team, pos, now_cost, form, ppg, next_3_fdr, expected_pts`）。
* **错误返回（供 AI 自修正）**：
  * 若 `position` 非法 → `参数错误：position 必须是 GKP/DEF/MID/FWD 之一。`
  * 若筛选后为空 → `当前预算 £{max_price}m 在 {position} 位置无可用健康候选人，请提高预算或先卖出高价球员腾出资金。`

### 工具 2：`get_player_detail`（查单名球员详情）
* **入参**：`player_id: int | None = None`、`player_name: str | None = None`（二选一）
* **确定性行为**：精确匹配 `player_id`，或按 `name`/`full_name` 模糊匹配；返回该球员基础身价、状态与新闻、`form`、`ppg`、`minutes`、`defensive_contribution`、`expected_pts` 以及**未来 5 轮赛程（`next_5_fdr`）**。
* **错误返回（供 AI 自修正）**：
  * 未传任何参数 → `请提供 player_id 或 player_name。`
  * 模糊匹配到多个重名球员 → `匹配到多个球员：[{id}: {name}({team}), ...]，请指定准确的 player_id。`
  * 找不到球员 → `未找到匹配 "{query}" 的球员。`

### 工具 3：`simulate_and_validate_move`（核心硬规则护栏）
* **入参**：
  * `action_type`: `"hold" | "lineup_only" | "transfer_1" | "transfer_2"`
  * `out_ids`: `list[int] = []`
  * `in_ids`: `list[int] = []`
* **确定性硬校验（按顺序检查，全部通过才返回 `valid=True`）**：
  1. **动作与人数匹配**：`hold`/`lineup_only` 必须 `out_ids=[]` 且 `in_ids=[]`；`transfer_1` 必须各 1 人；`transfer_2` 必须各 2 人；且不能同批卖出/买入同一名球员。
  2. **阵容归属**：`out_ids` 必须全在当前 15 人内且无重复；`in_ids` 必须全在 `FPLUniverse` 内且当前不在队内（也不重复）。
  3. **位置配额（2/5/5/3）**：转会后 15 人各位置人数必须仍严格为 `2 GKP, 5 DEF, 5 MID, 3 FWD`。
  4. **同队 ≤ 3 人**：转会后同一英超球队球员数不得超过 3 人。
  5. **预算校对（整数 0.1m 单位防浮点误差）**：`sum(in_now_cost) <= bank + sum(out_selling_price)`。
  6. **FT 与 -4 罚分**：`hit_count = max(0, len(out_ids) - free_transfers)`，`hit_points = hit_count * 4`，`remaining_ft = max(0, free_transfers - len(out_ids))`。
* **返回结果**：`valid`、新 15 人 ID、精确财务账（`new_bank`、`remaining_ft`、`hit_points`）、新阵容最优首发（含队长×2）参考分及相对“不买人最佳排阵”的净参考收益。
* **错误返回（供 AI 自修正）**：
  * `校验失败：超预算 £0.3m（可用 £10.2m，目标总价 £10.5m）。`
  * `校验失败：转会后 ARS 球员将达 4 人，超过同队最多 3 人限制。`
  * `校验失败：位置配额不匹配（卖出 MID，不能买入 FWD）。`

### 工具 4：`suggest_best_lineup`（确定性排最优首发与队长）
* **入参**：`squad_15_ids: list[int]`、`exclude_player_ids: list[int] = []`（可选，供 Agent 传入突发新闻伤停屏蔽）
* **确定性行为**：校验传入恰好 15 个不重复球员且满足 2/5/5/3；按 `expected_pts`（被 `exclude_player_ids` 屏蔽的球员暂记 `0.0`）选出合法 11 人首发（`1 GKP` + `>=3 DEF` + `>=1 FWD` 下的总分最高组合）、替补顺位（1 替补门将 + 3 名按分降序的外场替补）、以及正副队长（首发中预期分最高 2 人，并在总分中计入队长 × 2）。
* **错误返回**：人数不为 15 或位置配额不满足时返回具体缺失提示。

### 工具 5：`evaluate_hold_baseline`（评估“不动 / 仅调阵”基线）
* **入参**：无（直接读取当前 `TeamSnapshot` 与 `FPLUniverse`）
* **确定性行为**：
  1. 计算当前已锁定首发与队长的 **原封不动参考分（`as_is_score`）**；
  2. 调用 `suggest_best_lineup` 计算不买人下的 **最佳首发 + 最佳队长（含队长×2）参考分（`best_no_transfer_score`）** 与调阵提升差值；
  3. 返回当前 `free_transfers`、保留 1 个 FT 的期权参考分（`1.5` 或当 FT=5 时为 `0.0`）以及阵容痛点摘要（如“首发中有伤停/低概率球员”、“队长非最高参考分球员”），确保“不动”与“仅调阵”作为一等公民参与比较。

---

## 4. 验收标准（Acceptance Criteria）

交付本模块时，必须通过 `pytest tests/test_tools.py` 覆盖以下三类用例：
1. **正常 Case**：
   * `search_transfer_candidates` 正确排除队内已有球员和伤停球员，按价格上限返回 Top 10；
   * `simulate_and_validate_move` 正确校验 `hold`、`transfer_1`（如用 £7.9m 余额 + £4.4m 卖出 `Iroegbunam` 买 £11.0m `Palmer`，剩 `£1.3m`、`remaining_ft = 2`、`hit_points = 0`）；
   * `suggest_best_lineup` 严格满足 `1 GKP + >=3 DEF + >=1 FWD` 并选出正副队长；
   * `evaluate_hold_baseline` 在 1 FT 与 5 FT 边界下返回正确的 FT 期权参考分。
2. **边界 Case**：
   * 当前只有 `1 FT` 时测试 `transfer_2` → `valid=True` 但精确标出 `hit_points = 4, remaining_ft = 0`；
   * 转会时先卖掉某队第 3 人、再买回同队另一人 → 总数仍为 3 人，应允许通过；
   * `get_player_detail` 模糊匹配重名时返回列表提示。
3. **失败 Case（规则拦截与自修正反馈）**：
   * 超预算（哪怕仅差 `£0.1m`）→ `valid=False` + 明确差额报错；
   * 同队超过 3 人（如已有 3 名 ARS 再买第 4 名 ARS）→ `valid=False` + 明确球队名报错；
   * 跨位置买卖（卖 `MID` 买 `FWD`）→ `valid=False` + 明确位置报错；
   * 传入不足 15 人排首发 / 预算过低找不到候选 → 返回明确错误提示。
