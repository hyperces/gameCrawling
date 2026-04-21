"""베트맨 경기 결과 API 데이터를 DB에 저장하고 적중 통계까지 반영하는 스크립트"""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from typing import Any

from db_manager import (
    evaluate_picks_for_round,
    get_connection,
    get_cursor,
    get_games_by_round as fetch_games_by_round,
    get_rounds_without_results,
    mark_round_result_saved,
)
from fetch_game_results import fetch_game_results


CODE_TO_PICK_RESULT = {
    0: "W",
    1: "D",
    2: "L",
    3: None,
    99: None,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="결과 API를 받아 games/picks/round_user_results/result_saved까지 반영합니다."
    )
    parser.add_argument(
        "gm_ts",
        nargs="?",
        help="특정 gmTs만 처리합니다. 미지정 시 결과 미완료 회차 중 처리 가능한 회차를 자동 처리합니다.",
    )
    return parser.parse_args()


def normalize_name(value: str | None) -> str:
    return "".join((value or "").split())


def parse_game_datetime(date_str: str | None) -> datetime | None:
    if not date_str:
        return None

    cleaned = str(date_str).strip()
    cleaned = re.sub(r"\s*\([^)]*\)\s*", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    for fmt in ("%y.%m.%d %H:%M", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue

    return None


def get_latest_game_datetime_for_round(round_id: int) -> datetime | None:
    latest_game_time: datetime | None = None

    for game in fetch_games_by_round(round_id):
        game_time = parse_game_datetime(game.get("game_date"))
        if game_time is None:
            continue
        if latest_game_time is None or game_time > latest_game_time:
            latest_game_time = game_time

    return latest_game_time


def ensure_win_result_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS win_result_codes (
            code SMALLINT NOT NULL PRIMARY KEY COMMENT 'Betman raw result code',
            value VARCHAR(20) NOT NULL COMMENT 'Korean label from API',
            pick_result ENUM('W','D','L') NULL COMMENT 'normalized result for picks, NULL for special/cancel',
            sort_order SMALLINT NOT NULL DEFAULT 0 COMMENT 'display order',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB COMMENT='Betman win result code master'
        """
    )

    cursor.execute(
        """
        SELECT COLUMN_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'games'
        """
    )
    existing_columns = {row["COLUMN_NAME"] for row in cursor.fetchall()}

    alter_statements: list[str] = []
    if "home_score" not in existing_columns:
        alter_statements.append(
            "ADD COLUMN home_score SMALLINT NULL COMMENT 'home score from result API' AFTER game_date"
        )
    if "away_score" not in existing_columns:
        alter_statements.append(
            "ADD COLUMN away_score SMALLINT NULL COMMENT 'away score from result API' AFTER home_score"
        )
    if "win_result_code" not in existing_columns:
        alter_statements.append(
            "ADD COLUMN win_result_code SMALLINT NULL COMMENT 'raw result code from Betman result API' AFTER away_score"
        )
    if "result_checked_at" not in existing_columns:
        alter_statements.append(
            "ADD COLUMN result_checked_at DATETIME NULL COMMENT 'latest result sync time' AFTER result"
        )

    if alter_statements:
        cursor.execute(f"ALTER TABLE games {', '.join(alter_statements)}")

    cursor.execute("SHOW INDEX FROM games WHERE Key_name = 'idx_games_win_result_code'")
    if not cursor.fetchall():
        cursor.execute("ALTER TABLE games ADD INDEX idx_games_win_result_code (win_result_code)")

    cursor.execute(
        """
        SELECT CONSTRAINT_NAME
        FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'games'
          AND COLUMN_NAME = 'win_result_code'
          AND REFERENCED_TABLE_NAME = 'win_result_codes'
        """
    )
    if not cursor.fetchall():
        cursor.execute(
            """
            ALTER TABLE games
            ADD CONSTRAINT fk_games_win_result_code
            FOREIGN KEY (win_result_code) REFERENCES win_result_codes(code)
            """
        )


def upsert_win_result_codes(cursor, result_codes: list[dict[str, Any]]) -> None:
    for sort_order, item in enumerate(result_codes, start=1):
        code = int(item["code"])
        cursor.execute(
            """
            INSERT INTO win_result_codes (code, value, pick_result, sort_order)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                value = VALUES(value),
                pick_result = VALUES(pick_result),
                sort_order = VALUES(sort_order),
                updated_at = CURRENT_TIMESTAMP
            """,
            (code, item["value"], CODE_TO_PICK_RESULT.get(code), sort_order),
        )


def get_round_row_by_gm_ts(cursor, gm_ts: str | int) -> dict[str, Any] | None:
    cursor.execute(
        "SELECT id, round_number, gm_ts FROM rounds WHERE gm_ts = %s",
        (int(gm_ts),),
    )
    return cursor.fetchone()


def get_db_games_by_round(cursor, round_id: int) -> dict[int, dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, game_no, home_team, away_team, result, win_result_code, home_score, away_score
        FROM games
        WHERE round_id = %s
        ORDER BY game_no
        """,
        (round_id,),
    )
    return {int(row["game_no"]): row for row in cursor.fetchall()}


def update_game_rows(
    cursor,
    round_id: int,
    db_games: dict[int, dict[str, Any]],
    detail_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    updated_rows: list[dict[str, Any]] = []

    for item in detail_rows:
        game_no = int(item["GM_SEQ"])
        db_game = db_games.get(game_no)
        if not db_game:
            raise ValueError(f"round_id={round_id} game_no={game_no} 경기 행을 찾을 수 없습니다.")

        api_home = normalize_name(item.get("HM_TEAM_NM"))
        api_away = normalize_name(item.get("AW_TEAM_NM"))
        db_home = normalize_name(db_game.get("home_team"))
        db_away = normalize_name(db_game.get("away_team"))
        if api_home != db_home or api_away != db_away:
            raise ValueError(
                f"game_no={game_no} 팀명이 일치하지 않습니다. "
                f"DB=({db_game.get('home_team')} vs {db_game.get('away_team')}), "
                f"API=({item.get('HM_TEAM_NM')} vs {item.get('AW_TEAM_NM')})"
            )

        win_result_code = int(item["TOTO_RSLT_VAL"])
        pick_result = CODE_TO_PICK_RESULT.get(win_result_code)

        cursor.execute(
            """
            UPDATE games
            SET home_score = %s,
                away_score = %s,
                win_result_code = %s,
                result = %s,
                result_checked_at = CURRENT_TIMESTAMP
            WHERE round_id = %s AND game_no = %s
            """,
            (
                item.get("HM_TEAM_MCH_RSLT_VAL"),
                item.get("AW_TEAM_MCH_RSLT_VAL"),
                win_result_code,
                pick_result,
                round_id,
                game_no,
            ),
        )

        updated_rows.append(
            {
                "game_no": game_no,
                "home_team": db_game.get("home_team"),
                "away_team": db_game.get("away_team"),
                "home_score": item.get("HM_TEAM_MCH_RSLT_VAL"),
                "away_score": item.get("AW_TEAM_MCH_RSLT_VAL"),
                "win_result_code": win_result_code,
                "pick_result": pick_result,
            }
        )

    return updated_rows


def sync_results_for_gm_ts(gm_ts: str | int) -> dict[str, Any]:
    response = fetch_game_results(gm_ts)
    detail_rows = response.get("detlBody", [])
    result_codes = response.get("winrstCode", [])

    if not detail_rows:
        raise ValueError(f"gmTs={gm_ts} 응답에 detlBody가 없습니다.")

    with get_connection() as conn, get_cursor(conn) as cursor:
        ensure_win_result_schema(cursor)
        upsert_win_result_codes(cursor, result_codes)

        round_row = get_round_row_by_gm_ts(cursor, gm_ts)
        if not round_row:
            raise ValueError(f"gmTs={gm_ts} 회차가 rounds 테이블에 없습니다.")

        round_id = int(round_row["id"])
        db_games = get_db_games_by_round(cursor, round_id)
        updated_rows = update_game_rows(cursor, round_id, db_games, detail_rows)

        if len(updated_rows) != len(db_games):
            raise ValueError(
                f"gmTs={gm_ts} 결과 저장 건수({len(updated_rows)})와 DB 경기 수({len(db_games)})가 일치하지 않습니다."
            )

    user_stats = evaluate_picks_for_round(round_id)
    mark_round_result_saved(round_id)

    return {
        "gm_ts": str(gm_ts),
        "round_id": round_id,
        "round_number": str(round_row["round_number"]),
        "result_codes_count": len(result_codes),
        "updated_rows": updated_rows,
        "user_stats": user_stats,
    }


def process_pending_results() -> int:
    pending_rounds = get_rounds_without_results()
    if not pending_rounds:
        print("[INFO] 결과 처리 대상 회차가 없습니다.")
        return 0

    print(f"[INFO] 결과 미완료 회차 수: {len(pending_rounds)}")
    processed_count = 0

    for round_info in sorted(pending_rounds, key=lambda item: int(item["gm_ts"])):
        round_id = int(round_info["id"])
        gm_ts = int(round_info["gm_ts"])
        round_number = str(round_info["round_number"])
        latest_game_time = get_latest_game_datetime_for_round(round_id)

        if latest_game_time is None:
            print(
                f"\n[SKIP] round={round_number} gmTs={gm_ts}: "
                "마지막 경기 시간을 확인할 수 없습니다."
            )
            continue

        if datetime.now() <= latest_game_time:
            print(
                f"\n[SKIP] round={round_number} gmTs={gm_ts}: "
                f"마지막 경기 시간({latest_game_time:%Y-%m-%d %H:%M}) 전입니다."
            )
            continue

        print(
            f"\n[RUN] round={round_number} gmTs={gm_ts}: "
            f"마지막 경기 시간 경과({latest_game_time:%Y-%m-%d %H:%M})"
        )

        try:
            result = sync_results_for_gm_ts(gm_ts)
        except Exception as exc:
            print(f"[ERROR] round={round_number} gmTs={gm_ts}: {exc}")
            continue

        processed_count += 1
        print(
            f"[OK] gmTs={result['gm_ts']}, round={result['round_number']} "
            f"결과 코드 {result['result_codes_count']}건 / 경기 결과 {len(result['updated_rows'])}건 저장 완료"
        )
        for row in result["updated_rows"]:
            print(
                f"- {row['game_no']}경기 {row['home_team']} {row['home_score']}:{row['away_score']} "
                f"{row['away_team']} | code={row['win_result_code']} | pick_result={row['pick_result']}"
            )
        if not result["user_stats"]:
            print("  -> 집계할 픽이 없습니다.")
        else:
            for user_id, stats in sorted(result["user_stats"].items()):
                print(
                    f"  -> user_id={user_id}: "
                    f"{stats['correct']}/{stats['total']} 맞음 "
                    f"({stats['wrong']}개 틀림)"
                )

    print(f"\n[SUMMARY] 처리 완료 회차 수: {processed_count}")
    return 0


def main() -> None:
    args = parse_args()

    if args.gm_ts:
        result = sync_results_for_gm_ts(args.gm_ts)
        print(
            f"[OK] gmTs={result['gm_ts']}, round={result['round_number']} "
            f"결과 코드 {result['result_codes_count']}건 / 경기 결과 {len(result['updated_rows'])}건 저장 완료"
        )
        for row in result["updated_rows"]:
            print(
                f"- {row['game_no']}경기 {row['home_team']} {row['home_score']}:{row['away_score']} "
                f"{row['away_team']} | code={row['win_result_code']} | pick_result={row['pick_result']}"
            )
        if not result["user_stats"]:
            print("  -> 집계할 픽이 없습니다.")
        else:
            for user_id, stats in sorted(result["user_stats"].items()):
                print(
                    f"  -> user_id={user_id}: "
                    f"{stats['correct']}/{stats['total']} 맞음 "
                    f"({stats['wrong']}개 틀림)"
                )
        return

    process_pending_results()


if __name__ == "__main__":
    main()
