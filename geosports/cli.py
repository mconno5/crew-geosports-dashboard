from __future__ import annotations

import argparse
import json
from pathlib import Path

from .aggregate import build_dashboard_data
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


def parse_chat_ids(values: list[str] | None) -> list[int] | None:
    if not values:
        return None
    ids: list[int] = []
    for value in values:
        ids.extend(int(part.strip()) for part in value.split(",") if part.strip())
    return ids


def build(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir)
    dist_dir = Path(args.dist_dir)
    player_config = load_player_config(Path(args.players))

    if args.input_csv:
        scores = read_scores_csv(Path(args.input_csv))
        raw_count = 0
        parsed_count = len(scores)
    else:
        try:
            chat_ids = resolve_chat_ids(Path(args.db), args.chat_name, parse_chat_ids(args.chat_id))
            raw_messages = fetch_messages(Path(args.db), chat_ids)
        except MessagesDatabaseError as exc:
            raise SystemExit(str(exc)) from exc
        parsed = parse_messages(raw_messages)
        scores = dedupe_scores(parsed)
        raw_count = len(raw_messages)
        parsed_count = len(parsed)
        write_raw_csv(data_dir / "geosports_scores.csv", raw_messages)
        write_scores_csv(data_dir / "geosports_parsed.csv", scores)

    dashboard_data = build_dashboard_data(scores, player_config)
    data_dir.mkdir(parents=True, exist_ok=True)
    data_path = data_dir / "dashboard_data.json"
    data_path.write_text(json.dumps(dashboard_data, ensure_ascii=False, indent=2), encoding="utf-8")

    output_path = dist_dir / "dashboard.html"
    render_dashboard(Path(args.template), output_path, dashboard_data)

    removed = parsed_count - len(scores)
    print(f"Raw GeoSports messages: {raw_count}")
    print(f"Parsed scores: {parsed_count} -> {len(scores)} after dedupe (removed {removed})")
    print(f"Data: {data_path}")
    print(f"Report: {output_path}")


def render(args: argparse.Namespace) -> None:
    player_config = load_player_config(Path(args.players))
    scores = read_scores_csv(Path(args.input_csv))
    dashboard_data = build_dashboard_data(scores, player_config)
    Path(args.data_json).write_text(json.dumps(dashboard_data, ensure_ascii=False, indent=2), encoding="utf-8")
    render_dashboard(Path(args.template), Path(args.output), dashboard_data)
    print(f"Report: {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a GeoSports dashboard from iMessage scores.")
    subparsers = parser.add_subparsers(dest="command")

    build_parser = subparsers.add_parser("build", help="Extract, parse, aggregate, and render the dashboard.")
    build_parser.add_argument("--chat-name", default="The Crew")
    build_parser.add_argument("--chat-id", action="append", help="One chat ID or comma-separated chat IDs.")
    build_parser.add_argument("--db", default=str(DB_PATH))
    build_parser.add_argument("--players", default=str(DEFAULT_PLAYERS))
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

    add_recap_subparser(subparsers)

    args = parser.parse_args()
    if not args.command:
        args = parser.parse_args(["build"])
    if args.command == "recap" and not args.recap_command:
        parser.parse_args(["recap", "--help"])
    args.func(args)
