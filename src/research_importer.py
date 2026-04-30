from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from db_manager import get_connection, get_cursor


RESEARCH_ROOT = Path(__file__).resolve().parent.parent / "research"
DEFAULT_INPUT_ROOT = RESEARCH_ROOT / "inbox"
DEFAULT_FILE_PATTERN = "*_match_research_data.json"


@dataclass
class ImportStats:
    files_processed: int = 0
    rounds_matched: int = 0
    reports_upserted: int = 0
    warnings: int = 0
    errors: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import per-game research JSON files into the game_reports table."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_ROOT),
        help="JSON file or directory to import. Default: research/inbox",
    )
    parser.add_argument(
        "--pattern",
        default=DEFAULT_FILE_PATTERN,
        help=f"Glob pattern when --input is a directory. Default: {DEFAULT_FILE_PATTERN}",
    )
    parser.add_argument(
        "--round",
        dest="round_number",
        help="Only import records for the given round_number.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print actions without writing to the database.",
    )
    parser.add_argument(
        "--strict-team-match",
        action="store_true",
        help="Fail the record when league/home/away team names do not match DB values.",
    )
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return "".join(str(value).strip().lower().split())


def texts_compatible(left: Any, right: Any) -> bool:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm and not right_norm:
        return True
    if left_norm == right_norm:
        return True
    return left_norm in right_norm or right_norm in left_norm


def load_json_records(file_path: Path) -> list[dict[str, Any]]:
    data = json.loads(file_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError(f"{file_path} is not a JSON array")
    records: list[dict[str, Any]] = []
    for idx, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{file_path} item #{idx} is not an object")
        records.append(item)
    return records


def discover_files(input_path: Path, pattern: str) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if not input_path.exists():
        raise FileNotFoundError(f"Input path not found: {input_path}")
    return sorted(path for path in input_path.rglob(pattern) if path.is_file())


def get_round_row(cursor, round_number: str) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT id, round_number, ym, status
        FROM rounds
        WHERE round_number = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (round_number,),
    )
    return cursor.fetchone()


def get_game_rows_by_round(cursor, round_id: int) -> dict[int, dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, game_no, league, home_team, away_team, game_date
        FROM games
        WHERE round_id = %s
        ORDER BY game_no
        """,
        (round_id,),
    )
    return {int(row["game_no"]): row for row in cursor.fetchall()}


def compare_game_info(db_game: dict[str, Any], report_record: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    checks = [
        ("league", db_game.get("league"), report_record.get("league"), normalize_text),
        ("home_team", db_game.get("home_team"), report_record.get("home_team"), texts_compatible),
        ("away_team", db_game.get("away_team"), report_record.get("away_team"), texts_compatible),
    ]
    for field_name, db_value, report_value, matcher in checks:
        is_match = (
            matcher(db_value) == matcher(report_value)
            if matcher is normalize_text
            else matcher(db_value, report_value)
        )
        if not is_match:
            mismatches.append(
                f"{field_name} mismatch: DB='{db_value}' / JSON='{report_value}'"
            )
    return mismatches


def upsert_game_report(
    cursor,
    *,
    round_id: int,
    game_id: int,
    game_no: int,
    league: str,
    home_team: str,
    away_team: str,
    kickoff_at: str | None,
    analysis_summary: str,
    report_json: str,
    source_file: str,
) -> None:
    cursor.execute(
        """
        INSERT INTO game_reports (
            round_id,
            game_id,
            game_no,
            league,
            home_team,
            away_team,
            kickoff_at,
            analysis_summary,
            report_json,
            source_file
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            round_id = VALUES(round_id),
            game_no = VALUES(game_no),
            league = VALUES(league),
            home_team = VALUES(home_team),
            away_team = VALUES(away_team),
            kickoff_at = VALUES(kickoff_at),
            analysis_summary = VALUES(analysis_summary),
            report_json = VALUES(report_json),
            source_file = VALUES(source_file),
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            round_id,
            game_id,
            game_no,
            league,
            home_team,
            away_team,
            kickoff_at,
            analysis_summary,
            report_json,
            source_file,
        ),
    )


