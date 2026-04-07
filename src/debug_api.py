"""API 응답 필드 확인용 디버그 스크립트"""
import json
import sys
sys.path.insert(0, '.')

from batman_crawling import fetch_schedule

ym = sys.argv[1] if len(sys.argv) > 1 else "202603"
print(f"=== {ym} 일정 API 응답 확인 ===\n")

response = fetch_schedule(ym)
schedules = response.get("schedules", {}).get("data", [])

print(f"총 {len(schedules)}개 회차 조회\n")
print("=" * 60)

for item in schedules:
    round_number = str(item.get("gmOsidTs") or item["gmTs"])
    print(f"[회차 {round_number}] 전체 필드:")
    print(json.dumps(item, ensure_ascii=False, indent=2, default=str))
    print("=" * 60)
