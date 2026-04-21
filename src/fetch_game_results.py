"""베트맨 결과 상세 API 호출 테스트 스크립트"""

from __future__ import annotations

import json
import random
import ssl
import sys
import time
from typing import Any

import requests
import urllib3
from fake_useragent import UserAgent
from requests.adapters import HTTPAdapter


WIN_RESULT_DETAIL_URL = "https://www.betman.co.kr/gamebuy/winrst/inqWinrstDetlBody.do"
DEFAULT_GM_ID = "G011"
DEFAULT_GM_TS = "260022"

# 베트맨 사이트 SSL 인증서 경고 비활성화
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class BetmanSSLAdapter(HTTPAdapter):
    """베트맨 사이트의 TLS 호환성 문제를 해결하기 위한 커스텀 SSL 어댑터"""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        ctx.options |= ssl.OP_NO_SSLv2 if hasattr(ssl, "OP_NO_SSLv2") else 0
        ctx.options |= ssl.OP_NO_SSLv3 if hasattr(ssl, "OP_NO_SSLv3") else 0
        if hasattr(ssl, "OP_IGNORE_UNEXPECTED_EOF"):
            ctx.options |= ssl.OP_IGNORE_UNEXPECTED_EOF
        if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1
        except (AttributeError, ValueError):
            pass
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def get_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", BetmanSSLAdapter())
    return session


def post_with_retry(url: str, max_retries: int = 3, **kwargs) -> requests.Response:
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
        except (requests.exceptions.ConnectionError, requests.exceptions.SSLError) as error:
            last_error = error
            print(f"  [연결 오류] {type(error).__name__}: 재시도 예정")
    raise last_error


def build_headers(referer: str) -> dict[str, str]:
    useragent = UserAgent()
    return {
        "referer": referer,
        "User-Agent": useragent.chrome,
    }


def fetch_game_results(gm_ts: str | int, gm_id: str = DEFAULT_GM_ID) -> dict[str, Any]:
    gm_ts_str = str(gm_ts)
    headers = build_headers(
        "https://www.betman.co.kr/main/mainPage/gamebuy/winrst/inqWinrstList.do"
        f"?gmId={gm_id}&gmTs={gm_ts_str}"
    )
    payload = {
        "draw": 1,
        "start": 1,
        "perPage": -1,
        "searchValue": "",
        "mbrNum": "",
        "gmId": gm_id,
        "gmTs": gm_ts_str,
        "_sbmInfo": {
            "debugMode": "false",
        },
    }
    response = post_with_retry(
        WIN_RESULT_DETAIL_URL,
        json=payload,
        headers=headers,
        timeout=30,
        verify=False,
    )
    return response.json()


def summarize_value(value: Any) -> str:
    if isinstance(value, dict):
        return f"object(keys={list(value.keys())[:8]})"
    if isinstance(value, list):
        return f"array(len={len(value)})"
    return repr(value)


def print_match_summary(response: dict[str, Any]) -> None:
    detail_rows = response.get("detlBody", [])
    result_codes = {
        item.get("code"): item.get("value")
        for item in response.get("winrstCode", [])
        if isinstance(item, dict)
    }

    print("[경기 결과 요약]")
    if not detail_rows:
        print("- detlBody 데이터가 없습니다.")
        return

    for item in detail_rows:
        seq = item.get("GM_SEQ")
        home_team = str(item.get("HM_TEAM_NM", "")).strip()
        away_team = str(item.get("AW_TEAM_NM", "")).strip()
        home_score = item.get("HM_TEAM_MCH_RSLT_VAL")
        away_score = item.get("AW_TEAM_MCH_RSLT_VAL")
        result_code = item.get("TOTO_RSLT_VAL")
        result_label = result_codes.get(result_code, result_code)
        match_time = item.get("MCH_DTM")
        print(
            f"- {seq}경기 {home_team} {home_score}:{away_score} {away_team} | "
            f"결과={result_label} | 일시={match_time}"
        )


def main() -> None:
    gm_ts = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GM_TS

    print(f"=== 게임 결과 API 테스트: gmTs={gm_ts} ===\n")
    response = fetch_game_results(gm_ts)

    print("[루트 필드 요약]")
    for key, value in response.items():
        print(f"- {key}: {summarize_value(value)}")

    print()
    print_match_summary(response)

    print("\n[전체 JSON]")
    print(json.dumps(response, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