def import_file(
    cursor,
    file_path: Path,
    *,
    stats: ImportStats,
    filter_round_number: str | None,
    dry_run: bool,
    strict_team_match: bool,
) -> None:
    records = load_json_records(file_path)
    if not records:
        print(f"[SKIP] {file_path} -> no records")
        return

    stats.files_processed += 1
    print(f"[FILE] {file_path} ({len(records)} records)")

    round_cache: dict[str, dict[str, Any] | None] = {}
    games_cache: dict[int, dict[int, dict[str, Any]]] = {}
    matched_rounds_in_file: set[int] = set()

    for record in records:
        round_number = str(record.get("round_number", "")).strip()
        if not round_number:
            print(f"  [WARN] missing round_number in {file_path.name}")
            stats.warnings += 1
            continue

        if filter_round_number and round_number != filter_round_number:
            continue

        if round_number not in round_cache:
            round_cache[round_number] = get_round_row(cursor, round_number)

        round_row = round_cache[round_number]
        if not round_row:
            print(f"  [WARN] round_number={round_number} not found in rounds table")
            stats.warnings += 1
            continue

        round_id = int(round_row["id"])
        matched_rounds_in_file.add(round_id)

        if round_id not in games_cache:
            games_cache[round_id] = get_game_rows_by_round(cursor, round_id)

        game_no = int(record.get("game_no", 0) or 0)
        if game_no <= 0:
            print(f"  [WARN] invalid game_no in round {round_number}")
            stats.warnings += 1
            continue

        db_game = games_cache[round_id].get(game_no)
        if not db_game:
            print(f"  [WARN] round={round_number} game_no={game_no} not found in games table")
            stats.warnings += 1
            continue

        if strict_team_match:
            mismatches = compare_game_info(db_game, record)
            if mismatches:
                message = "; ".join(mismatches)
                print(f"  [WARN] round={round_number} game_no={game_no} skipped: {message}")
                stats.warnings += 1
                continue

        analysis_summary = str(record.get("analysis_summary") or "").strip()
        if not analysis_summary:
            print(f"  [WARN] round={round_number} game_no={game_no} has empty analysis_summary")
            stats.warnings += 1

        try:
            source_file = str(file_path.relative_to(RESEARCH_ROOT.parent))
        except ValueError:
            source_file = str(file_path)
        if dry_run:
            print(
                f"  [DRY-RUN] round={round_number} game_no={game_no} -> "
                f"game_id={db_game['id']} summary_len={len(analysis_summary)}"
            )
            continue

        upsert_game_report(
            cursor,
            round_id=round_id,
            game_id=int(db_game["id"]),
            game_no=game_no,
            league=str(record.get("league") or db_game.get("league") or ""),
            home_team=str(record.get("home_team") or db_game.get("home_team") or ""),
            away_team=str(record.get("away_team") or db_game.get("away_team") or ""),
            kickoff_at=(str(record.get("kickoff_at")).strip() if record.get("kickoff_at") else None),
            analysis_summary=analysis_summary,
            report_json=json.dumps(record, ensure_ascii=False),
            source_file=source_file,
        )
        print(f"  [OK] round={round_number} game_no={game_no} -> game_id={db_game['id']}")
        stats.reports_upserted += 1

    stats.rounds_matched += len(matched_rounds_in_file)


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).resolve()

    try:
        files = discover_files(input_path, args.pattern)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1

    if not files:
        print(f"[ERROR] No files found for {input_path}")
        return 1

    stats = ImportStats()

    try:
        with get_connection() as conn, get_cursor(conn) as cursor:
            for file_path in files:
                try:
                    import_file(
                        cursor,
                        file_path,
                        stats=stats,
                        filter_round_number=args.round_number,
                        dry_run=args.dry_run,
                        strict_team_match=args.strict_team_match,
                    )
                except Exception as exc:
                    stats.errors += 1
                    print(f"[ERROR] {file_path}: {exc}")
    except Exception as exc:
        print(f"[ERROR] DB operation failed: {exc}")
        return 1

    print(
        "[SUMMARY] "
        f"files={stats.files_processed}, "
        f"rounds={stats.rounds_matched}, "
        f"upserted={stats.reports_upserted}, "
        f"warnings={stats.warnings}, "
        f"errors={stats.errors}"
    )

    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())
