"""회차별 적중 통계(round_user_results) 재집계 스크립트"""

from __future__ import annotations

import argparse

from db_manager import evaluate_picks_for_round, get_rounds_for_stats_aggregation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="games/picks 데이터를 기준으로 round_user_results를 재계산합니다."
    )
    parser.add_argument(
        "--year",
        help="특정 연도만 집계합니다. 예: 2026",
    )
    parser.add_argument(
        "--round-id",
        type=int,
        help="특정 round_id만 집계합니다.",
    )
    parser.add_argument(
        "--gm-ts",
        type=int,
        help="특정 gm_ts 회차만 집계합니다.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rounds = get_rounds_for_stats_aggregation(
        year=args.year,
        round_id=args.round_id,
        gm_ts=args.gm_ts,
    )

    if not rounds:
        print("[INFO] 집계할 회차가 없습니다.")
        return 0

    print(f"[INFO] 집계 대상 회차 수: {len(rounds)}")

    total_users = 0
    total_picks = 0
    total_correct = 0
    total_wrong = 0

    for round_row in sorted(rounds, key=lambda item: int(item["gm_ts"])):
        round_id = int(round_row["id"])
        round_number = str(round_row["round_number"])
        gm_ts = int(round_row["gm_ts"])
        ym = str(round_row["ym"])
        resolved_games = int(round_row.get("resolved_games") or 0)
        pick_count = int(round_row.get("pick_count") or 0)

        print(
            f"\n[ROUND] round_id={round_id} round={round_number} "
            f"gm_ts={gm_ts} ym={ym} resolved_games={resolved_games} picks={pick_count}"
        )

        user_stats = evaluate_picks_for_round(round_id)
        if not user_stats:
            print("  -> 집계할 픽이 없습니다.")
            continue

        total_users += len(user_stats)
        for user_id, stats in sorted(user_stats.items()):
            total_picks += stats["total"]
            total_correct += stats["correct"]
            total_wrong += stats["wrong"]
            print(
                f"  -> user_id={user_id}: "
                f"{stats['correct']}/{stats['total']} 맞음 "
                f"({stats['wrong']}개 틀림)"
            )

    print(
        "\n[SUMMARY] "
        f"rounds={len(rounds)} users={total_users} "
        f"picks={total_picks} correct={total_correct} wrong={total_wrong}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
