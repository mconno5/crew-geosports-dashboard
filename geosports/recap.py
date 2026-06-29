from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

from .io import read_scores_csv

SITE_URL = "https://mconno5.github.io/crew-geosports-dashboard/"
CREW_CHAT_ID = "iMessage;+;chat80160861622035091"
RECAP_DAYS = {0, 2, 5}  # Monday, Wednesday, Saturday
TOKEN_TTL_DAYS = 7
MAX_RECAP_CHARS = 900
FORM_MIN_GAMES = 3
MAX_SEND_ATTEMPTS = 3
SEND_TIMEOUT_SECONDS = 60
APPROVAL_RE = re.compile(r"(^|\s)/send\s+([A-Za-z0-9_-]+)(\s|$)")


@dataclass
class RecapPaths:
    root: Path
    data_dir: Path
    dashboard_json: Path
    parsed_csv: Path
    state_json: Path
    recaps_dir: Path
    latest_txt: Path
    latest_json: Path
    env_file: Path
    icloud_dir: Path
    icloud_latest: Path


class RecapSendError(RuntimeError):
    pass


def default_paths(root: Path | None = None) -> RecapPaths:
    root = root or Path(__file__).resolve().parent.parent
    data_dir = root / "data"
    recaps_dir = data_dir / "recaps"
    icloud_dir = (
        Path.home()
        / "Library"
        / "Mobile Documents"
        / "com~apple~CloudDocs"
        / "GeoSports Recaps"
    )
    return RecapPaths(
        root=root,
        data_dir=data_dir,
        dashboard_json=data_dir / "dashboard_data.json",
        parsed_csv=data_dir / "geosports_parsed.csv",
        state_json=data_dir / "recap_state.json",
        recaps_dir=recaps_dir,
        latest_txt=recaps_dir / "latest.txt",
        latest_json=recaps_dir / "latest.json",
        env_file=root / "config" / "recap.local.env",
        icloud_dir=icloud_dir,
        icloud_latest=icloud_dir / "latest.md",
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def recap_config(paths: RecapPaths) -> dict[str, str]:
    config = load_env_file(paths.env_file)
    for key in (
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "GITHUB_TOKEN",
        "GITHUB_REPO",
        "GITHUB_APPROVAL_ISSUE_NUMBER",
    ):
        if os.environ.get(key):
            config[key] = os.environ[key]
    return config


def missing_approval_config(config: dict[str, str]) -> list[str]:
    return [
        key
        for key in ("GITHUB_TOKEN", "GITHUB_REPO", "GITHUB_APPROVAL_ISSUE_NUMBER")
        if not config.get(key)
    ]


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_dashboard(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_score_date(data: dict) -> date | None:
    dates = data.get("dates", [])
    daily_scores = data.get("dailyScores", {})
    latest_index = -1
    for scores in daily_scores.values():
        for idx in range(len(scores) - 1, -1, -1):
            if scores[idx] is not None:
                latest_index = max(latest_index, idx)
                break
    if latest_index < 0 or latest_index >= len(dates):
        return None
    # Dashboard dates omit the year, so prefer meta window endDate when present.
    window = data.get("meta", {}).get("recentFormWindow") or {}
    if window.get("endDate"):
        return date.fromisoformat(window["endDate"])
    year = date.today().year
    month, day = map(int, dates[latest_index].split("-"))
    return date(year, month, day)


def draft_status(draft: dict) -> str:
    if not draft or not draft.get("token"):
        return "none"
    if draft.get("sent_at"):
        return "sent"
    if draft.get("abandoned_at"):
        return "abandoned"
    if draft.get("send_failed_at"):
        return "send_failed"
    if draft.get("approved_at"):
        return "approved"
    return "pending_review"


def draft_blocks_replacement(draft: dict) -> bool:
    return draft_status(draft) in {"pending_review", "approved"}


def concise_error(exc: BaseException) -> str:
    if isinstance(exc, RecapSendError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def is_recap_day(run_date: date) -> bool:
    return run_date.weekday() in RECAP_DAYS


def last_due_recap_day(run_date: date) -> date:
    current = run_date
    for _ in range(7):
        if is_recap_day(current):
            return current
        current -= timedelta(days=1)
    return run_date


def should_draft(
    data: dict,
    state: dict,
    run_date: date | None = None,
    *,
    force: bool = False,
    replace_pending: bool = False,
) -> tuple[bool, str]:
    if force:
        return True, "forced"

    run_date = run_date or date.today()
    due_day = last_due_recap_day(run_date)
    if due_day != run_date and state.get("last_due_day") == due_day.isoformat():
        return False, f"already handled missed recap day {due_day.isoformat()}"
    if not is_recap_day(run_date) and state.get("last_due_day") != due_day.isoformat():
        # Catch up a missed Mon/Wed/Sat when there are new scores.
        pass
    elif not is_recap_day(run_date):
        return False, "not a recap day"

    draft = state.get("draft") or {}
    if draft_blocks_replacement(draft) and not replace_pending:
        return False, "draft already pending"

    meta = data.get("meta", {})
    score_count = meta.get("scoreCount", 0)
    latest = latest_score_date(data)
    latest_iso = latest.isoformat() if latest else None
    if (
        state.get("last_drafted_score_count") == score_count
        and state.get("last_drafted_latest_score_date") == latest_iso
    ):
        return False, "no new scores since last draft"
    return True, "due"


def value_sum(values: list[int | None]) -> int:
    return sum(v for v in values if v is not None)


def avg(values: list[int]) -> int | None:
    return round(mean(values)) if values else None


def build_fact_pack(data: dict) -> dict:
    players = data.get("players", [])
    dates = data.get("dates", [])
    daily_scores = data.get("dailyScores", {})
    meta = data.get("meta", {})
    latest_idx = -1
    for scores in daily_scores.values():
        for idx in range(len(scores) - 1, -1, -1):
            if scores[idx] is not None:
                latest_idx = max(latest_idx, idx)
                break

    latest_day = latest_score_date(data)
    latest_label = dates[latest_idx] if latest_idx >= 0 else None
    latest_scores = []
    for player in players:
        score = daily_scores.get(player["id"], [])[latest_idx] if latest_idx >= 0 else None
        if score is not None:
            latest_scores.append({"name": player["name"], "score": score})
    latest_scores.sort(key=lambda item: item["score"], reverse=True)

    window = meta.get("recentFormWindow") or {}
    window_days = int(window.get("days") or 7)
    form_start = max(0, latest_idx - window_days + 1)
    form_end = latest_idx + 1
    form_rows = []
    for player in players:
        vals = [
            v
            for v in daily_scores.get(player["id"], [])[form_start:form_end]
            if v is not None
        ]
        if not vals:
            continue
        recent_avg = avg(vals)
        form_rows.append(
            {
                "name": player["name"],
                "recent_avg": recent_avg,
                "games": len(vals),
                "season_avg": player["avg"],
                "delta": None if recent_avg is None else recent_avg - player["avg"],
                "best": max(vals),
            }
        )
    form_rows.sort(key=lambda item: (item["recent_avg"], item["games"]), reverse=True)
    rated_rows = [item for item in form_rows if item["games"] >= FORM_MIN_GAMES]
    risers = sorted(rated_rows, key=lambda item: item["delta"], reverse=True)

    return {
        "site_url": SITE_URL,
        "meta": meta,
        "latest_score_date": latest_day.isoformat() if latest_day else None,
        "latest_day_label": latest_label,
        "latest_scores": latest_scores,
        "latest_winner": latest_scores[0] if latest_scores else None,
        "latest_score_count": len(latest_scores),
        "recent_window": window,
        "recent_form": form_rows,
        "rated_recent_form": rated_rows,
        "recent_leader": rated_rows[0] if rated_rows else (form_rows[0] if form_rows else None),
        "biggest_riser": risers[0] if risers else None,
        "most_active": sorted(form_rows, key=lambda item: item["games"], reverse=True)[:3],
    }


def fallback_recap(facts: dict) -> str:
    latest_winner = facts.get("latest_winner")
    recent_leader = facts.get("recent_leader")
    riser = facts.get("biggest_riser")
    window = facts.get("recent_window") or {}
    parts = ["GeoSports desk has the latest:"]
    if latest_winner:
        parts.append(
            f"{latest_winner['name']} took the newest matchday with {latest_winner['score']}."
        )
    if recent_leader:
        parts.append(
            f"Over {window.get('label', 'the last 7 days')}, {recent_leader['name']} is setting the pace at {recent_leader['recent_avg']} across {recent_leader['games']} games."
        )
    if riser and riser.get("delta", 0) > 0:
        parts.append(
            f"{riser['name']} is the stock-up name at +{riser['delta']} vs season form."
        )
    parts.append(f"Full board: {SITE_URL}")
    return " ".join(parts)[:MAX_RECAP_CHARS]


def openai_recap(facts: dict, config: dict[str, str]) -> str:
    api_key = config.get("OPENAI_API_KEY")
    model = config.get("OPENAI_MODEL")
    if not api_key or not model:
        raise RuntimeError("OPENAI_API_KEY and OPENAI_MODEL are required")

    prompt = {
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": (
                    "Write one friendly, witty sports-broadcast group-chat recap. "
                    "Use only these facts, do not invent scores or streaks, avoid profanity and mean personal attacks, "
                    f"include the dashboard link, and stay under {MAX_RECAP_CHARS} characters.\n\n"
                    + json.dumps(facts, ensure_ascii=False, indent=2)
                ),
            }
        ],
    }
    body = json.dumps(
        {
            "model": model,
            "input": [prompt],
            "max_output_tokens": 350,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))
    text = payload.get("output_text")
    if text:
        return text.strip()[:MAX_RECAP_CHARS]

    chunks: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                chunks.append(content["text"])
    if not chunks:
        raise RuntimeError("OpenAI response did not include text")
    return "\n".join(chunks).strip()[:MAX_RECAP_CHARS]


