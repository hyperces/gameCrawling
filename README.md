# Crawling Standalone

베트맨 크롤링 로직만 분리해 둔 임시 독립 폴더입니다.

## 포함 파일

- `src/`: 크롤링 및 DB 처리 코드
- `requirements.txt`: Python 패키지 목록
- `start-crawling.bat`: 로컬 실행 스크립트
- `sql/schema.sql`: 필요한 DB 스키마
- `docker-compose.yml`, `docker/Dockerfile`: Docker 실행용

## 빠른 실행

1. `.env.example`을 복사해서 `.env` 생성
2. `start-crawling.bat` 실행

## Docker 실행

`.env` 준비 후 아래 명령 실행:

```powershell
docker compose run --rm python
```
