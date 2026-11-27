"""FPL public API adapter, FT rollover calculator, and selling price estimator."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from backend.models import (
    FixtureInfo,
    FPLUniverse,
    PlayerSnapshot,
    Position,
    SnapshotBundle,
    TeamSnapshot,
    UniversePlayer,
)

FPL_BASE_URL = "https://fantasy.premierleague.com/api"
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

ELEMENT_TYPE_MAP: dict[int, Position] = {
    1: "GKP",
    2: "DEF",
    3: "MID",
    4: "FWD",
}

ALL_CHIPS = ["wildcard", "freehit", "bboost", "3xc"]


class FPLClientError(Exception):
    """Base exception for FPL client errors with user-facing Chinese messages."""

    def __init__(self, message: str, status_code: int = 500, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class FPLInvalidInputError(FPLClientError):
    def __init__(self, message: str = "请输入有效的纯数字 Team ID 或 FPL 官网球队链接。") -> None:
        super().__init__(message, status_code=400, retryable=False)


class FPLNotFoundError(FPLClientError):
    def __init__(self, team_id: int) -> None:
        super().__init__(
            f"未找到 Team ID 为 {team_id} 的球队，请检查 ID 是否正确。",
            status_code=404,
            retryable=False,
        )


class FPLUpstreamError(FPLClientError):
    def __init__(self, detail: str, status_code: int = 503) -> None:
        super().__init__(
            f"FPL 官方接口暂时不可用或超时（{detail}），请稍后重试。",
            status_code=status_code,
            retryable=True,
        )


class FPLDataError(FPLClientError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=502, retryable=False)


def parse_team_id(team_id_or_url: str | int) -> int:
    """Extract and validate a positive integer Team ID from raw ID or FPL URL."""
    if isinstance(team_id_or_url, bool):
        raise FPLInvalidInputError()

    if isinstance(team_id_or_url, int):
        if team_id_or_url > 0:
            return team_id_or_url
        raise FPLInvalidInputError()

    raw = team_id_or_url.strip()
    if not raw:
        raise FPLInvalidInputError()

    if raw.isdigit():
        val = int(raw)
        if val > 0:
            return val
        raise FPLInvalidInputError()

    match = re.search(r"/entry/(\d+)", raw)
    if match:
        val = int(match.group(1))
        if val > 0:
            return val

    raise FPLInvalidInputError()


def calculate_free_transfers(
    started_event: int,
    history_current: list[dict[str, Any]],
    chips: list[dict[str, Any]],
    target_gw: int,
) -> int:
    """Roll over Free Transfers from `started_event` up to `target_gw` (cap 5).

    Per 2024/25+ FPL rules:
    - Team creation GW (`started_event`) grants 1 FT entering the next GW.
    - Playing `wildcard` or `freehit` preserves existing FT without +1 or deduction.
    - Normal GWs: `min(5, max(0, prev_ft - event_transfers) + 1)`.
    """
    chips_by_event: dict[int, str] = {
        int(c["event"]): str(c["name"])
        for c in chips
        if "event" in c and "name" in c
    }
    history_by_event: dict[int, dict[str, Any]] = {
        int(row["event"]): row
        for row in history_current
        if "event" in row
    }

    ft = 1
    for ev in range(started_event + 1, target_gw):
        row = history_by_event.get(ev)
        if row is None:
            continue
        chip = chips_by_event.get(ev)
        if chip in ("wildcard", "freehit"):
            continue
        transfers_used = int(row.get("event_transfers", 0))
        ft = min(5, max(0, ft - transfers_used) + 1)

    return max(1, min(5, ft))


def calculate_selling_price(
    now_cost_tenths: int, buy_price_tenths: int | None
) -> tuple[float, bool]:
    """Compute selling price in £m and whether it is estimated (Option A).

    - If `buy_price_tenths` is known from transfer history:
      half of profit rounded down to £0.1m if price rose; full loss if dropped.
    - If unknown (initial squad veteran): fallback to `now_cost` and mark estimated=True.
    """
    if buy_price_tenths is None:
        return round(now_cost_tenths / 10.0, 1), True

    if now_cost_tenths > buy_price_tenths:
        profit = now_cost_tenths - buy_price_tenths
        sell_tenths = buy_price_tenths + (profit // 2)
    else:
        sell_tenths = now_cost_tenths

    return round(sell_tenths / 10.0, 1), False


def extract_latest_buy_prices(transfers: list[dict[str, Any]]) -> dict[int, int]:
    """Map player_id -> most recent `element_in_cost` (in tenths of £m)."""
    sorted_transfers = sorted(
        transfers,
        key=lambda t: (int(t.get("event", 0)), str(t.get("time", ""))),
    )
    buy_prices: dict[int, int] = {}
    for t in sorted_transfers:
        if "element_in" in t and "element_in_cost" in t:
            buy_prices[int(t["element_in"])] = int(t["element_in_cost"])
    return buy_prices


def _compute_available_chips(chips: list[dict[str, Any]], target_gw: int) -> list[str]:
    """Return remaining chips for the current half-season (GW1-19 or GW20-38)."""
    used_in_half: set[str] = set()
    for c in chips:
        ev = int(c.get("event", 0))
        name = str(c.get("name", ""))
        if target_gw <= 19 and 1 <= ev <= 19:
            used_in_half.add(name)
        elif target_gw >= 20 and ev >= 20:
            used_in_half.add(name)
    return [chip for chip in ALL_CHIPS if chip not in used_in_half]


def _build_team_fdr_map(
    fixtures: list[FixtureInfo],
    teams: dict[int, str],
    start_gw: int,
    limit: int = 5,
) -> dict[int, list[str]]:
    """Build upcoming fixture labels like `WHU(A,2)` per team_id starting at `start_gw`."""
    upcoming = sorted(
        (f for f in fixtures if f.event is not None and f.event >= start_gw and not f.finished),
        key=lambda f: f.event or 99,
    )
    result: dict[int, list[str]] = {tid: [] for tid in teams}
    for f in upcoming:
        h_name = teams.get(f.team_h, str(f.team_h))
        a_name = teams.get(f.team_a, str(f.team_a))
        if f.team_h in result and len(result[f.team_h]) < limit:
            result[f.team_h].append(f"{a_name}(H,{f.team_h_difficulty})")
        if f.team_a in result and len(result[f.team_a]) < limit:
            result[f.team_a].append(f"{h_name}(A,{f.team_a_difficulty})")
    return result


def build_snapshot_bundle(
    bootstrap: dict[str, Any],
    fixtures_raw: list[dict[str, Any]],
    entry: dict[str, Any],
    history: dict[str, Any],
    transfers: list[dict[str, Any]],
    picks_data: dict[str, Any],
) -> SnapshotBundle:
    """Pure deterministic transformer from raw FPL JSON payloads to SnapshotBundle."""
    try:
        events = bootstrap["events"]
        teams_raw = bootstrap["teams"]
        elements_raw = bootstrap["elements"]
    except KeyError as exc:
        raise FPLDataError("官方返回数据格式异常，无法解析球队快照，请重试。") from exc

    current_event_obj = next((e for e in events if e.get("is_current")), None)
    next_event_obj = next((e for e in events if e.get("is_next")), None)

    if current_event_obj is None:
        raise FPLDataError("该球队当前暂无可用阵容快照（可能尚未完成建队或赛季未开始）。")

    picks_gw = int(current_event_obj["id"])
    target_gw = int(next_event_obj["id"]) if next_event_obj else min(38, picks_gw + 1)

    teams_map: dict[int, str] = {int(t["id"]): str(t["short_name"]) for t in teams_raw}
    fixtures: list[FixtureInfo] = []
    for f in fixtures_raw:
        try:
            fixtures.append(
                FixtureInfo(
                    event=f.get("event"),
                    team_h=int(f["team_h"]),
                    team_a=int(f["team_a"]),
                    team_h_difficulty=int(f["team_h_difficulty"]),
                    team_a_difficulty=int(f["team_a_difficulty"]),
                    finished=bool(f.get("finished", False)),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise FPLDataError("官方返回数据格式异常，无法解析球队快照，请重试。") from exc

    team_fdr_5 = _build_team_fdr_map(fixtures, teams_map, start_gw=target_gw, limit=5)

    universe_players: dict[int, UniversePlayer] = {}
    raw_cost_tenths: dict[int, int] = {}

    for el in elements_raw:
        try:
            pid = int(el["id"])
            tid = int(el["team"])
            pos = ELEMENT_TYPE_MAP[int(el["element_type"])]
            cost_tenths = int(el["now_cost"])
            raw_cost_tenths[pid] = cost_tenths

            first = str(el.get("first_name", "")).strip()
            second = str(el.get("second_name", "")).strip()
            full_name = f"{first} {second}".strip() or str(el["web_name"])

            universe_players[pid] = UniversePlayer(
                id=pid,
                name=str(el["web_name"]),
                full_name=full_name,
                team_id=tid,
                team=teams_map.get(tid, str(tid)),
                pos=pos,
                now_cost=round(cost_tenths / 10.0, 1),
                status=str(el.get("status", "a")),
                chance_of_playing=el.get("chance_of_playing_next_round"),
                news=str(el.get("news") or ""),
                form=float(el.get("form") or 0.0),
                ppg=float(el.get("points_per_game") or 0.0),
                total_points=int(el.get("total_points") or 0),
                minutes=int(el.get("minutes") or 0),
                selected_by_percent=float(el.get("selected_by_percent") or 0.0),
                defensive_contribution=int(el.get("defensive_contribution") or 0),
                next_5_fdr=team_fdr_5.get(tid, []),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise FPLDataError("官方返回数据格式异常，无法解析球队快照，请重试。") from exc

    try:
        team_id = int(entry["id"])
        team_name = str(entry["name"])
        started_event = int(entry["started_event"])
        history_current = list(history["current"])
        chips = list(history.get("chips", []))
        picks_list = list(picks_data["picks"])
        entry_history = picks_data["entry_history"]
        bank_tenths = int(entry_history["bank"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FPLDataError("官方返回数据格式异常，无法解析球队快照，请重试。") from exc

    ft = calculate_free_transfers(
        started_event=started_event,
        history_current=history_current,
        chips=chips,
        target_gw=target_gw,
    )
    available_chips = _compute_available_chips(chips, target_gw=target_gw)
    buy_prices = extract_latest_buy_prices(transfers)

    squad_players: list[PlayerSnapshot] = []
    for pick in picks_list:
        try:
            pid = int(pick["element"])
            position_idx = int(pick["position"])
            u_player = universe_players[pid]
            now_tenths = raw_cost_tenths[pid]
        except (KeyError, TypeError, ValueError) as exc:
            raise FPLDataError("官方返回数据格式异常，无法解析球队快照，请重试。") from exc

        selling_price, is_estimated = calculate_selling_price(
            now_cost_tenths=now_tenths,
            buy_price_tenths=buy_prices.get(pid),
        )
        is_starter = position_idx <= 11
        bench_order = 0 if is_starter else position_idx - 11

        squad_players.append(
            PlayerSnapshot(
                id=pid,
                name=u_player.name,
                team=u_player.team,
                team_id=u_player.team_id,
                pos=u_player.pos,
                now_cost=u_player.now_cost,
                selling_price=selling_price,
                price_is_estimated=is_estimated,
                status=u_player.status,
                chance_of_playing=u_player.chance_of_playing,
                news=u_player.news,
                form=u_player.form,
                ppg=u_player.ppg,
                next_3_fdr=u_player.next_5_fdr[:3],
                is_starter=is_starter,
                bench_order=bench_order,
                is_captain=bool(pick.get("is_captain", False)),
                is_vice_captain=bool(pick.get("is_vice_captain", False)),
            )
        )

    try:
        snapshot = TeamSnapshot(
            team_id=team_id,
            team_name=team_name,
            gw=target_gw,
            picks_gw=picks_gw,
            started_event=started_event,
            bank=round(bank_tenths / 10.0, 1),
            free_transfers=ft,
            ft_is_calibrated=False,
            available_chips=available_chips,
            players=squad_players,
        )
    except ValidationError as exc:
        raise FPLDataError("官方返回数据格式异常，无法解析球队快照，请重试。") from exc

    universe = FPLUniverse(
        current_gw=picks_gw,
        next_gw=target_gw,
        teams=teams_map,
        players=universe_players,
        fixtures=fixtures,
    )
    return SnapshotBundle(snapshot=snapshot, universe=universe)


async def _fetch_json(client: httpx.AsyncClient, path: str, team_id: int | None = None) -> Any:
    url = f"{FPL_BASE_URL}/{path.lstrip('/')}"
    try:
        resp = await client.get(url)
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise FPLUpstreamError("网络超时") from exc

    if resp.status_code == 404:
        if team_id is not None and f"entry/{team_id}" in path and "/picks/" not in path:
            raise FPLNotFoundError(team_id)
        if "/picks/" in path:
            raise FPLDataError("该球队当前暂无可用阵容快照（可能尚未完成建队或赛季未开始）。")
        raise FPLUpstreamError("404", status_code=404)

    if resp.status_code in (429, 500, 502, 503, 504):
        raise FPLUpstreamError(str(resp.status_code), status_code=resp.status_code)

    if resp.status_code >= 400:
        raise FPLUpstreamError(str(resp.status_code), status_code=resp.status_code)

    try:
        return resp.json()
    except ValueError as exc:
        raise FPLDataError("官方返回数据格式异常，无法解析球队快照，请重试。") from exc


async def fetch_team_snapshot(
    team_id_or_url: str | int,
    *,
    demo: bool = False,
    timeout_seconds: float = 10.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SnapshotBundle:
    """Fetch live FPL snapshot (or explicit demo fixture when `demo=True`)."""
    if demo:
        return load_demo_snapshot()

    team_id = parse_team_id(team_id_or_url)

    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        headers={"User-Agent": "OneMoveFPL/0.1"},
        transport=transport,
    ) as client:
        bootstrap, fixtures_raw, entry, history, transfers = await asyncio.gather(
            _fetch_json(client, "bootstrap-static/"),
            _fetch_json(client, "fixtures/"),
            _fetch_json(client, f"entry/{team_id}/", team_id=team_id),
            _fetch_json(client, f"entry/{team_id}/history/", team_id=team_id),
            _fetch_json(client, f"entry/{team_id}/transfers/", team_id=team_id),
        )

        events = bootstrap.get("events", [])
        current_event_obj = next((e for e in events if e.get("is_current")), None)
        if current_event_obj is None:
            raise FPLDataError("该球队当前暂无可用阵容快照（可能尚未完成建队或赛季未开始）。")

        picks_gw = int(current_event_obj["id"])
        picks_data = await _fetch_json(
            client, f"entry/{team_id}/event/{picks_gw}/picks/", team_id=team_id
        )

    return build_snapshot_bundle(
        bootstrap=bootstrap,
        fixtures_raw=fixtures_raw,
        entry=entry,
        history=history,
        transfers=transfers,
        picks_data=picks_data,
    )


def load_demo_snapshot() -> SnapshotBundle:
    """Load local sanitized demo fixtures (only when explicitly requested)."""
    def _load(filename: str) -> Any:
        path = FIXTURES_DIR / filename
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    return build_snapshot_bundle(
        bootstrap=_load("demo_bootstrap.json"),
        fixtures_raw=_load("demo_fixtures.json"),
        entry=_load("demo_entry.json"),
        history=_load("demo_history.json"),
        transfers=_load("demo_transfers.json"),
        picks_data=_load("demo_picks.json"),
    )
