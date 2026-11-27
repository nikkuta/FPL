"""Unit & fault-injection tests for Task 1 (backend.fpl_client & backend.models)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from backend.fpl_client import (
    FPLDataError,
    FPLInvalidInputError,
    FPLNotFoundError,
    FPLUpstreamError,
    build_snapshot_bundle,
    calculate_free_transfers,
    calculate_selling_price,
    extract_latest_buy_prices,
    fetch_team_snapshot,
    load_demo_snapshot,
    parse_team_id,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_json(name: str):
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Normal cases
# ---------------------------------------------------------------------------


def test_parse_team_id_valid_formats() -> None:
    assert parse_team_id(123456) == 123456
    assert parse_team_id(" 123456 ") == 123456
    assert parse_team_id("https://fantasy.premierleague.com/entry/654321/event/12") == 654321
    assert parse_team_id("fantasy.premierleague.com/entry/42") == 42


def test_load_demo_snapshot_selling_prices_and_fields() -> None:
    bundle = load_demo_snapshot()
    snap = bundle.snapshot

    assert snap.team_id == 999001
    assert snap.team_name == "Demo United"
    assert snap.gw == 12
    assert snap.picks_gw == 11
    assert snap.bank == 7.9
    assert snap.free_transfers == 3
    assert snap.ft_is_calibrated is False
    assert len(snap.players) == 15
    assert snap.available_chips == ["wildcard", "freehit", "3xc"]

    by_id = {p.id: p for p in snap.players}

    # Saka: bought at 10.0, now 10.3 -> selling price 10.1 (exact)
    saka = by_id[8]
    assert saka.now_cost == 10.3
    assert saka.selling_price == 10.1
    assert saka.price_is_estimated is False
    assert len(saka.next_3_fdr) == 3

    # Mbeumo: bought at 8.0, now 7.8 -> selling price 7.8 (exact)
    mbeumo = by_id[10]
    assert mbeumo.selling_price == 7.8
    assert mbeumo.price_is_estimated is False

    # Salah: initial veteran (no transfer record), Option A -> 12.9, estimated=True
    salah = by_id[9]
    assert salah.now_cost == 12.9
    assert salah.selling_price == 12.9
    assert salah.price_is_estimated is True
    assert salah.is_captain is True
    assert salah.is_starter is True

    # Injured bench player
    iro = by_id[12]
    assert iro.status == "i"
    assert iro.is_starter is False
    assert iro.bench_order == 3


def test_fetch_team_snapshot_with_mock_transport() -> None:
    bootstrap = _load_json("demo_bootstrap.json")
    fixtures = _load_json("demo_fixtures.json")
    entry = _load_json("demo_entry.json")
    history = _load_json("demo_history.json")
    transfers = _load_json("demo_transfers.json")
    picks = _load_json("demo_picks.json")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/bootstrap-static/"):
            return httpx.Response(200, json=bootstrap)
        if path.endswith("/fixtures/"):
            return httpx.Response(200, json=fixtures)
        if path.endswith("/entry/999001/"):
            return httpx.Response(200, json=entry)
        if path.endswith("/entry/999001/history/"):
            return httpx.Response(200, json=history)
        if path.endswith("/entry/999001/transfers/"):
            return httpx.Response(200, json=transfers)
        if path.endswith("/entry/999001/event/11/picks/"):
            return httpx.Response(200, json=picks)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    bundle = asyncio.run(
        fetch_team_snapshot("https://fantasy.premierleague.com/entry/999001/event/11", transport=transport)
    )
    assert bundle.snapshot.team_id == 999001
    assert len(bundle.universe.players) == 19


# ---------------------------------------------------------------------------
# 2. Boundary cases
# ---------------------------------------------------------------------------


def test_ft_started_event_late_join() -> None:
    # Joined in GW5, 0 transfers in GW6 -> entering GW7 should have 2 FT (not from GW1)
    history_current = [
        {"event": 5, "event_transfers": 0},
        {"event": 6, "event_transfers": 0},
    ]
    assert calculate_free_transfers(5, history_current, [], target_gw=7) == 2
    assert calculate_free_transfers(5, history_current[:1], [], target_gw=6) == 1


def test_ft_capped_at_5() -> None:
    history_current = [{"event": gw, "event_transfers": 0} for gw in range(1, 10)]
    assert calculate_free_transfers(1, history_current, [], target_gw=10) == 5


def test_ft_preserved_on_wildcard_and_freehit() -> None:
    # GW1 start -> GW2 (0 transfers -> 2 FT) -> GW3 Wildcard (still 2 FT) -> GW4 Free Hit (still 2 FT)
    history_current = [
        {"event": 1, "event_transfers": 0},
        {"event": 2, "event_transfers": 0},
        {"event": 3, "event_transfers": 5},
        {"event": 4, "event_transfers": 3},
    ]
    chips = [
        {"event": 3, "name": "wildcard"},
        {"event": 4, "name": "freehit"},
    ]
    assert calculate_free_transfers(1, history_current, chips, target_gw=5) == 2


def test_extract_latest_buy_price_and_floor_rounding() -> None:
    transfers = [
        {"element_in": 8, "element_in_cost": 98, "event": 2, "time": "2026-08-25T10:00:00Z"},
        {"element_in": 8, "element_in_cost": 100, "event": 6, "time": "2026-09-28T10:00:00Z"},
    ]
    prices = extract_latest_buy_prices(transfers)
    assert prices[8] == 100
    sell, est = calculate_selling_price(105, prices[8])
    assert sell == 10.2  # 100 + floor(5/2)=2 -> 102 -> 10.2
    assert est is False


# ---------------------------------------------------------------------------
# 3. Failure / fault-injection cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_input", ["", "   ", "abc", "0", "-10", 0, -1, True, "https://fantasy.premierleague.com/leagues"])
def test_parse_team_id_invalid(bad_input) -> None:
    with pytest.raises(FPLInvalidInputError):
        parse_team_id(bad_input)


def test_fetch_team_snapshot_404_never_falls_back_to_demo() -> None:
    bootstrap = _load_json("demo_bootstrap.json")
    fixtures = _load_json("demo_fixtures.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bootstrap-static/"):
            return httpx.Response(200, json=bootstrap)
        if request.url.path.endswith("/fixtures/"):
            return httpx.Response(200, json=fixtures)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with pytest.raises(FPLNotFoundError) as exc_info:
        asyncio.run(fetch_team_snapshot("888888", transport=transport))
    assert "888888" in exc_info.value.message


def test_fetch_team_snapshot_503_and_timeout() -> None:
    def handler_503(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(FPLUpstreamError) as exc_503:
        asyncio.run(fetch_team_snapshot("123", transport=httpx.MockTransport(handler_503)))
    assert exc_503.value.retryable is True

    def handler_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(FPLUpstreamError) as exc_timeout:
        asyncio.run(fetch_team_snapshot("123", transport=httpx.MockTransport(handler_timeout)))
    assert exc_timeout.value.retryable is True


def test_build_snapshot_bundle_missing_fields_raises_data_error() -> None:
    with pytest.raises(FPLDataError):
        build_snapshot_bundle({}, [], {}, {}, [], {})
