"""
22회차 로테이션을 21회차와 동일하게 수정하는 스크립트
- rotation_assignments: 22회차 배정을 21회차와 동일하게 덮어쓰기
- rotation_base_config: 기준 회차를 22로 업데이트 (이후 회차 순환 기준)
"""
import sys
sys.path.insert(0, '.')

from db_manager import get_connection, get_cursor


def get_round_id(round_number: int) -> int:
    with get_connection() as conn, get_cursor(conn) as cursor:
        cursor.execute("SELECT id FROM rounds WHERE round_number = %s", (round_number,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"{round_number}회차를 DB에서 찾을 수 없습니다.")
        return row["id"]


def get_rotation(round_id: int) -> list[dict]:
    with get_connection() as conn, get_cursor(conn) as cursor:
        cursor.execute("""
            SELECT ra.rotation_no, ra.user_id, u.display_name
            FROM rotation_assignments ra
            JOIN users u ON ra.user_id = u.id
            WHERE ra.round_id = %s
            ORDER BY ra.rotation_no
        """, (round_id,))
        return cursor.fetchall()


def print_rotation(round_number: int, rotation: list[dict]):
    print(f"\n[{round_number}회차 로테이션]")
    for r in rotation:
        print(f"  rot{r['rotation_no']}: {r['display_name']} (user_id={r['user_id']})")


def apply_rotation(round_id: int, rotation: list[dict]):
    """특정 회차에 로테이션 배정 적용 (기존 데이터 삭제 후 재삽입)"""
    with get_connection() as conn, get_cursor(conn) as cursor:
        # 기존 데이터 삭제 (유니크 키 충돌 방지)
        cursor.execute("DELETE FROM rotation_assignments WHERE round_id = %s", (round_id,))
        # 새 데이터 삽입
        for r in rotation:
            cursor.execute("""
                INSERT INTO rotation_assignments (round_id, user_id, rotation_no)
                VALUES (%s, %s, %s)
            """, (round_id, r["user_id"], r["rotation_no"]))


def update_base_config(base_round_number: int, rotation: list[dict]):
    """rotation_base_config 기준 회차 업데이트"""
    with get_connection() as conn, get_cursor(conn) as cursor:
        for r in rotation:
            cursor.execute("""
                UPDATE rotation_base_config
                SET base_round_number = %s
                WHERE rotation_no = %s
            """, (base_round_number, r["rotation_no"]))


# ─────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────
print("=== 21회차 → 22회차 로테이션 수정 ===")

round21_id = get_round_id(21)
round22_id = get_round_id(22)

rotation21 = get_rotation(round21_id)
rotation22_before = get_rotation(round22_id)

print_rotation(21, rotation21)
print_rotation(22, rotation22_before)

if not rotation21:
    print("\n오류: 21회차 로테이션 데이터가 없습니다.")
    sys.exit(1)

confirm = input("\n22회차 로테이션을 21회차와 동일하게 수정하시겠습니까? (y/n): ")
if confirm.strip().lower() != "y":
    print("취소되었습니다.")
    sys.exit(0)

# 22회차 rotation_assignments 업데이트
apply_rotation(round22_id, rotation21)

# rotation_base_config 기준을 22회차로 업데이트
update_base_config(22, rotation21)

# 결과 확인
rotation22_after = get_rotation(round22_id)
print_rotation(22, rotation22_after)
print("\n수정 완료! 23회차 이후 회차도 22회차 기준으로 정상 순환됩니다.")
