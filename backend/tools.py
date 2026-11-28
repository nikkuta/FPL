"""5 deterministic tools & hard FPL rules validator (Task 2)."""

from __future__ import annotations

import itertools
from collections import Counter
from typing import Any, Literal

from backend.models import Position, SnapshotBundle, UniversePlayer

FDR_MULTIPLIER: dict[int, float] = {
    1: 1.15,
    2: 1.08,
    3: 1.00,
    4: 0.92,
    5: 0.85,
}

REQUIRED_POS_COUNTS: dict[Position, int] = {
    "GKP": 2,
    "DEF": 5,
    "MID": 5,
    "FWD": 3,
}

FT_THRESHOLD_DEFAULT = 2.0  # Aligned with FPL Review standard FT Value (~2.0 pts)


def _to_tenths(amount_m: float) -> int:
    return int(round(amount_m * 10))


def _from_tenths(tenths: int) -> float:
    return round(tenths / 10.0, 1)


def compute_expected_pts(
    player: UniversePlayer,
    bundle: SnapshotBundle,
    *,
    excluded: bool = False,
) -> float:
    """Transparent deterministic reference expected points for the next GW.

    Note: `form` and `ppg` from the official API already include historical
    DefCon (+2) points, so no extra DefCon bonus is added here.
    """
    if excluded:
        return 0.0

    base = 0.6 * player.form + 0.4 * player.ppg
    if base <= 0.0:
        return 0.0

    if player.status == "a":
        avail = 1.0
    elif player.status == "d":
        chance = player.chance_of_playing if player.chance_of_playing is not None else 50
        avail = max(0.0, min(1.0, chance / 100.0))
    else:
        avail = 0.0

    if avail == 0.0:
        return 0.0

    next_gw = bundle.universe.next_gw
    team_fixtures = [
        f
        for f in bundle.universe.fixtures
        if f.event == next_gw and (f.team_h == player.team_id or f.team_a == player.team_id)
    ]

    if not team_fixtures:
        return 0.0

    fdr_mult = 0.0
    for f in team_fixtures:
        diff = f.team_h_difficulty if f.team_h == player.team_id else f.team_a_difficulty
        fdr_mult += FDR_MULTIPLIER.get(diff, 1.0)

    return round(base * avail * fdr_mult, 2)


