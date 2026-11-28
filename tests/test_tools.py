"""Unit & rule-interception tests for Task 2 (backend.tools)."""

from __future__ import annotations

from backend.fpl_client import load_demo_snapshot
from backend.models import UniversePlayer
from backend.tools import FPLToolbox, OPENAI_TOOLS_SCHEMA


def _make_toolbox() -> FPLToolbox:
    bundle = load_demo_snapshot()
    return FPLToolbox(bundle)


# ---------------------------------------------------------------------------
# 1. Normal cases
# ---------------------------------------------------------------------------


def test_search_transfer_candidates_filters_squad_and_injured() -> None:
    tb = _make_toolbox()
    res = tb.search_transfer_candidates(position="MID", max_price=12.0)
    assert res["ok"] is True
    ids = [c["id"] for c in res["candidates"]]
    # Palmer (16, 11.0m) and Gordon (18, 7.2m) should appear; Saka (8) is already in squad; Iroegbunam (12) is injured & in squad
    assert 16 in ids
    assert 18 in ids
    assert 8 not in ids
    assert 12 not in ids


def test_get_player_detail_by_id_and_name() -> None:
    tb = _make_toolbox()
    res = tb.get_player_detail(player_name="Palmer")
    assert res["ok"] is True
    assert res["player"]["id"] == 16
    assert len(res["player"]["next_5_fdr"]) >= 3
    assert "DefCon" in res["player"]["note"]


def test_evaluate_hold_baseline_compares_as_is_vs_best_lineup() -> None:
    tb = _make_toolbox()
    res = tb.evaluate_hold_baseline()
    assert res["ok"] is True
    assert res["free_transfers"] == 3
    assert res["ft_threshold_pts"] == 2.0
    # Best no-transfer score should be >= as-is score
    assert res["best_no_transfer_score"] >= res["as_is_score"]
    assert res["recommended_no_transfer_lineup"]["captain"]["id"] == 9  # Salah highest xPts
    assert any("Iroegbunam" in note for note in res["pain_points"])


def test_simulate_transfer_1_valid_palmer_for_iroegbunam() -> None:
    tb = _make_toolbox()
    # Demo squad: bank=7.9, Iroegbunam(12) selling_price=4.4 -> available=12.3
    # Palmer(16) now_cost=11.0 -> new_bank=1.3, free_transfers=3 -> remaining_ft=2, hit_points=0
    res = tb.simulate_and_validate_move("transfer_1", out_ids=[12], in_ids=[16])
    assert res["valid"] is True
    assert res["new_bank"] == 1.3
    assert res["remaining_ft"] == 2
    assert res["hit_points"] == 0
    assert res["net_pts_gain"] > 0


def test_suggest_best_lineup_valid_formation_and_exclude() -> None:
    tb = _make_toolbox()
    squad_ids = [p.id for p in tb.snapshot.players]
    res = tb.suggest_best_lineup(squad_ids, exclude_player_ids=[9])  # Exclude Salah via news
    assert res["ok"] is True
    assert res["captain"]["id"] != 9
    assert len(res["starters"]) == 11
    assert len(res["bench"]) == 4


# ---------------------------------------------------------------------------
# 2. Boundary cases
# ---------------------------------------------------------------------------


def test_ft_5_threshold_is_zero_and_hit_points_calculated() -> None:
    bundle = load_demo_snapshot()
    bundle.snapshot.free_transfers = 5
    tb_5 = FPLToolbox(bundle)
    assert tb_5.evaluate_hold_baseline()["ft_threshold_pts"] == 0.0

    # With 1 FT, transfer_2 should incur 4 hit points
    bundle.snapshot.free_transfers = 1
    tb_1 = FPLToolbox(bundle)
    # Sell Iroegbunam (12, MID, 4.4) + Wood (14, FWD, 6.5) -> available = 7.9 + 4.4 + 6.5 = 18.8
    # Buy Gordon (18, MID, 7.2) + Isak (17, FWD, 8.5) -> cost = 15.7 -> valid, hit=4, rem_ft=0
    res = tb_1.simulate_and_validate_move("transfer_2", out_ids=[12, 14], in_ids=[18, 17])
    assert res["valid"] is True
    assert res["hit_points"] == 4
    assert res["remaining_ft"] == 0


def test_swap_within_maxed_out_club_allowed() -> None:
    tb = _make_toolbox()
    # Demo squad already has 3 ARS players: Raya(1), Gabriel(3), Saka(8).
    # Selling Gabriel(3, DEF, 6.3) and buying Saliba(19, DEF, 6.0) keeps ARS at 3 -> valid!
    res = tb.simulate_and_validate_move("transfer_1", out_ids=[3], in_ids=[19])
    assert res["valid"] is True
    # Note: upgrading/downgrading healthy Gabriel to Saliba should have negative or small gain -> advice_note triggered
    assert res["advice_note"] != ""


def test_get_player_detail_ambiguous_name() -> None:
    bundle = load_demo_snapshot()
    # Add a second "Gordon" to test ambiguity
    bundle.universe.players[999] = UniversePlayer(
        id=999,
        name="Gordon",
        full_name="Kaide Gordon",
        team_id=6,
        team="LIV",
        pos="MID",
        now_cost=4.5,
        status="a",
    )
    tb = FPLToolbox(bundle)
    res = tb.get_player_detail(player_name="Gordon")
    assert res["ok"] is False
    assert "匹配到多个球员" in res["error"]


# ---------------------------------------------------------------------------
# 3. Failure / rule-interception cases
# ---------------------------------------------------------------------------


def test_simulate_blocks_over_budget() -> None:
    bundle = load_demo_snapshot()
    bundle.snapshot.bank = 0.0  # Total available selling Iroegbunam = 4.4
    tb = FPLToolbox(bundle)
    res = tb.simulate_and_validate_move("transfer_1", out_ids=[12], in_ids=[16])  # Palmer 11.0
    assert res["valid"] is False
    assert "超预算" in res["error"]


def test_simulate_blocks_4th_player_from_same_club() -> None:
    tb = _make_toolbox()
    # Already 3 ARS (Raya, Gabriel, Saka). Selling Gvardiol (MCI, DEF) and buying Saliba (ARS, DEF) -> 4 ARS
    res = tb.simulate_and_validate_move("transfer_1", out_ids=[5], in_ids=[19])
    assert res["valid"] is False
    assert "ARS" in res["error"]
    assert "3 人" in res["error"]


def test_simulate_blocks_wrong_position_swap() -> None:
    tb = _make_toolbox()
    # Sell MID Iroegbunam(12), buy FWD Isak(17)
    res = tb.simulate_and_validate_move("transfer_1", out_ids=[12], in_ids=[17])
    assert res["valid"] is False
    assert "位置配额" in res["error"]


def test_search_candidates_low_budget_and_schema_present() -> None:
    tb = _make_toolbox()
    res = tb.search_transfer_candidates(position="FWD", max_price=3.0)
    assert res["ok"] is False
    assert "无可用健康候选人" in res["error"]
    assert len(OPENAI_TOOLS_SCHEMA) == 5