def github_request(config: dict[str, str], path: str, method: str = "GET", body: dict | None = None) -> dict | list:
    token = config.get("GITHUB_TOKEN")
    repo = config.get("GITHUB_REPO")
    if not token or not repo:
        raise RuntimeError("GITHUB_TOKEN and GITHUB_REPO are required")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "geosports-recap-agent",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def approval_issue_number(config: dict[str, str]) -> str:
    issue = config.get("GITHUB_APPROVAL_ISSUE_NUMBER")
    if not issue:
        raise RuntimeError("GITHUB_APPROVAL_ISSUE_NUMBER is required")
    return issue


def write_review_files(paths: RecapPaths, draft_text: str, payload: dict, config: dict[str, str]) -> None:
    paths.recaps_dir.mkdir(parents=True, exist_ok=True)
    paths.icloud_dir.mkdir(parents=True, exist_ok=True)
    paths.latest_txt.write_text(draft_text, encoding="utf-8")
    paths.latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    issue_num = config.get("GITHUB_APPROVAL_ISSUE_NUMBER", "<issue-number>")
    repo = config.get("GITHUB_REPO", "mconno5/crew-geosports-dashboard")
    token = payload["token"]
    review = f"""# GeoSports Recap Draft

Generated: {payload['created_at']}
Token: `{token}`

## Draft

{draft_text}

## Approve from phone

Comment on the approval issue:

```text
/send {token}
```

Approval issue: https://github.com/{repo}/issues/{issue_num}
Dashboard: {SITE_URL}
"""
    paths.icloud_latest.write_text(review, encoding="utf-8")