class FPLToolbox:
    """Deterministic toolbox exposed to the Agent and unit tests."""

    def __init__(self, bundle: SnapshotBundle) -> None:
        self.bundle = bundle
        self.snapshot = bundle.snapshot
        self.universe = bundle.universe
        self._squad_by_id = {p.id: p for p in self.snapshot.players}
        self._xpts_cache: dict[int, float] = {
            pid: compute_expected_pts(p, bundle) for pid, p in self.universe.players.items()
        }

    def expected_pts(self, player_id: int, excluded: bool = False) -> float:
        if excluded:
            return 0.0
        return self._xpts_cache.get(player_id, 0.0)

    # ------------------------------------------------------------------
    # Tool 1: search_transfer_candidates
    # ------------------------------------------------------------------
    def search_transfer_candidates(
        self,
        position: str,
        max_price: float,
        exclude_teams: list[str] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        pos_upper = position.strip().upper()
        if pos_upper not in REQUIRED_POS_COUNTS:
            return {
                "ok": False,
                "error": "参数错误：position 必须是 GKP/DEF/MID/FWD 之一。",
            }

        excluded_set = {t.strip().upper() for t in (exclude_teams or []) if t.strip()}
        max_tenths = _to_tenths(max_price)
        current_ids = set(self._squad_by_id.keys())

        candidates: list[dict[str, Any]] = []
        for pid, p in self.universe.players.items():
            if pid in current_ids:
                continue
            if p.pos != pos_upper:
                continue
            if p.status in ("i", "s", "u", "n"):
                continue
            if p.team.upper() in excluded_set:
                continue
            if _to_tenths(p.now_cost) > max_tenths:
                continue

            xpts = self.expected_pts(pid)
            candidates.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "team": p.team,
                    "pos": p.pos,
                    "now_cost": p.now_cost,
                    "status": p.status,
                    "chance_of_playing": p.chance_of_playing,
                    "form": p.form,
                    "ppg": p.ppg,
                    "next_3_fdr": p.next_5_fdr[:3],
                    "expected_pts": xpts,
                }
            )

        if not candidates:
            return {
                "ok": False,
                "error": (
                    f"当前预算 £{max_price:.1f}m 在 {pos_upper} 位置无可用健康候选人，"
                    "请提高预算或先卖出高价球员腾出资金。"
                ),
            }

        candidates.sort(key=lambda c: (-c["expected_pts"], -c["form"], c["now_cost"], c["id"]))
        return {
            "ok": True,
            "position": pos_upper,
            "max_price": round(max_price, 1),
            "count": min(limit, len(candidates)),
            "candidates": candidates[:limit],
        }

    # ------------------------------------------------------------------
    # Tool 2: get_player_detail
    # ------------------------------------------------------------------
    def get_player_detail(
        self,
        player_id: int | None = None,
        player_name: str | None = None,
    ) -> dict[str, Any]:
        if player_id is None and (player_name is None or not player_name.strip()):
            return {"ok": False, "error": "请提供 player_id 或 player_name。"}

        matches: list[UniversePlayer] = []
        if player_id is not None:
            p = self.universe.players.get(player_id)
            if p is not None:
                matches = [p]
            query_label = str(player_id)
        else:
            q = player_name.strip().lower()
            query_label = player_name.strip()
            exact = [
                p
                for p in self.universe.players.values()
                if p.name.lower() == q or p.full_name.lower() == q
            ]
            if exact:
                matches = exact
            else:
                matches = [
                    p
                    for p in self.universe.players.values()
                    if q in p.name.lower() or q in p.full_name.lower()
                ]

        if not matches:
            return {"ok": False, "error": f'未找到匹配 "{query_label}" 的球员。'}

        if len(matches) > 1:
            preview = ", ".join(f"{p.id}: {p.name}({p.team})" for p in matches[:8])
            return {
                "ok": False,
                "error": f"匹配到多个球员：[{preview}]，请指定准确的 player_id。",
            }

        p = matches[0]
        return {
            "ok": True,
            "player": {
                "id": p.id,
                "name": p.name,
                "full_name": p.full_name,
                "team": p.team,
                "pos": p.pos,
                "now_cost": p.now_cost,
                "status": p.status,
                "chance_of_playing": p.chance_of_playing,
                "news": p.news,
                "form": p.form,
                "ppg": p.ppg,
                "total_points": p.total_points,
                "minutes": p.minutes,
                "selected_by_percent": p.selected_by_percent,
                "defensive_contribution": p.defensive_contribution,
                "next_5_fdr": p.next_5_fdr,
                "expected_pts": self.expected_pts(p.id),
                "note": "官方 form 与 ppg 已包含历史 DefCon(+2) 得分，defensive_contribution 仅作未来保底能力参考，切勿重复加分。",
            },
        }

    # ------------------------------------------------------------------
    # Tool 4 (placed before 3/5 as internal helper too): suggest_best_lineup
    # ------------------------------------------------------------------
    def suggest_best_lineup(
        self,
        squad_15_ids: list[int],
        exclude_player_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        if len(squad_15_ids) != 15 or len(set(squad_15_ids)) != 15:
            return {
                "ok": False,
                "error": f"排阵失败：必须传入恰好 15 名不重复球员，当前收到 {len(squad_15_ids)} 人。",
            }

        players: list[UniversePlayer] = []
        for pid in squad_15_ids:
            p = self.universe.players.get(pid)
            if p is None:
                return {"ok": False, "error": f"排阵失败：球员 ID {pid} 不存在。"}
            players.append(p)

        pos_counts = Counter(p.pos for p in players)
        for pos, req in REQUIRED_POS_COUNTS.items():
            if pos_counts[pos] != req:
                return {
                    "ok": False,
                    "error": f"排阵失败：位置配额不合法，{pos} 应为 {req} 人，当前为 {pos_counts[pos]} 人。",
                }

        excluded_set = set(exclude_player_ids or [])
        pts_map = {p.id: self.expected_pts(p.id, excluded=(p.id in excluded_set)) for p in players}

        gkps = sorted([p for p in players if p.pos == "GKP"], key=lambda p: (-pts_map[p.id], p.id))
        starter_gkp = gkps[0]
        bench_gkp = gkps[1]

        outfield = [p for p in players if p.pos != "GKP"]
        best_combo: tuple[UniversePlayer, ...] | None = None
        best_outfield_sum = -1.0

        for combo in itertools.combinations(outfield, 10):
            defs = sum(1 for p in combo if p.pos == "DEF")
            fwds = sum(1 for p in combo if p.pos == "FWD")
            if defs < 3 or fwds < 1:
                continue
            score = sum(pts_map[p.id] for p in combo)
            if best_combo is None or score > best_outfield_sum:
                best_outfield_sum = score
                best_combo = combo

        assert best_combo is not None
        starters = sorted([starter_gkp, *best_combo], key=lambda p: (-pts_map[p.id], p.id))
        starter_ids = {p.id for p in starters}
        bench_outfield = sorted(
            [p for p in outfield if p.id not in starter_ids],
            key=lambda p: (-pts_map[p.id], p.id),
        )
        bench = [bench_gkp, *bench_outfield]

        captain = starters[0]
        vice_captain = starters[1]
        total_with_captain = round(sum(pts_map[p.id] for p in starters) + pts_map[captain.id], 2)

        return {
            "ok": True,
            "formation": (
                f"{sum(1 for p in starters if p.pos == 'DEF')}-"
                f"{sum(1 for p in starters if p.pos == 'MID')}-"
                f"{sum(1 for p in starters if p.pos == 'FWD')}"
            ),
            "expected_pts_with_captain": total_with_captain,
            "captain": {"id": captain.id, "name": captain.name, "expected_pts": pts_map[captain.id]},
            "vice_captain": {
                "id": vice_captain.id,
                "name": vice_captain.name,
                "expected_pts": pts_map[vice_captain.id],
            },
            "starters": [
                {"id": p.id, "name": p.name, "pos": p.pos, "team": p.team, "expected_pts": pts_map[p.id]}
                for p in starters
            ],
            "bench": [
                {"id": p.id, "name": p.name, "pos": p.pos, "team": p.team, "expected_pts": pts_map[p.id]}
                for p in bench
            ],
        }

    # ------------------------------------------------------------------
    # Tool 5: evaluate_hold_baseline
    # ------------------------------------------------------------------
    def evaluate_hold_baseline(self) -> dict[str, Any]:
        squad_ids = [p.id for p in self.snapshot.players]
        best_lineup = self.suggest_best_lineup(squad_ids)
        assert best_lineup["ok"] is True

        # As-is score using locked starters and locked captain
        as_is_starters = [p for p in self.snapshot.players if p.is_starter]
        as_is_captain = next((p for p in self.snapshot.players if p.is_captain), None)
        as_is_sum = sum(self.expected_pts(p.id) for p in as_is_starters)
        if as_is_captain is not None and as_is_captain.is_starter:
            as_is_sum += self.expected_pts(as_is_captain.id)
        as_is_score = round(as_is_sum, 2)

        best_no_transfer_score = float(best_lineup["expected_pts_with_captain"])
        lineup_gain = round(best_no_transfer_score - as_is_score, 2)

        pain_points: list[str] = []
        for p in self.snapshot.players:
            if p.status in ("i", "s", "u", "n"):
                role = "首发" if p.is_starter else "替补"
                pain_points.append(f"{role}球员 {p.name}({p.team}) 状态异常({p.status})：{p.news or '不可用'}")
            elif p.status == "d":
                pain_points.append(
                    f"{'首发' if p.is_starter else '替补'}球员 {p.name}({p.team}) 出场存疑({p.chance_of_playing}%)"
                )
        if lineup_gain >= 0.5:
            pain_points.append(
                f"仅调整首发/队长（不花转会）即可提升 +{lineup_gain:.2f} 参考分（建议队长：{best_lineup['captain']['name']}）。"
            )

        ft = self.snapshot.free_transfers
        ft_threshold = FT_THRESHOLD_DEFAULT if ft < 5 else 0.0

        return {
            "ok": True,
            "free_transfers": ft,
            "ft_threshold_pts": ft_threshold,
            "as_is_score": as_is_score,
            "best_no_transfer_score": best_no_transfer_score,
            "lineup_only_gain": lineup_gain,
            "recommended_no_transfer_lineup": best_lineup,
            "pain_points": pain_points,
        }

    # ------------------------------------------------------------------
    # Tool 3: simulate_and_validate_move
    # ------------------------------------------------------------------
    def simulate_and_validate_move(
        self,
        action_type: Literal["hold", "lineup_only", "transfer_1", "transfer_2"],
        out_ids: list[int] | None = None,
        in_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        out_list = list(out_ids or [])
        in_list = list(in_ids or [])

        if action_type in ("hold", "lineup_only"):
            if out_list or in_list:
                return {
                    "valid": False,
                    "error": f"校验失败：{action_type} 动作不允许传入 out_ids 或 in_ids。",
                }
        elif action_type == "transfer_1":
            if len(out_list) != 1 or len(in_list) != 1:
                return {
                    "valid": False,
                    "error": "校验失败：transfer_1 必须恰好指定 1 名转出和 1 名转入球员。",
                }
        elif action_type == "transfer_2":
            if len(out_list) != 2 or len(in_list) != 2:
                return {
                    "valid": False,
                    "error": "校验失败：transfer_2 必须恰好指定 2 名转出和 2 名转入球员。",
                }
        else:
            return {
                "valid": False,
                "error": f"校验失败：不支持的 action_type '{action_type}'。",
            }

        if len(set(out_list)) != len(out_list) or len(set(in_list)) != len(in_list):
            return {"valid": False, "error": "校验失败：转出或转入列表中存在重复球员。"}

        if set(out_list) & set(in_list):
            return {"valid": False, "error": "校验失败：不能在同一轮卖出并买回同一名球员。"}

        for pid in out_list:
            if pid not in self._squad_by_id:
                return {"valid": False, "error": f"校验失败：转出球员 ID {pid} 不在当前 15 人阵容中。"}

        for pid in in_list:
            if pid not in self.universe.players:
                return {"valid": False, "error": f"校验失败：转入球员 ID {pid} 不存在。"}
            if pid in self._squad_by_id:
                name = self._squad_by_id[pid].name
                return {"valid": False, "error": f"校验失败：{name} 已在当前阵容中，不能重复买入。"}

        # Build new 15-player IDs
        out_set = set(out_list)
        new_squad_ids = [p.id for p in self.snapshot.players if p.id not in out_set] + in_list

        # Position quota check (2/5/5/3)
        pos_counts = Counter(self.universe.players[pid].pos for pid in new_squad_ids)
        for pos, req in REQUIRED_POS_COUNTS.items():
            if pos_counts[pos] != req:
                out_pos = [self._squad_by_id[pid].pos for pid in out_list]
                in_pos = [self.universe.players[pid].pos for pid in in_list]
                return {
                    "valid": False,
                    "error": f"校验失败：位置配额不匹配（卖出 {out_pos}，不能买入 {in_pos}）。",
                }

        # Max 3 players per club check
        team_counts = Counter(self.universe.players[pid].team for pid in new_squad_ids)
        for team_name, cnt in team_counts.items():
            if cnt > 3:
                return {
                    "valid": False,
                    "error": f"校验失败：转会后 {team_name} 球员将达 {cnt} 人，超过同队最多 3 人限制。",
                }

        # Budget check in integer 0.1m tenths
        bank_tenths = _to_tenths(self.snapshot.bank)
        selling_tenths = sum(_to_tenths(self._squad_by_id[pid].selling_price) for pid in out_list)
        available_tenths = bank_tenths + selling_tenths
        in_cost_tenths = sum(_to_tenths(self.universe.players[pid].now_cost) for pid in in_list)

        if in_cost_tenths > available_tenths:
            diff = _from_tenths(in_cost_tenths - available_tenths)
            return {
                "valid": False,
                "error": (
                    f"校验失败：超预算 £{diff:.1f}m"
                    f"（可用 £{_from_tenths(available_tenths):.1f}m，目标总价 £{_from_tenths(in_cost_tenths):.1f}m）。"
                ),
            }

        new_bank = _from_tenths(available_tenths - in_cost_tenths)
        transfers_count = len(out_list)
        ft = self.snapshot.free_transfers
        hit_count = max(0, transfers_count - ft)
        hit_points = hit_count * 4
        remaining_ft = max(0, ft - transfers_count)

        best_lineup = self.suggest_best_lineup(new_squad_ids)
        assert best_lineup["ok"] is True

        baseline = self.evaluate_hold_baseline()
        base_score = float(baseline["best_no_transfer_score"])
        new_score = float(best_lineup["expected_pts_with_captain"])
        net_pts_gain = round((new_score - hit_points) - base_score, 2)

        ft_threshold = FT_THRESHOLD_DEFAULT if ft < 5 else 0.0
        advice_note = ""
        if transfers_count > 0 and net_pts_gain < ft_threshold:
            advice_note = (
                f"提示：纯净净收益 {net_pts_gain:+.2f} 分低于参考门槛 {ft_threshold:.1f} 分，"
                "除非清理伤员或应对赛程拐点，否则可优先考虑保留 FT 不动。"
            )

        return {
            "valid": True,
            "action_type": action_type,
            "out_players": [
                {
                    "id": pid,
                    "name": self._squad_by_id[pid].name,
                    "selling_price": self._squad_by_id[pid].selling_price,
                }
                for pid in out_list
            ],
            "in_players": [
                {
                    "id": pid,
                    "name": self.universe.players[pid].name,
                    "now_cost": self.universe.players[pid].now_cost,
                }
                for pid in in_list
            ],
            "new_bank": new_bank,
            "total_selling_value": _from_tenths(selling_tenths),
            "total_in_cost": _from_tenths(in_cost_tenths),
            "remaining_ft": remaining_ft,
            "hit_points": hit_points,
            "new_best_score": new_score,
            "baseline_no_transfer_score": base_score,
            "net_pts_gain": net_pts_gain,
            "ft_threshold_pts": ft_threshold,
            "advice_note": advice_note,
            "lineup": best_lineup,
        }

    # ------------------------------------------------------------------
    # Dispatcher for OpenAI Tool-Calling loop (Task 3)
    # ------------------------------------------------------------------
    def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "search_transfer_candidates":
            return self.search_transfer_candidates(**arguments)
        if tool_name == "get_player_detail":
            return self.get_player_detail(**arguments)
        if tool_name == "simulate_and_validate_move":
            return self.simulate_and_validate_move(**arguments)
        if tool_name == "suggest_best_lineup":
            return self.suggest_best_lineup(**arguments)
        if tool_name == "evaluate_hold_baseline":
            return self.evaluate_hold_baseline()
        return {"ok": False, "error": f"未知工具：{tool_name}"}


OPENAI_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "evaluate_hold_baseline",
            "description": "评估本周不买人（原封不动 vs 仅调首发/队长）的客观参考得分、FT 出手门槛及当前阵容痛点。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_transfer_candidates",
            "description": "按位置与最高预算从全英超健康球员池中筛选 Top 10 转会候选人（自动排除已在队及伤停球员）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "position": {"type": "string", "enum": ["GKP", "DEF", "MID", "FWD"]},
                    "max_price": {"type": "number", "description": "最高可承受买入价（£m）"},
                    "exclude_teams": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "需排除的球队缩写列表，如 ['ARS']",
                    },
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["position", "max_price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_player_detail",
            "description": "查询单名球员详细状态、出场分钟、DefCon 防守贡献及未来 5 轮赛程难度。注意：form 和 ppg 已包含历史 DefCon 得分，切勿重复加分。",
            "parameters": {
                "type": "object",
                "properties": {
                    "player_id": {"type": "integer"},
                    "player_name": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_and_validate_move",
            "description": "核心硬规则校验器：校验转会或不动/调阵是否满足预算、2/5/5/3配额、同队<=3人，并计算 FT、-4 罚分与纯净分差。推荐任何方案前必须调用并获得 valid=true。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action_type": {
                        "type": "string",
                        "enum": ["hold", "lineup_only", "transfer_1", "transfer_2"],
                    },
                    "out_ids": {"type": "array", "items": {"type": "integer"}, "default": []},
                    "in_ids": {"type": "array", "items": {"type": "integer"}, "default": []},
                },
                "required": ["action_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_best_lineup",
            "description": "为指定 15 人阵容排出合法最优 11 人首发、替补顺位与正副队长（可传入突发新闻伤停需屏蔽的球员 ID）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "squad_15_ids": {"type": "array", "items": {"type": "integer"}},
                    "exclude_player_ids": {"type": "array", "items": {"type": "integer"}, "default": []},
                },
                "required": ["squad_15_ids"],
            },
        },
    },
]
