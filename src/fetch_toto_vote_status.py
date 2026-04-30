from __future__ import annotations

import argparse
import json
from typing import Any

from batman_crawling import (
    build_headers,
    determine_round_status,
    fetch_schedule,
    get_current_ym,
    get_next_ym,
    get_prev_ym,
    post_with_retry,
)
from config import DEFAULT_GM_ID, TOTO_GAME_DATA_URL


def resolve_target_gm_ts(requested_gm_ts: str | None = None) -> str:
    ym_list = [get_prev_ym(), get_current_ym(), get_next_ym()]
    items: list[dict[str, Any]] = []

    for ym in ym_list:
        schedule = fetch_schedule(ym)
        schedule_items = schedule.get("schedules", {}).get("data", [])
        items.extend(schedule_items)

    if not items:
        raise RuntimeError(f"schedule.do 응답에 회차 데이터가 없습니다. checked_ym={','.join(ym_list)}")

    open_items = [item for item in items if determine_round_status(item, "") == "open"]
    if not open_items:
        raise RuntimeError(
            f"진행중인 회차가 없어 totoGameData.do 호출을 건너뜁니다. checked_ym={','.join(ym_list)}"
        )

    if requested_gm_ts is None:
        open_items.sort(key=lambda item: item.get("saleEndDate") or 0)
        return str(open_items[0]["gmTs"])

    requested_gm_ts = str(requested_gm_ts)
    for item in open_items:
        if str(item.get("gmTs")) == requested_gm_ts:
            return requested_gm_ts

    raise RuntimeError(f"gmTs={requested_gm_ts} 는 진행중인 회차가 아닙니다. checked_ym={','.join(ym_list)}")


def fetch_toto_vote_status(gm_ts: str | int, gm_id: str = DEFAULT_GM_ID) -> dict[str, Any]:
    gm_ts_str = str(gm_ts)
    headers = build_headers(
        "https://www.betman.co.kr/main/mainPage/gamebuy/gameSlip.do"
        f"?frameType=typeA&gmId={gm_id}&gmTs={gm_ts_str}"
    )
    payload = {
        "gmId": gm_id,
        "gmTs": gm_ts_str,
        "_sbmInfo": {
            "debugMode": "false",
        },
    }
    response = post_with_retry(
        TOTO_GAME_DATA_URL,
        json=payload,
        headers=headers,
        timeout=30,
        verify=False,
    )
    return response.json()


def summarize_vote_status(response: dict[str, Any]) -> list[str]:
    schedules = response.get("schedulesList") or []
    vote_status = response.get("voteStatus") or {}
    home_vote_status_list = vote_status.get("homeVoteStatusList") or []

    lines: list[str] = []
    lines.append(f"gmTs: {response.get('gmTs')}")
    lines.append(f"voteStatus keys: {list(vote_status.keys())}")
    lines.append(f"scoreRange: {vote_status.get('scoreRange')}")
    lines.append(f"indicator: {vote_status.get('indicator')}")
    lines.append(f"games in schedulesList: {len(schedules)}")
    lines.append(f"games in homeVoteStatusList: {len(home_vote_status_list)}")
    lines.append("")
    lines.append("[경기별 총 투표 현황]")

    selection_labels = ["홈", "무", "원정"]

    for index, schedule in enumerate(schedules):
        vote_row = home_vote_status_list[index] if index < len(home_vote_status_list) else {}
        away_vote_status_list = vote_row.get("awayVoteStatusList") or []

        counts = [int(item.get("voteCount", 0)) for item in away_vote_status_list]
        total_votes = sum(counts)
        home_team = str(schedule.get("homeName", "")).strip()
        away_team = str(schedule.get("awayName", "")).strip()
        game_no = schedule.get("gameSeq") or index + 1

        ratio_parts: list[str] = []
        for label, count in zip(selection_labels, counts):
            ratio = (count / total_votes * 100) if total_votes else 0.0
            ratio_parts.append(f"{label}={count:,} ({ratio:.2f}%)")

        lines.append(f"- {game_no}경기 {home_team} vs {away_team} | total={total_votes:,}")
        if ratio_parts:
            lines.append(f"  {' | '.join(ratio_parts)}")
        else:
            lines.append("  voteStatus 데이터 없음")

    return lines


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python src/fetch_toto_vote_status.py",
        description="Betman totoGameData.do API에서 voteStatus를 조회합니다.",
    )
    parser.add_argument("gm_ts", nargs="?", help="조회할 gmTs. 미입력 시 진행중 회차를 사용합니다.")
    parser.add_argument("--gm-id", default=DEFAULT_GM_ID, help="게임 ID (기본값: G011)")
    parser.add_argument("--raw", action="store_true", help="응답 JSON 전체를 출력합니다.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        gm_ts = resolve_target_gm_ts(args.gm_ts)
    except RuntimeError as exc:
        print(f"[SKIP] {exc}")
        return

    response = fetch_toto_vote_status(gm_ts, gm_id=args.gm_id)

    if args.raw:
        print(json.dumps(response, ensure_ascii=False, indent=2, default=str))
        return

    for line in summarize_vote_status(response):
        print(line)


if __name__ == "__main__":
    main()
