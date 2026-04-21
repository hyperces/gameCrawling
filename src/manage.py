from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent


def run_script(script_name: str, args: list[str]) -> int:
    command = [sys.executable, str(SRC_DIR / script_name), *args]
    completed = subprocess.run(command, cwd=str(SRC_DIR))
    return completed.returncode


def print_help() -> None:
    print(
        "\n".join(
            [
                "gameCrawling Python task runner",
                "",
                "Usage:",
                "  python src/manage.py crawl [YYYYMM]",
                "  python src/manage.py results",
                "  python src/manage.py debug-api [YYYYMM]",
                "  python src/manage.py research-import [args...]",
                "  python src/manage.py aggregate-stats [options]",
                "  python src/manage.py fix-rotation",
                "",
                "Examples:",
                "  python src/manage.py crawl",
                "  python src/manage.py crawl 202604",
                "  python src/manage.py results",
                "  python src/manage.py aggregate-stats --year 2026",
                "  python src/manage.py research-import --dry-run",
            ]
        )
    )


def parse_crawl_args(args: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python src/manage.py crawl")
    parser.add_argument("ym", nargs="?")
    return parser.parse_args(args)


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])

    if not args or args[0] in {"-h", "--help", "help"}:
        print_help()
        return 0

    command, *extra = args

    if command == "crawl":
        parsed = parse_crawl_args(extra)
        script_args: list[str] = []
        if parsed.ym:
            script_args.append(parsed.ym)
        return run_script("batman_crawling.py", script_args)

    if command == "results":
        return run_script("save_game_results_to_db.py", extra)

    if command == "debug-api":
        return run_script("debug_api.py", extra)

    if command == "research-import":
        return run_script("research_importer.py", extra)

    if command == "aggregate-stats":
        return run_script("aggregate_round_user_results.py", extra)

    if command == "fix-rotation":
        return run_script("fix_rotation.py", extra)

    print(f"[ERROR] Unknown command: {command}", file=sys.stderr)
    print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
