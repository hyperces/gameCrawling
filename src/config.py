"""프로젝트 설정 파일
유저/로테이션 설정은 DB에서 관리합니다.
"""

import os
from pathlib import Path


# ============================================
# .env 파일 자동 로드 (어디서 실행하든 동작)
# ============================================
def _load_env() -> None:
    """crawling/.env 파일을 자동으로 읽어 환경변수에 적용"""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())

_load_env()


# ============================================
# 베트맨 API 설정
# ============================================
SCHEDULE_URL = "https://www.betman.co.kr/buyPsblGame/schedule.do"
GAME_INFO_URL = "https://www.betman.co.kr/buyPsblGame/gameInfoInq.do"
GAME_RESULT_URL = "https://www.betman.co.kr/buyPsblGame/gameInfoInq.do"
DEFAULT_GM_ID = "G011"

# ============================================
# DB 설정 (환경변수 우선, 없으면 기본값)
# ============================================
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DB_PORT", 3306)),
    "database": os.environ.get("DB_NAME", "game_schedule"),
    "user": os.environ.get("DB_USER", "game_user"),
    "password": os.environ.get("DB_PASS", "game_pass1234"),
    "charset": "utf8mb4",
}
