from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .aggregate import build_dashboard_data
from .archive import (
    ArchiveValidationError,
    build_manifest,
    manifest_status,
    read_archive_scores,
    reconcile_archive_row,
    sync_archive,
    write_archive,
)
from .backfill import BackfillValidationError, read_backfill_scores
from .config import load_player_config
from .imessage import DB_PATH, MessagesDatabaseError, fetch_messages, resolve_chat_ids
from .io import read_scores_csv, write_raw_csv, write_scores_csv
from .parser import dedupe_scores, parse_messages
from .recap import add_recap_subparser
from .render import render_dashboard

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / "data"
DEFAULT_DIST_DIR = ROOT / "dist"
DEFAULT_PLAYERS = ROOT / "config" / "players.json"
DEFAULT_TEMPLATE = ROOT / "dashboard.html"
DEFAULT_BACKFILL = DEFAULT_DATA_DIR / "geosports_backfill.local.csv"
DEFAULT_ARCHIVE = DEFAULT_DATA_DIR / "geosports_history.local.csv"
DEFAULT_ARCHIVE_MANIFEST = DEFAULT_DATA_DIR / "geosports_history_manifest.local.json"


def parse_chat_ids(values: list[str] | None) -> list[int] | None:
    if not values:
        return None
    ids: list[int] = []
    for value in values:
        ids.extend(int(part.strip()) for part in value.split(",") if part.strip())
    return ids


def load_live_scores(args: argparse.Namespace, player_config: dict):
    """Extract live scores and private reference rows without writing outputs."""
    try:
        chat_ids = resolve_chat_ids(Path(args.db), args.chat_name, parse_chat_ids(args.chat_id))
        raw_messages = fetch_messages(Path(args.db), chat_ids)
    except MessagesDatabaseError as exc:
        raise SystemExit(str(exc)) from exc
    parsed = parse_messages(raw_messages)
    message_scores = dedupe_scores(parsed)
    try:
        backfill_scores = read_backfill_scores(Path(args.backfill), player_config)
    except BackfillValidationError as exc:
        raise SystemExit(f"Invalid private backfill reference: {exc}") from exc
    return raw_messages, parsed, message_scores, backfill_scores


def sync_private_archive(
    archive_path: Path,
    manifest_path: Path,
    message_scores: list,
    backfill_scores: list,
    player_config: dict,
    *,
    dry_run: bool = False,
):
    try:
        archive_rows = read_archive_scores(archive_path, player_config)
        result = sync_archive(archive_rows, message_scores, backfill_scores, player_config)
    except ArchiveValidationError as exc:
        raise SystemExit(f"Invalid private score archive: {exc}") from exc

    should_write = (
        result.changed
        or not archive_path.exists()
        or manifest_status(manifest_path, result.rows, player_config) != "current"
    )
    if should_write and not dry_run:
        write_archive(archive_path, manifest_path, result.rows, player_config)
    return result, should_write


def print_archive_result(result, archive_path: Path, *, dry_run: bool = False) -> None:
    action = "would update" if dry_run else "updated"
    if not result.changed:
        action = "is current"
    print(
        "Private archive: "
        f"existing={result.archive_rows}; "
        f"messages added={result.messages_added}; "
        f"reference added={result.backfill_added}; "
        f"matched={result.matched_rows}; "
        f"conflicts={len(result.conflicts)}"
    )
    print(f"Private archive {action}: {archive_path}")
    for conflict in result.conflicts:
        print(f"WARNING: Archive retained over live source for {conflict}")


