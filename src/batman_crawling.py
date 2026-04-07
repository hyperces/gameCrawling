"""베트맨 토토 경기 데이터 수집, 결과 판정 및 DB 저장"""

from __future__ import annotations

import ssl
import sys
import time
import random
from datetime import datetime

import urllib3
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from fake_useragent import UserAgent

# 베트맨 사이트 SSL 인증서 경고 비활성화
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class BetmanSSLAdapter(HTTPAdapter):
    """베트맨 사이트의 TLS 호환성 문제를 해결하기 위한 커스텀 SSL 어댑터"""
    def init_poolmanager(self, *args, **kwargs):
        # ssl.SSLContext 직접 생성 (Python 3.13 호환)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # 보안 수준 0: 레거시 서버 최대 호환
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        # 오래된 서버와의 TLS 협상 허용
        ctx.options |= ssl.OP_NO_SSLv2 if hasattr(ssl, "OP_NO_SSLv2") else 0
        ctx.options |= ssl.OP_NO_SSLv3 if hasattr(ssl, "OP_NO_SSLv3") else 0
        if hasattr(ssl, "OP_IGNORE_UNEXPECTED_EOF"):
            ctx.options |= ssl.OP_IGNORE_UNEXPECTED_EOF
        if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        # TLS 최소 버전을 1.0으로 낮춤 (레거시 서버 대응)
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1
        except (AttributeError, ValueError):
            pass
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def get_session() -> requests.Session:
    """SSL 어댑터가 적용된 requests 세션 생성"""
    session = requests.Session()
    session.mount("https://", BetmanSSLAdapter())
    return session


def post_with_retry(url: str, max_retries: int = 3, **kwargs) -> requests.Response:
    """연결 오류 시 새 세션으로 재시도하는 POST 요청"""
    last_error = None
    for attempt in range(max_retries):
        if attempt > 0:
            wait = (2 ** attempt) + random.uniform(1, 3)
            print(f"  [재시도 {attempt}/{max_retries - 1}] {wait:.1f}초 대기 후 재시도...")
            time.sleep(wait)
        try:
            session = get_session()
            response = session.post(url, **kwargs)
            response.raise_for_status()
            return response
        except (requests.exceptions.ConnectionError, requests.exceptions.SSLError) as e:
            last_error = e
            print(f"  [연결 오류] {type(e).__name__}: 재시도 예정")
    raise last_error

from config import SCHEDULE_URL, GAME_INFO_URL, GAME_RESULT_URL, DEFAULT_GM_ID
from db_manager import (
    upsert_round,
    upsert_games,
    assign_rotation,
    get_round_by_gm_ts,
    update_round_status,
    update_game_result,
    get_closed_rounds_without_results,
    mark_round_result_saved,
    evaluate_picks_for_round,
)


def get_current_ym() -> str:
    return datetime.now().strftime("%Y%m")


def get_prev_ym() -> str:
    """전달 년월 반환 (예: 202604 → 202603)"""
    now = datetime.now()
    if now.month == 1:
        return f"{now.year - 1}12"
    return f"{now.year}{now.month - 1:02d}"


def build_headers(referer: str) -> dict[str, str]:
    useragent = UserAgent()
    return {
        "referer": referer,
        "User-Agent": useragent.chrome,
    }


def fetch_schedule(ym: str | None = None) -> dict:
    """베트맨 일정 목록 조회"""
    ym = ym or get_current_ym()
    headers = build_headers(
        "https://www.betman.co.kr/main/mainPage/gamebuy/gameScheduleList.do"
        "?sbx_gmType=&sbx_gmKind=G011,G999&sbx_gmLeag=&sbx_gmTeam="
        f"&gmSports=SC&yearMonth={ym}&state=list&curPage=1&perPage=10&isIFR="
    )
    payload = {
        "draw": 1,
        "start": 1,
        "perPage": 10,
        "searchValue": "",
        "gmId": DEFAULT_GM_ID,
        "league": "",
        "team": "",
        "yearMonth": ym,
        "_sbmInfo": {
            "debugMode": "false",
        },
    }
    response = post_with_retry(SCHEDULE_URL, json=payload, headers=headers, timeout=30, verify=False)
    return response.json()


