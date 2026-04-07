"""MySQL 데이터베이스 관리 모듈
유저/로테이션 설정은 모두 DB에서 조회합니다.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Generator

import mysql.connector
from mysql.connector import MySQLConnection
from mysql.connector.cursor import MySQLCursor

from config import DB_CONFIG


@contextmanager
def get_connection() -> Generator[MySQLConnection, None, None]:
    """DB 커넥션 컨텍스트 매니저"""
    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_cursor(conn: MySQLConnection) -> Generator[MySQLCursor, None, None]:
    """커서 컨텍스트 매니저 (자동 커밋)"""
    cursor = conn.cursor(dictionary=True)
    try:
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


# ============================================
# 회차 관련
# ============================================

def upsert_round(
    gm_ts: int,
    gm_id: str,
    round_number: str,
    ym: str,
    status: str = "open",
    sale_start: datetime | None = None,
    sale_end: datetime | None = None,
) -> int:
    """회차 정보를 저장하거나 업데이트하고 round_id를 반환"""
    with get_connection() as conn, get_cursor(conn) as cursor:
        cursor.execute(
            """
            INSERT INTO rounds (gm_ts, gm_id, round_number, ym, status, sale_start, sale_end)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                round_number = VALUES(round_number),
                status = VALUES(status),
                sale_start = VALUES(sale_start),
                sale_end = VALUES(sale_end),
                updated_at = CURRENT_TIMESTAMP
            """,
            (gm_ts, gm_id, round_number, ym, status, sale_start, sale_end),
        )

        cursor.execute("SELECT id FROM rounds WHERE gm_ts = %s", (gm_ts,))
        row = cursor.fetchone()
        return row["id"]


def update_round_status(round_id: int, status: str) -> None:
    """회차 상태 업데이트"""
    with get_connection() as conn, get_cursor(conn) as cursor:
        cursor.execute(
            "UPDATE rounds SET status = %s WHERE id = %s",
            (status, round_id),
        )


def get_round_by_gm_ts(gm_ts: int) -> dict | None:
    """gm_ts로 회차 조회"""
    with get_connection() as conn, get_cursor(conn) as cursor:
        cursor.execute("SELECT * FROM rounds WHERE gm_ts = %s", (gm_ts,))
        return cursor.fetchone()


def get_closed_rounds_without_results() -> list[dict]:
    """마감되었지만 결과가 아직 저장되지 않은 회차 목록"""
    with get_connection() as conn, get_cursor(conn) as cursor:
        cursor.execute(
            "SELECT * FROM rounds WHERE status = 'closed' AND result_saved = 0"
        )
        return cursor.fetchall()


def mark_round_result_saved(round_id: int) -> None:
    """회차 결과 저장 완료 표시"""
    with get_connection() as conn, get_cursor(conn) as cursor:
        cursor.execute(
            "UPDATE rounds SET result_saved = 1 WHERE id = %s",
            (round_id,),
        )


# ============================================
# 경기 관련
# ============================================

def upsert_games(round_id: int, games_data: list[dict]) -> None:
    """경기 정보 일괄 저장 (upsert)"""
    with get_connection() as conn, get_cursor(conn) as cursor:
        for game in games_data:
            cursor.execute(
                """
                INSERT INTO games (round_id, game_no, league, home_team, away_team, game_date)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    league = VALUES(league),
                    home_team = VALUES(home_team),
                    away_team = VALUES(away_team),
                    game_date = VALUES(game_date)
                """,
                (
                    round_id,
                    game["game_no"],
                    game["league"],
                    game["home_team"],
                    game["away_team"],
                    game.get("game_date"),
                ),
            )


def update_game_result(round_id: int, game_no: int, result: str) -> None:
    """경기 결과 업데이트"""
    with get_connection() as conn, get_cursor(conn) as cursor:
        cursor.execute(
            """
            UPDATE games SET result = %s
            WHERE round_id = %s AND game_no = %s
            """,
            (result, round_id, game_no),
        )


def get_games_by_round(round_id: int) -> list[dict]:
    """특정 회차의 경기 목록 조회"""
    with get_connection() as conn, get_cursor(conn) as cursor:
        cursor.execute(
            "SELECT * FROM games WHERE round_id = %s ORDER BY game_no",
            (round_id,),
        )
        return cursor.fetchall()


# ============================================
# 유저 관련 (DB에서 조회)
# ============================================

def get_active_users_ordered() -> list[dict]:
    """활성 유저 목록 (sort_order 순)"""
    with get_connection() as conn, get_cursor(conn) as cursor:
        cursor.execute(
            "SELECT id, username, display_name, sort_order "
            "FROM users WHERE is_active = 1 ORDER BY sort_order"
        )
        return cursor.fetchall()


def get_user_id_map() -> dict[str, int]:
    """username -> user_id 매핑 조회"""
    with get_connection() as conn, get_cursor(conn) as cursor:
        cursor.execute("SELECT id, username FROM users WHERE is_active = 1")
        rows = cursor.fetchall()
        return {row["username"]: row["id"] for row in rows}


# ============================================
# 로테이션 관련 (DB 기반)
# ============================================

def get_rotation_base_config() -> dict:
    """
    DB에서 로테이션 기준 설정 조회.
    반환: {
        "base_round_number": int,
        "base_users": [user_id, ...],  # rotation_no 순서대로
    }
    """
    with get_connection() as conn, get_cursor(conn) as cursor:
        cursor.execute(
            "SELECT base_round_number, rotation_no, user_id "
            "FROM rotation_base_config ORDER BY rotation_no"
        )
        rows = cursor.fetchall()

    if not rows:
        raise ValueError("rotation_base_config 테이블에 데이터가 없습니다. DB를 확인해주세요.")

    base_round_number = rows[0]["base_round_number"]
    base_users = [row["user_id"] for row in rows]  # rot1, rot2, rot3 순서
    return {"base_round_number": base_round_number, "base_users": base_users}


def get_round_number(round_id: int) -> int:
    """round_id로 round_number 조회"""
    with get_connection() as conn, get_cursor(conn) as cursor:
        cursor.execute("SELECT round_number FROM rounds WHERE id = %s", (round_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"round_id={round_id}에 해당하는 회차가 없습니다.")
        return int(row["round_number"])


def calculate_rotation_assignment(round_id: int) -> dict[int, int]:
    """
    회차에 따른 로테이션 배정 계산 (DB 기반).

    기준 회차(base_round_number)의 배정을 기반으로,
    매 회차마다 유저가 한 칸씩 밀려남 (우측 순환).

    예) base=20회차: rot1=초, rot2=광, rot3=범
        21회차: rot1=범, rot2=초, rot3=광  (한 칸 밀림)
        22회차: rot1=광, rot2=범, rot3=초  (두 칸 밀림)
        23회차: rot1=초, rot2=광, rot3=범  (다시 처음)

    반환: {rotation_no: user_id}
    """
    config = get_rotation_base_config()
    base_users = config["base_users"]  # [초, 광, 범] for rot [1, 2, 3]
    user_count = len(base_users)

    if user_count == 0:
        raise ValueError("활성 유저가 없습니다.")

    round_number = get_round_number(round_id)
    offset = (round_number - config["base_round_number"]) % user_count

    assignment = {}
    for rot_no in range(1, user_count + 1):
        idx = (rot_no - 1 - offset) % user_count
        assignment[rot_no] = base_users[idx]

    return assignment


def assign_rotation(round_id: int) -> None:
    """회차에 대한 로테이션을 계산하여 DB에 저장"""
    assignment = calculate_rotation_assignment(round_id)

    with get_connection() as conn, get_cursor(conn) as cursor:
        for rot_no, user_id in assignment.items():
            cursor.execute(
                """
                INSERT INTO rotation_assignments (round_id, user_id, rotation_no)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE rotation_no = VALUES(rotation_no)
                """,
                (round_id, user_id, rot_no),
            )


def get_rotation_for_round(round_id: int) -> list[dict]:
    """특정 회차의 로테이션 배정 조회"""
    with get_connection() as conn, get_cursor(conn) as cursor:
        cursor.execute(
            """
            SELECT ra.rotation_no, u.id AS user_id, u.username, u.display_name
            FROM rotation_assignments ra
            JOIN users u ON ra.user_id = u.id
            WHERE ra.round_id = %s
            ORDER BY ra.rotation_no
            """,
            (round_id,),
        )
        return cursor.fetchall()


# ============================================
# 결과 판정 관련
# ============================================

def evaluate_picks_for_round(round_id: int) -> dict:
    """
    마감된 회차의 경기 결과와 유저 픽을 비교하여 정답 여부를 판정.
    picks.is_correct 업데이트 + round_user_results 요약 저장.

    반환: {user_id: {"total": int, "correct": int, "wrong": int}}
    """
    with get_connection() as conn, get_cursor(conn) as cursor:
        # 경기 결과 조회 (game_id -> result 매핑)
        cursor.execute(
            "SELECT id, game_no, result FROM games WHERE round_id = %s",
            (round_id,),
        )
        games = cursor.fetchall()
        game_result_map = {g["id"]: g["result"] for g in games}

        # 해당 회차의 모든 픽 조회
        cursor.execute(
            "SELECT id, game_id, user_id, pick FROM picks WHERE round_id = %s",
            (round_id,),
        )
        picks = cursor.fetchall()

        # 유저별 성적 집계
        user_stats: dict[int, dict[str, int]] = {}

        for pick in picks:
            game_result = game_result_map.get(pick["game_id"])

            if game_result is None:
                # 결과가 아직 없는 경기는 스킵
                continue

            is_correct = 1 if pick["pick"] == game_result else 0

            # picks.is_correct 업데이트
            cursor.execute(
                "UPDATE picks SET is_correct = %s WHERE id = %s",
                (is_correct, pick["id"]),
            )

            # 유저별 집계
            uid = pick["user_id"]
            if uid not in user_stats:
                user_stats[uid] = {"total": 0, "correct": 0, "wrong": 0}
            user_stats[uid]["total"] += 1
            user_stats[uid]["correct"] += is_correct
            user_stats[uid]["wrong"] += (1 - is_correct)

        # round_user_results 요약 저장
        for uid, stats in user_stats.items():
            cursor.execute(
                """
                INSERT INTO round_user_results (round_id, user_id, total_picks, correct, wrong)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    total_picks = VALUES(total_picks),
                    correct = VALUES(correct),
                    wrong = VALUES(wrong),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (round_id, uid, stats["total"], stats["correct"], stats["wrong"]),
            )

        conn.commit()
        return user_stats