def post_github_draft_notice(config: dict[str, str], payload: dict, draft_text: str) -> None:
    issue = approval_issue_number(config)
    body = (
        f"New GeoSports recap draft ready.\n\n"
        f"Token: `{payload['token']}`\n\n"
        f"```text\n{draft_text}\n```\n\n"
        f"Approve with:\n\n`/send {payload['token']}`"
    )
    github_request(config, f"/issues/{issue}/comments", method="POST", body={"body": body})


def post_github_send_failure_notice(config: dict[str, str], draft: dict) -> None:
    issue = approval_issue_number(config)
    token = draft.get("token", "<unknown>")
    attempts = draft.get("send_attempt_count", 0)
    error = draft.get("last_send_error", "unknown error")
    body = (
        "GeoSports recap send failed after approval.\n\n"
        f"Token: `{token}`\n"
        f"Attempts: {attempts}\n"
        f"Last error: `{error}`\n\n"
        "The draft is marked failed so future scheduled recaps can continue. "
        "The Mac may need Messages opened/signed in, macOS Automation permission, or iCloud sync attention."
    )
    github_request(config, f"/issues/{issue}/comments", method="POST", body={"body": body})


def draft_recap(args: argparse.Namespace) -> int:
    paths = default_paths()
    config = recap_config(paths)
    missing = missing_approval_config(config)
    if missing:
        message = f"recap approval config missing: {', '.join(missing)}"
        if args.if_due:
            print(f"No recap drafted: {message}")
            return 0
        raise SystemExit(message)

    data = load_dashboard(paths.dashboard_json)
    state = load_state(paths.state_json)
    run_date = date.fromisoformat(args.run_date) if args.run_date else date.today()
    ok, reason = should_draft(
        data,
        state,
        run_date,
        force=args.force,
        replace_pending=args.replace_pending,
    )
    if args.if_due and not ok:
        print(f"No recap drafted: {reason}")
        state["last_due_day"] = last_due_recap_day(run_date).isoformat()
        save_state(paths.state_json, state)
        return 0
    if not ok and not args.force:
        raise SystemExit(f"Refusing to draft recap: {reason}")

    facts = build_fact_pack(data)
    try:
        draft_text = openai_recap(facts, config)
        generator = "openai"
    except Exception as exc:
        draft_text = fallback_recap(facts)
        generator = f"fallback: {exc}"

    token = secrets.token_urlsafe(6)
    created_at = now_utc().isoformat()
    payload = {
        "token": token,
        "created_at": created_at,
        "expires_at": (now_utc() + timedelta(days=TOKEN_TTL_DAYS)).isoformat(),
        "generator": generator,
        "draft_text": draft_text,
        "facts": facts,
    }
    write_review_files(paths, draft_text, payload, config)
    try:
        post_github_draft_notice(config, payload, draft_text)
        github_status = "posted"
    except Exception as exc:
        github_status = f"not posted: {exc}"

    latest = latest_score_date(data)
    state.update(
        {
            "last_due_day": last_due_recap_day(run_date).isoformat(),
            "last_drafted_score_count": data.get("meta", {}).get("scoreCount"),
            "last_drafted_latest_score_date": latest.isoformat() if latest else None,
            "draft": {
                "token": token,
                "created_at": created_at,
                "expires_at": payload["expires_at"],
                "draft_path": str(paths.latest_txt),
                "icloud_path": str(paths.icloud_latest),
                "github_status": github_status,
                "approved_at": None,
                "sent_at": None,
                "approval_comment_id": None,
                "send_attempt_count": 0,
                "last_send_attempt_at": None,
                "last_send_error": None,
                "send_failed_at": None,
                "abandoned_at": None,
                "abandoned_reason": None,
            },
        }
    )
    save_state(paths.state_json, state)
    print(f"Drafted recap token {token}; GitHub notice {github_status}")
    print(f"iCloud review file: {paths.icloud_latest}")
    return 0