def fetch_game_info(gm_ts: int) -> dict:
    """특정 회차의 경기 상세 정보 조회"""
    headers = build_headers(
        "https://www.betman.co.kr/main/mainPage/gamebuy/closedGameSlip.do"
        f"?frameType=typeA&gmId=G101&gmTs={gm_ts}"
    )
    payload = {
        "gmId": DEFAULT_GM_ID,
        "gmTs": gm_ts,
        "gameYear": "",
        "_sbmInfo": {
            "_sbmInfo": {
                "debugMode": "false",
            }
        },
    }
    response = post_with_retry(GAME_INFO_URL, json=payload, headers=headers, timeout=30, verify=False)
    return response.json()


def determine_round_status(item: dict, ym: str) -> str:
    """회차 상태 결정 (saleProgress + saleEndDate 기반)"""
    # saleProgress: False 이면 발매 종료
    if not item.get("saleProgress", False):
        return "closed"
    # saleEndDate (Unix ms)가 현재 시각보다 이전이면 마감
    sale_end_ms = item.get("saleEndDate")
    if sale_end_ms:
        sale_end = datetime.fromtimestamp(sale_end_ms / 1000)
        if sale_end < datetime.now():
            return "closed"
    return "open"


def parse_sale_datetime(date_str: str | None) -> datetime | None:
    """발매 시작/종료 일시 파싱"""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y%m%d%H%M%S")
    except ValueError:
        try:
            return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def parse_game_result(game_data: dict) -> str | None:
    """
    경기 결과 파싱 (홈 기준 승/무/패).
    베트맨 API 응답의 결과 필드에서 추출.
    """
    # 베트맨 API의 결과 필드명은 실제 응답 확인 후 조정 필요
    result_code = game_data.get("gameResult") or game_data.get("resultCode")

    if result_code is None:
        return None

    result_str = str(result_code).strip()

    # 베트맨 결과 코드 매핑
    # "1" 또는 "승" = 홈 승리
    # "0" 또는 "무" = 무승부
    # "2" 또는 "패" = 홈 패배
    result_map = {
        "1": "W", "승": "W", "W": "W",
        "0": "D", "무": "D", "D": "D",
        "2": "L", "패": "L", "L": "L",
    }
    return result_map.get(result_str)


# ============================================
# 메인 크롤링: 일정 수집 + DB 저장
# ============================================

def crawl_and_save(ym: str | None = None) -> None:
    """일정을 크롤링하여 DB에 저장하는 메인 함수"""
    ym = ym or get_current_ym()
    print(f"[{datetime.now()}] === 크롤링 시작: {ym} ===")

    schedule_response = fetch_schedule(ym)
    schedules = schedule_response.get("schedules", {}).get("data", [])

    if not schedules:
        print("  조회된 일정이 없습니다.")
        return

    for item in schedules:
        gm_ts = int(item["gmTs"])
        round_number = str(item.get("gmOsidTs") or item["gmTs"])
        status = determine_round_status(item, ym)

        # 이미 마감 + 결과저장 완료된 회차는 스킵
        existing = get_round_by_gm_ts(gm_ts)
        if existing and existing["status"] == "closed" and existing["result_saved"]:
            print(f"  회차 {round_number} (gm_ts={gm_ts}): 결과 저장 완료, 스킵")
            continue

        sale_start_ms = item.get("saleStartDate")
        sale_end_ms = item.get("saleEndDate")
        sale_start = datetime.fromtimestamp(sale_start_ms / 1000) if sale_start_ms else None
        sale_end = datetime.fromtimestamp(sale_end_ms / 1000) if sale_end_ms else None

        # 회차 저장
        round_id = upsert_round(
            gm_ts=gm_ts,
            gm_id=DEFAULT_GM_ID,
            round_number=round_number,
            ym=ym,
            status=status,
            sale_start=sale_start,
            sale_end=sale_end,
        )
        print(f"  회차 {round_number} (gm_ts={gm_ts}): round_id={round_id}, status={status}")

        # 경기 정보 조회 및 저장
        try:
            game_info = fetch_game_info(gm_ts)
            games_list = game_info.get("schedulesList", [])

            games_data = []
            for idx, game in enumerate(games_list, start=1):
                games_data.append({
                    "game_no": idx,
                    "league": game.get("leagueName", ""),
                    "home_team": game.get("homeName", ""),
                    "away_team": game.get("awayName", ""),
                    "game_date": game.get("gameDateStr") or None,
                })

            upsert_games(round_id, games_data)
            print(f"    -> {len(games_data)}개 경기 저장 완료")

            # 마감된 회차면 경기 결과도 함께 저장
            if status == "closed":
                result_count = 0
                for idx, game in enumerate(games_list, start=1):
                    result = parse_game_result(game)
                    if result:
                        update_game_result(round_id, idx, result)
                        result_count += 1
                if result_count > 0:
                    print(f"    -> {result_count}개 경기 결과 저장")

        except Exception as e:
            print(f"    -> 경기 정보 조회 실패: {e}")
            continue

        # 로테이션 배정
        try:
            assign_rotation(round_id)
            print(f"    -> 로테이션 배정 완료")
        except Exception as e:
            print(f"    -> 로테이션 배정 실패: {e}")

    print(f"[{datetime.now()}] === 크롤링 완료 ===")