def build(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir)
    dist_dir = Path(args.dist_dir)
    player_config = load_player_config(Path(args.players))
    archive_path = Path(args.archive)
    manifest_path = Path(args.archive_manifest)

    if args.input_csv:
        message_scores = read_scores_csv(Path(args.input_csv))
        backfill_scores = []
        raw_messages = []
        raw_count = 0
        parsed_count = len(message_scores)
        message_score_count = len(message_scores)
    else:
        raw_messages, parsed, message_scores, backfill_scores = load_live_scores(args, player_config)
        raw_count = len(raw_messages)
        parsed_count = len(parsed)
        message_score_count = len(message_scores)

    source_result, _ = sync_private_archive(
        archive_path, manifest_path, message_scores, backfill_scores, player_config
    )
    scores = source_result.rows
    if not args.input_csv:
        write_raw_csv(data_dir / "geosports_scores.csv", raw_messages)
    write_scores_csv(data_dir / "geosports_parsed.csv", scores)

    dashboard_data = build_dashboard_data(scores, player_config)
    data_dir.mkdir(parents=True, exist_ok=True)
    data_path = data_dir / "dashboard_data.json"
    data_path.write_text(json.dumps(dashboard_data, ensure_ascii=False, indent=2), encoding="utf-8")

    output_path = dist_dir / "dashboard.html"
    render_dashboard(Path(args.template), output_path, dashboard_data)

    removed = parsed_count - message_score_count
    print(f"Raw GeoSports messages: {raw_count}")
    print(f"Parsed Messages scores: {parsed_count} -> {message_score_count} after dedupe (removed {removed})")
    print_archive_result(source_result, archive_path)
    print(f"Combined dashboard scores: {len(scores)}")
    print(f"Data: {data_path}")
    print(f"Report: {output_path}")


def render(args: argparse.Namespace) -> None:
    player_config = load_player_config(Path(args.players))
    scores = read_scores_csv(Path(args.input_csv))
    dashboard_data = build_dashboard_data(scores, player_config)
    Path(args.data_json).write_text(json.dumps(dashboard_data, ensure_ascii=False, indent=2), encoding="utf-8")
    render_dashboard(Path(args.template), Path(args.output), dashboard_data)
    print(f"Report: {args.output}")


def archive_status(args: argparse.Namespace) -> None:
    player_config = load_player_config(Path(args.players))
    archive_path = Path(args.archive)
    manifest_path = Path(args.archive_manifest)
    try:
        rows = read_archive_scores(archive_path, player_config)
    except ArchiveValidationError as exc:
        raise SystemExit(f"Invalid private score archive: {exc}") from exc

    current_manifest_status = manifest_status(manifest_path, rows, player_config)

    manifest = build_manifest(rows, player_config)
    print(f"Private archive: {archive_path}")
    print(f"Score count: {manifest['scoreCount']}")
    print(f"Date range: {manifest['dateRange']['start']} to {manifest['dateRange']['end']}")
    print("Player counts: " + ", ".join(f"{player}={count}" for player, count in manifest["playerCounts"].items()))
    print(f"Manifest: {current_manifest_status} ({manifest_path})")


def archive_verify(args: argparse.Namespace) -> None:
    player_config = load_player_config(Path(args.players))
    archive_path = Path(args.archive)
    manifest_path = Path(args.archive_manifest)
    try:
        rows = read_archive_scores(archive_path, player_config)
    except ArchiveValidationError as exc:
        raise SystemExit(f"Invalid private score archive: {exc}") from exc
    if not archive_path.exists():
        raise SystemExit(f"Private score archive does not exist: {archive_path}")
    if not manifest_path.exists():
        raise SystemExit(f"Private archive manifest does not exist: {manifest_path}")
    if manifest_status(manifest_path, rows, player_config) != "current":
        raise SystemExit("Private archive manifest checksum does not match the archive")
    print(f"Verified private archive: {len(rows)} scores; checksum matches manifest")


def archive_snapshot(args: argparse.Namespace) -> None:
    player_config = load_player_config(Path(args.players))
    raw_messages, parsed, message_scores, backfill_scores = load_live_scores(args, player_config)
    result, _ = sync_private_archive(
        Path(args.archive),
        Path(args.archive_manifest),
        message_scores,
        backfill_scores,
        player_config,
        dry_run=args.dry_run,
    )
    print(f"Raw GeoSports messages: {len(raw_messages)}")
    print(f"Parsed Messages scores: {len(parsed)} -> {len(message_scores)} after dedupe")
    print_archive_result(result, Path(args.archive), dry_run=args.dry_run)


