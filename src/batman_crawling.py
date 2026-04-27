"""Betman Toto round/game crawler and DB sync."""

from __future__ import annotations

import random
import ssl
import sys
import time
from calendar import monthrange
from datetime import datetime

import requests
import urllib3
from fake_useragent import UserAgent
from requests.adapters import HTTPAdapter

from config import DEFAULT_GM_ID, GAME_INFO_URL, SCHEDULE_URL
from db_manager import assign_rotation, get_round_by_gm_ts, upsert_games, upsert_round

# Disable warnings because the upstream site requires legacy TLS handling.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class BetmanSSLAdapter(HTTPAdapter):
    """HTTP adapter with legacy TLS compatibility for Betman."""

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
            print(f"  [retry {attempt}/{max_retries - 1}] waiting {wait:.1f}s...")
            time.sleep(wait)

        try:
            session = get_session()
            response = session.post(url, **kwargs)
            response.raise_for_status()
            return response
        except (requests.exceptions.ConnectionError, requests.exceptions.SSLError) as exc:
            last_error = exc
            print(f"  [connection error] {type(exc).__name__}: retry scheduled")

    raise last_error


def get_current_ym(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return now.strftime("%Y%m")


def get_prev_ym(now: datetime | None = None) -> str:
    now = now or datetime.now()
    if now.month == 1:
        return f"{now.year - 1}12"
    return f"{now.year}{now.month - 1:02d}"


def get_next_ym(now: datetime | None = None) -> str:
    now = now or datetime.now()
    if now.month == 12:
        return f"{now.year + 1}01"
    return f"{now.year}{now.month + 1:02d}"


def is_last_week_of_month(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    last_day = monthrange(now.year, now.month)[1]
    last_date = now.replace(day=last_day)
    last_week_start_day = last_day - last_date.weekday()
    return now.day >= last_week_start_day


def get_default_crawl_ym_list(now: datetime | None = None) -> list[str]:
    now = now or datetime.now()
    ym_list = [get_current_ym(now)]
    if is_last_week_of_month(now):
        ym_list.append(get_next_ym(now))
    return ym_list


def build_headers(referer: str) -> dict[str, str]:
    useragent = UserAgent()
    return {
        "referer": referer,
        "User-Agent": useragent.chrome,
    }


def fetch_schedule(ym: str | None = None) -> dict:
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
    del ym

    now = datetime.now()
    sale_start_ms = item.get("saleStartDate")
    sale_end_ms = item.get("saleEndDate")

    sale_start = datetime.fromtimestamp(sale_start_ms / 1000) if sale_start_ms else None
    sale_end = datetime.fromtimestamp(sale_end_ms / 1000) if sale_end_ms else None

    if sale_start is not None and now < sale_start:
        return "upcoming"

    if sale_end is not None and now >= sale_end:
        return "closed"

    if sale_start is not None or sale_end is not None:
        return "open"

    if not item.get("saleProgress", False):
        return "closed"

    return "open"


def parse_sale_datetime(date_str: str | None) -> datetime | None:
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
    result_code = game_data.get("gameResult") or game_data.get("resultCode")
    if result_code is None:
        return None

    result_map = {
        "1": "W",
        "0": "D",
        "2": "L",
        "W": "W",
        "D": "D",
        "L": "L",
        "\uc2b9": "W",
        "\ubb34": "D",
        "\ud328": "L",
    }
    return result_map.get(str(result_code).strip())


def crawl_and_save(ym: str | None = None) -> None:
    ym = ym or get_current_ym()
    print(f"[{datetime.now()}] === crawl start: {ym} ===")

    schedule_response = fetch_schedule(ym)
    schedules = schedule_response.get("schedules", {}).get("data", [])

    if not schedules:
        print("  no schedules found.")
        return

    for item in schedules:
        gm_ts = int(item["gmTs"])
        round_number = str(item.get("gmOsidTs") or item["gmTs"])
        status = determine_round_status(item, ym)

        existing = get_round_by_gm_ts(gm_ts)
        if existing and existing["status"] == "closed" and existing["result_saved"]:
            print(f"  round {round_number} (gm_ts={gm_ts}): results already saved, skip")
            continue

        sale_start_ms = item.get("saleStartDate")
        sale_end_ms = item.get("saleEndDate")
        sale_start = datetime.fromtimestamp(sale_start_ms / 1000) if sale_start_ms else None
        sale_end = datetime.fromtimestamp(sale_end_ms / 1000) if sale_end_ms else None

        round_id = upsert_round(
            gm_ts=gm_ts,
            gm_id=DEFAULT_GM_ID,
            round_number=round_number,
            ym=ym,
            status=status,
            sale_start=sale_start,
            sale_end=sale_end,
        )
        print(f"  round {round_number} (gm_ts={gm_ts}): round_id={round_id}, status={status}")

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
            print(f"    -> saved {len(games_data)} games")
        except Exception as exc:
            print(f"    -> failed to fetch games: {exc}")
            continue

        try:
            assign_rotation(round_id)
            print("    -> rotation assigned")
        except Exception as exc:
            print(f"    -> failed to assign rotation: {exc}")

    print(f"[{datetime.now()}] === crawl finished ===")


def main() -> None:
    ym = sys.argv[1] if len(sys.argv) > 1 else None

    if ym:
        crawl_and_save(ym)
    else:
        for crawl_ym in get_default_crawl_ym_list():
            crawl_and_save(crawl_ym)


if __name__ == "__main__":
    main()