# ============================================
# 결과 판정: 마감 회차 결과 수집 + 정답 판정
# ============================================

def process_results() -> None:
    """마감된 회차의 결과를 수집하고 유저 픽 정답 여부를 판정"""
    print(f"\n[{datetime.now()}] === 결과 판정 시작 ===")

    pending_rounds = get_closed_rounds_without_results()

    if not pending_rounds:
        print("  판정 대기 중인 회차가 없습니다.")
        return

    for round_info in pending_rounds:
        round_id = round_info["id"]
        gm_ts = round_info["gm_ts"]
        round_number = round_info["round_number"]

        print(f"  회차 {round_number} (gm_ts={gm_ts}) 결과 처리 중...")

        # 경기 결과 재조회 (마감 후 결과가 나왔을 수 있으므로)
        try:
            game_info = fetch_game_info(gm_ts)
            games_list = game_info.get("schedulesList", [])

            all_results_available = True
            result_count = 0

            for idx, game in enumerate(games_list, start=1):
                result = parse_game_result(game)
                if result:
                    update_game_result(round_id, idx, result)
                    result_count += 1
                else:
                    all_results_available = False

            print(f"    -> {result_count}/{len(games_list)}개 경기 결과 확인")

            if not all_results_available:
                print(f"    -> 아직 모든 결과가 나오지 않음, 다음 실행에서 재시도")
                continue

        except Exception as e:
            print(f"    -> 결과 조회 실패: {e}")
            continue

        # 유저 픽 정답 판정
        try:
            user_stats = evaluate_picks_for_round(round_id)

            for uid, stats in user_stats.items():
                print(
                    f"    -> user_id={uid}: "
                    f"{stats['correct']}/{stats['total']} 맞음 "
                    f"({stats['wrong']}개 틀림)"
                )

            # 결과 저장 완료 표시
            mark_round_result_saved(round_id)
            print(f"    -> 결과 판정 완료!")

        except Exception as e:
            print(f"    -> 정답 판정 실패: {e}")

    print(f"[{datetime.now()}] === 결과 판정 완료 ===")


# ============================================
# 메인 실행
# ============================================

def main() -> None:
    ym = None

    if len(sys.argv) > 1:
        if sys.argv[1] == "--results-only":
            # 결과 판정만 실행
            process_results()
            return
        else:
            ym = sys.argv[1]

    if ym:
        # 특정 월 지정된 경우 해당 월만 크롤링
        crawl_and_save(ym)
    else:
        # 기본: 전달 + 이번 달 순서로 크롤링 (누락 방지)
        crawl_and_save(get_prev_ym())
        crawl_and_save(get_current_ym())

    # 결과 판정 실행 (마감된 회차가 있으면)
    process_results()


if __name__ == "__main__":
    main()