def archive_reconcile(args: argparse.Namespace) -> None:
    player_config = load_player_config(Path(args.players))
    archive_path = Path(args.archive)
    manifest_path = Path(args.archive_manifest)
    try:
        score_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        archive_rows = read_archive_scores(archive_path, player_config)
        rows = reconcile_archive_row(
            archive_rows,
            args.player_id,
            score_date,
            args.score,
            args.emoji_row,
            player_config,
        )
    except (ArchiveValidationError, ValueError) as exc:
        raise SystemExit(f"Cannot reconcile private score archive: {exc}") from exc
    write_archive(archive_path, manifest_path, rows, player_config)
    print(f"Reconciled private archive for {args.date} player={args.player_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a GeoSports dashboard from iMessage scores.")
    subparsers = parser.add_subparsers(dest="command")

    def add_source_arguments(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--chat-name", default="The Crew")
        command_parser.add_argument("--chat-id", action="append", help="One chat ID or comma-separated chat IDs.")
        command_parser.add_argument("--db", default=str(DB_PATH))
        command_parser.add_argument("--players", default=str(DEFAULT_PLAYERS))
        command_parser.add_argument("--backfill", default=str(DEFAULT_BACKFILL))

    def add_archive_arguments(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--archive", default=str(DEFAULT_ARCHIVE))
        command_parser.add_argument("--archive-manifest", default=str(DEFAULT_ARCHIVE_MANIFEST))

    build_parser = subparsers.add_parser("build", help="Extract, parse, aggregate, and render the dashboard.")
    add_source_arguments(build_parser)
    add_archive_arguments(build_parser)
    build_parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    build_parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    build_parser.add_argument("--dist-dir", default=str(DEFAULT_DIST_DIR))
    build_parser.add_argument("--input-csv", help="Skip iMessage extraction and build from an existing parsed CSV.")
    build_parser.set_defaults(func=build)

    render_parser = subparsers.add_parser("render", help="Render the dashboard from an existing parsed CSV.")
    render_parser.add_argument("input_csv")
    render_parser.add_argument("--players", default=str(DEFAULT_PLAYERS))
    render_parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    render_parser.add_argument("--output", default=str(DEFAULT_DIST_DIR / "dashboard.html"))
    render_parser.add_argument("--data-json", default=str(DEFAULT_DATA_DIR / "dashboard_data.json"))
    render_parser.set_defaults(func=render)

    archive_parser = subparsers.add_parser("archive", help="Manage the private durable score history.")
    archive_commands = archive_parser.add_subparsers(dest="archive_command")

    snapshot_parser = archive_commands.add_parser("snapshot", help="Capture new live scores in the private archive.")
    add_source_arguments(snapshot_parser)
    add_archive_arguments(snapshot_parser)
    snapshot_parser.add_argument("--dry-run", action="store_true", help="Report archive changes without writing files.")
    snapshot_parser.set_defaults(func=archive_snapshot)

    status_parser = archive_commands.add_parser("status", help="Show private archive health and coverage.")
    status_parser.add_argument("--players", default=str(DEFAULT_PLAYERS))
    add_archive_arguments(status_parser)
    status_parser.set_defaults(func=archive_status)

    verify_parser = archive_commands.add_parser("verify", help="Validate private archive and manifest integrity.")
    verify_parser.add_argument("--players", default=str(DEFAULT_PLAYERS))
    add_archive_arguments(verify_parser)
    verify_parser.set_defaults(func=archive_verify)

    reconcile_parser = archive_commands.add_parser("reconcile", help="Explicitly correct one private archive score.")
    reconcile_parser.add_argument("--players", default=str(DEFAULT_PLAYERS))
    add_archive_arguments(reconcile_parser)
    reconcile_parser.add_argument("--date", required=True, help="Score date in YYYY-MM-DD format.")
    reconcile_parser.add_argument("--player-id", required=True, help="Configured public player slug.")
    reconcile_parser.add_argument("--score", required=True, type=int)
    reconcile_parser.add_argument("--emoji-row", required=True, help="Blank or up to five GeoSports answer markers.")
    reconcile_parser.set_defaults(func=archive_reconcile)

    add_recap_subparser(subparsers)

    args = parser.parse_args()
    if not args.command:
        args = parser.parse_args(["build"])
    if args.command == "recap" and not args.recap_command:
        parser.parse_args(["recap", "--help"])
    if args.command == "archive" and not args.archive_command:
        parser.parse_args(["archive", "--help"])
    args.func(args)
