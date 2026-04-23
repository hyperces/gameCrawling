from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any

from db_manager import get_connection, get_cursor
from fetch_toto_vote_status import fetch_toto_vote_status, resolve_target_gm_ts


OPTION_META = [
    ("W", 1, "홈"),
    ("D", 2, "무"),
    ("L", 3, "원정"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="진행중 회차의 Betman voteStatus를 DB에 저장합니다."
    )
    parser.add_argument("gm_ts", nargs="?", help="저장할 gmTs. 미입력 시 진행중 회차를 자동 선택합니다.")
    parser.add_argument(
        "--snapshot-kind",
        choices=["periodic", "final"],
        default="periodic",
        help="스냅샷 종류",
    )
    return parser.parse_args()


def normalize_name(value: str | None) -> str:
    return "".join((value or "").split())


def parse_betman_datetime(ms_value: Any) -> datetime | None:
    if ms_value in (None, "", 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(ms_value) / 1000)
    except (TypeError, ValueError):
        return None


def to_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    return int(value)


def to_decimal_value(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def ensure_vote_status_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS vote_status_batches (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            round_id INT NOT NULL,
            gm_id VARCHAR(10) NOT NULL,
            gm_ts INT NOT NULL,
            snapshot_kind ENUM('periodic','final') NOT NULL DEFAULT 'periodic',
            standard_date DATETIME NULL,
            national_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
            international_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
            available_buy_amount BIGINT UNSIGNED NULL,
            comment TEXT NULL,
            rs_msg VARCHAR(255) NULL,
            raw_json JSON NULL,
            fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_latest TINYINT(1) NOT NULL DEFAULT 1,
            KEY idx_vote_status_batches_round_latest (round_id, is_latest, fetched_at),
            KEY idx_vote_status_batches_gmts (gm_ts, fetched_at),
            CONSTRAINT fk_vote_status_batches_round
                FOREIGN KEY (round_id) REFERENCES rounds(id) ON DELETE CASCADE
        ) ENGINE=InnoDB COMMENT='Betman voteStatus snapshot batches by round'
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS vote_status_games (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            batch_id BIGINT UNSIGNED NOT NULL,
            round_id INT NOT NULL,
            game_id INT NOT NULL,
            game_no TINYINT NOT NULL,
            home_team VARCHAR(100) NOT NULL,
            away_team VARCHAR(100) NOT NULL,
            total_vote_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
            api_vote_count_sum BIGINT UNSIGNED NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_vote_status_games_batch_game (batch_id, game_id),
            UNIQUE KEY uk_vote_status_games_batch_game_no (batch_id, game_no),
            KEY idx_vote_status_games_round_game (round_id, game_id),
            CONSTRAINT fk_vote_status_games_batch
                FOREIGN KEY (batch_id) REFERENCES vote_status_batches(id) ON DELETE CASCADE,
            CONSTRAINT fk_vote_status_games_round
                FOREIGN KEY (round_id) REFERENCES rounds(id) ON DELETE CASCADE,
            CONSTRAINT fk_vote_status_games_game
                FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        ) ENGINE=InnoDB COMMENT='Per-game voteStatus snapshot rows'
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS vote_status_options (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            vote_status_game_id BIGINT UNSIGNED NOT NULL,
            option_code ENUM('W','D','L') NOT NULL,
            option_order TINYINT NOT NULL,
            option_label VARCHAR(10) NOT NULL,
            vote_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
            vote_ratio DECIMAL(7,4) NOT NULL DEFAULT 0.0000,
            allot DECIMAL(12,4) NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_vote_status_options_game_option (vote_status_game_id, option_code),
            CONSTRAINT fk_vote_status_options_game
                FOREIGN KEY (vote_status_game_id) REFERENCES vote_status_games(id) ON DELETE CASCADE
        ) ENGINE=InnoDB COMMENT='Per-option voteStatus snapshot rows'
        """
    )


def get_round_and_games(cursor, gm_ts: str | int) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    cursor.execute(
        """
        SELECT id, gm_ts, gm_id, round_number, status, sale_start, sale_end
        FROM rounds
        WHERE gm_ts = %s
        """,
        (int(gm_ts),),
    )
    round_row = cursor.fetchone()
    if not round_row:
        raise ValueError(f"gmTs={gm_ts} 회차가 rounds 테이블에 없습니다.")

    cursor.execute(
        """
        SELECT id, round_id, game_no, league, home_team, away_team
        FROM games
        WHERE round_id = %s
        ORDER BY game_no
        """,
        (int(round_row["id"]),),
    )
    games = cursor.fetchall()
    if len(games) != 14:
        raise ValueError(f"round_id={round_row['id']} 의 games row 수가 14가 아닙니다. count={len(games)}")

    return round_row, {int(row["game_no"]): row for row in games}


def insert_vote_status_snapshot(
    cursor,
    round_row: dict[str, Any],
    db_games_by_no: dict[int, dict[str, Any]],
    response: dict[str, Any],
    snapshot_kind: str,
) -> dict[str, Any]:
    vote_status = response.get("voteStatus") or {}
    schedule_rows = response.get("schedulesList") or []
    vote_rows = vote_status.get("homeVoteStatusList") or []

    if len(schedule_rows) != len(db_games_by_no):
        raise ValueError(
            f"schedulesList 수({len(schedule_rows)})와 DB 경기 수({len(db_games_by_no)})가 일치하지 않습니다."
        )

    if len(vote_rows) != len(db_games_by_no):
        raise ValueError(
            f"homeVoteStatusList 수({len(vote_rows)})와 DB 경기 수({len(db_games_by_no)})가 일치하지 않습니다."
        )

    cursor.execute(
        "UPDATE vote_status_batches SET is_latest = 0 WHERE round_id = %s AND is_latest = 1",
        (int(round_row["id"]),),
    )

    cursor.execute(
        """
        INSERT INTO vote_status_batches (
            round_id, gm_id, gm_ts, snapshot_kind, standard_date,
            national_count, international_count, available_buy_amount,
            comment, rs_msg, raw_json, is_latest
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
        """,
        (
            int(round_row["id"]),
            str(response.get("currentLottery", {}).get("gmId") or round_row["gm_id"]),
            int(response.get("gmTs") or round_row["gm_ts"]),
            snapshot_kind,
            parse_betman_datetime(response.get("standardDate")),
            to_int(response.get("nationalCount")),
            to_int(response.get("internationalCount")),
            to_int(response.get("availableBuyAmount"), default=0) if response.get("availableBuyAmount") not in (None, "") else None,
            str(response.get("comment") or "") or None,
            str(response.get("rsMsg") or "") or None,
            json.dumps(response, ensure_ascii=False),
        ),
    )
    batch_id = int(cursor.lastrowid)

    inserted_games: list[dict[str, Any]] = []

    for index, schedule_row in enumerate(schedule_rows, start=1):
        db_game = db_games_by_no.get(index)
        if not db_game:
            raise ValueError(f"game_no={index} DB 경기 매핑을 찾을 수 없습니다.")

        api_home = normalize_name(schedule_row.get("homeName"))
        api_away = normalize_name(schedule_row.get("awayName"))
        db_home = normalize_name(db_game.get("home_team"))
        db_away = normalize_name(db_game.get("away_team"))
        if api_home != db_home or api_away != db_away:
            raise ValueError(
                f"game_no={index} 팀명 매핑 실패: DB=({db_game['home_team']} vs {db_game['away_team']}), "
                f"API=({schedule_row.get('homeName')} vs {schedule_row.get('awayName')})"
            )

        vote_row = vote_rows[index - 1] if index - 1 < len(vote_rows) else {}
        option_rows = vote_row.get("awayVoteStatusList") or []
        counts = [to_int(item.get("voteCount")) for item in option_rows]
        total_vote_count = sum(counts)

        cursor.execute(
            """
            INSERT INTO vote_status_games (
                batch_id, round_id, game_id, game_no, home_team, away_team,
                total_vote_count, api_vote_count_sum
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                batch_id,
                int(round_row["id"]),
                int(db_game["id"]),
                index,
                str(db_game["home_team"]),
                str(db_game["away_team"]),
                total_vote_count,
                to_int(vote_row.get("voteCountSum"), default=0) if vote_row.get("voteCountSum") not in (None, "") else None,
            ),
        )
        vote_status_game_id = int(cursor.lastrowid)

        option_summaries: list[dict[str, Any]] = []
        for option_index, (option_code, option_order, option_label) in enumerate(OPTION_META):
            option_payload = option_rows[option_index] if option_index < len(option_rows) else {}
            vote_count = to_int(option_payload.get("voteCount"))
            vote_ratio = (vote_count / total_vote_count) if total_vote_count else 0.0
            allot = to_decimal_value(option_payload.get("allot"))

            cursor.execute(
                """
                INSERT INTO vote_status_options (
                    vote_status_game_id, option_code, option_order, option_label,
                    vote_count, vote_ratio, allot
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    vote_status_game_id,
                    option_code,
                    option_order,
                    option_label,
                    vote_count,
                    vote_ratio,
                    allot,
                ),
            )
            option_summaries.append(
                {
                    "option_code": option_code,
                    "vote_count": vote_count,
                    "vote_ratio": round(vote_ratio * 100, 2),
                }
            )

        inserted_games.append(
            {
                "game_id": int(db_game["id"]),
                "game_no": index,
                "home_team": str(db_game["home_team"]),
                "away_team": str(db_game["away_team"]),
                "total_vote_count": total_vote_count,
                "options": option_summaries,
            }
        )

    return {
        "batch_id": batch_id,
        "round_id": int(round_row["id"]),
        "gm_ts": int(round_row["gm_ts"]),
        "games_count": len(inserted_games),
        "inserted_games": inserted_games,
    }


def save_vote_status_snapshot(gm_ts: str | int, snapshot_kind: str) -> dict[str, Any]:
    response = fetch_toto_vote_status(gm_ts)

    with get_connection() as conn, get_cursor(conn) as cursor:
        ensure_vote_status_schema(cursor)
        round_row, db_games_by_no = get_round_and_games(cursor, gm_ts)
        result = insert_vote_status_snapshot(cursor, round_row, db_games_by_no, response, snapshot_kind)

    return result


def main() -> None:
    args = parse_args()

    try:
        gm_ts = resolve_target_gm_ts(args.gm_ts)
    except RuntimeError as exc:
        print(f"[SKIP] {exc}")
        return

    result = save_vote_status_snapshot(gm_ts, args.snapshot_kind)
    print(
        f"[OK] round_id={result['round_id']} gmTs={result['gm_ts']} "
        f"batch_id={result['batch_id']} games={result['games_count']}"
    )
    for game in result["inserted_games"]:
        option_text = ", ".join(
            f"{item['option_code']}={item['vote_count']:,} ({item['vote_ratio']:.2f}%)"
            for item in game["options"]
        )
        print(
            f"- {game['game_no']}경기 game_id={game['game_id']} "
            f"{game['home_team']} vs {game['away_team']} | total={game['total_vote_count']:,} | {option_text}"
        )


if __name__ == "__main__":
    main()
