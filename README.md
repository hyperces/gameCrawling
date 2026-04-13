# gameCrawling

이 폴더가 게임 스케줄 수집과 파이썬 기반 데이터 관리의 정식 실행 경로입니다.

## 포함 범위

- 회차/경기 스케줄 수집
- 회차 오픈/마감 상태 갱신을 위한 배치 실행
- 경기 결과 수집 및 정답 판정
- 리서치 JSON import
- 로테이션 보정 같은 운영용 파이썬 작업

`gameSchedule/crawling`과 `gameSchedule/crawling-standalone`은 더 이상 정식 실행 경로가 아닙니다.

## 로컬 직접 실행

```bat
start-crawling.bat
start-crawling.bat 202604
start-crawling.bat --results-only
```

## 공통 Python 진입점

```powershell
python src/manage.py crawl
python src/manage.py crawl 202604
python src/manage.py results
python src/manage.py research-import --dry-run
python src/manage.py debug-api 202604
python src/manage.py fix-rotation
```

## Docker 실행

```powershell
docker compose run --rm python python manage.py crawl
docker compose run --rm python python manage.py results
docker compose run --rm python python manage.py research-import --dry-run
```

## 서버 배치 예시

```cron
*/5 * * * * cd /path/to/gameCrawling && /bin/bash ./crawl.sh
```

서버 전용 환경변수가 필요하면 `.env.server.example`을 복사해서 `.env.server`로 사용하면 됩니다.