def send_message(text: str, chat_id: str = CREW_CHAT_ID) -> None:
    script = """
on run argv
  set messageText to item 1 of argv
  set targetChatId to item 2 of argv
  tell application "Messages"
    activate
    with timeout of 45 seconds
      send messageText to chat id targetChatId
    end timeout
  end tell
end run
"""
    try:
        subprocess.run(
            ["osascript", "-e", script, text, chat_id],
            check=True,
            capture_output=True,
            text=True,
            timeout=SEND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RecapSendError(f"Messages AppleScript timed out after {SEND_TIMEOUT_SECONDS}s") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RecapSendError(detail.splitlines()[-1] if detail else "Messages AppleScript failed") from exc


def record_send_failure(paths: RecapPaths, state: dict, error: str, config: dict[str, str] | None = None) -> dict:
    draft = state.get("draft") or {}
    now = now_utc().isoformat()
    draft["send_attempt_count"] = int(draft.get("send_attempt_count") or 0) + 1
    draft["last_send_attempt_at"] = now
    draft["last_send_error"] = error[:500]
    if draft["send_attempt_count"] >= MAX_SEND_ATTEMPTS:
        draft["send_failed_at"] = now
        if config and not draft.get("send_failure_notice_at"):
            try:
                post_github_send_failure_notice(config, draft)
                draft["send_failure_notice_at"] = now
            except Exception as exc:
                draft["send_failure_notice_error"] = concise_error(exc)[:500]
    state["draft"] = draft
    save_state(paths.state_json, state)
    return draft


def send_recap(args: argparse.Namespace) -> int:
    paths = default_paths()
    state = load_state(paths.state_json)
    draft = state.get("draft") or {}
    token = args.token or draft.get("token")
    if not token or token != draft.get("token"):
        raise SystemExit("No matching draft token found")
    if draft.get("sent_at") and not args.force:
        raise SystemExit("Draft was already sent")
    if draft.get("abandoned_at") and not args.force:
        raise SystemExit("Draft was abandoned")
    if draft.get("send_failed_at") and not args.force:
        raise SystemExit("Draft send previously failed")
    expires = draft.get("expires_at")
    if expires and datetime.fromisoformat(expires) < now_utc() and not args.force:
        raise SystemExit("Draft token expired")
    if int(draft.get("send_attempt_count") or 0) >= MAX_SEND_ATTEMPTS and not args.force:
        raise SystemExit("Draft reached the send retry limit")
    text = paths.latest_txt.read_text(encoding="utf-8").strip()
    if not args.yes:
        print(text)
        confirmation = input(f"Send this recap to The Crew with token {token}? [y/N] ")
        if confirmation.lower() != "y":
            print("Send cancelled")
            return 0
    try:
        send_message(text)
    except Exception as exc:
        config = recap_config(paths)
        failed = record_send_failure(paths, state, concise_error(exc), config)
        message = (
            f"Failed to send recap token {token} "
            f"({failed.get('send_attempt_count')}/{MAX_SEND_ATTEMPTS} attempts): {failed.get('last_send_error')}"
        )
        if args.yes:
            print(message)
            return 0
        raise SystemExit(message) from exc
    sent_at = now_utc().isoformat()
    draft["sent_at"] = sent_at
    if not draft.get("approved_at"):
        draft["approved_at"] = sent_at
    draft["last_send_error"] = None
    state["draft"] = draft
    state["last_sent_token"] = token
    state["last_sent_at"] = sent_at
    save_state(paths.state_json, state)
    print(f"Sent recap token {token}")
    return 0


def approval_comments(config: dict[str, str]) -> list[dict]:
    issue = approval_issue_number(config)
    comments = github_request(config, f"/issues/{issue}/comments?per_page=100")
    return comments if isinstance(comments, list) else []


def poll_approvals(args: argparse.Namespace) -> int:
    paths = default_paths()
    config = recap_config(paths)
    state = load_state(paths.state_json)
    draft = state.get("draft") or {}
    token = draft.get("token")
    if not token:
        print("No pending draft token")
        return 0
    status = draft_status(draft)
    if status == "abandoned":
        print("Latest draft was abandoned")
        return 0
    if status == "send_failed":
        print("Latest draft send failed")
        return 0
    if draft.get("sent_at"):
        print("Latest draft already sent")
        return 0
    expires = draft.get("expires_at")
    if expires and datetime.fromisoformat(expires) < now_utc():
        print("Latest draft token expired")
        return 0

    seen_after = draft.get("created_at", "")
    for comment in approval_comments(config):
        body = comment.get("body") or ""
        match = APPROVAL_RE.search(body)
        if not match or match.group(2) != token:
            continue
        created = comment.get("created_at") or ""
        if seen_after and created < seen_after:
            continue
        draft["approved_at"] = now_utc().isoformat()
        draft["approval_comment_id"] = comment.get("id")
        state["draft"] = draft
        save_state(paths.state_json, state)
        print(f"Found approval comment {comment.get('id')} for token {token}")
        return send_recap(argparse.Namespace(token=token, force=False, yes=True))
    print("No matching approval comment found")
    return 0


def status_recap(args: argparse.Namespace) -> int:
    paths = default_paths()
    state = load_state(paths.state_json)
    data = load_dashboard(paths.dashboard_json) if paths.dashboard_json.exists() else {}
    draft = state.get("draft") or {}
    status = draft_status(draft)
    latest = latest_score_date(data)
    blocked = draft_blocks_replacement(draft)
    print(f"Draft status: {status}")
    print(f"Blocks next draft: {'yes' if blocked else 'no'}")
    print(f"Token: {draft.get('token') or '-'}")
    print(f"Created: {draft.get('created_at') or '-'}")
    print(f"Approved: {draft.get('approved_at') or '-'}")
    print(f"Sent: {draft.get('sent_at') or '-'}")
    print(f"Failed: {draft.get('send_failed_at') or '-'}")
    print(f"Abandoned: {draft.get('abandoned_at') or '-'}")
    print(f"Send attempts: {draft.get('send_attempt_count') or 0}/{MAX_SEND_ATTEMPTS}")
    print(f"Last send error: {draft.get('last_send_error') or '-'}")
    print(f"Latest score date: {latest.isoformat() if latest else '-'}")
    print(f"Last drafted score date: {state.get('last_drafted_latest_score_date') or '-'}")
    print(f"Last due day: {state.get('last_due_day') or '-'}")
    return 0


def abandon_recap(args: argparse.Namespace) -> int:
    paths = default_paths()
    state = load_state(paths.state_json)
    draft = state.get("draft") or {}
    if not draft.get("token"):
        print("No draft to abandon")
        return 0
    if draft.get("sent_at") and not args.force:
        raise SystemExit("Refusing to abandon an already-sent draft without --force")
    if draft.get("abandoned_at") and not args.force:
        print(f"Draft token {draft.get('token')} was already abandoned")
        return 0
    now = now_utc().isoformat()
    draft["abandoned_at"] = now
    draft["abandoned_reason"] = args.reason
    state["draft"] = draft
    save_state(paths.state_json, state)
    print(f"Abandoned recap token {draft.get('token')}: {args.reason}")
    return 0


def add_recap_subparser(subparsers) -> None:
    recap_parser = subparsers.add_parser("recap", help="Draft, approve, and send GeoSports recaps.")
    recap_subparsers = recap_parser.add_subparsers(dest="recap_command")

    draft_parser = recap_subparsers.add_parser("draft", help="Draft a recap for phone review.")
    draft_parser.add_argument("--if-due", action="store_true")
    draft_parser.add_argument("--force", action="store_true")
    draft_parser.add_argument("--replace-pending", action="store_true")
    draft_parser.add_argument("--run-date", help="YYYY-MM-DD override for tests/manual catch-up.")
    draft_parser.set_defaults(func=draft_recap)

    poll_parser = recap_subparsers.add_parser("poll-approvals", help="Poll GitHub for approved recap tokens.")
    poll_parser.set_defaults(func=poll_approvals)

    send_parser = recap_subparsers.add_parser("send", help="Send a saved recap draft.")
    send_parser.add_argument("--token")
    send_parser.add_argument("--force", action="store_true")
    send_parser.add_argument("--yes", action="store_true")
    send_parser.set_defaults(func=send_recap)

    status_parser = recap_subparsers.add_parser("status", help="Show recap draft and automation state.")
    status_parser.set_defaults(func=status_recap)

    abandon_parser = recap_subparsers.add_parser("abandon", help="Abandon the current unsent recap draft.")
    abandon_parser.add_argument("--reason", required=True)
    abandon_parser.add_argument("--force", action="store_true")
    abandon_parser.set_defaults(func=abandon_recap)
