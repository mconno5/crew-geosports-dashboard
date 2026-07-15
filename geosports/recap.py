from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import subprocess
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

from .imessage import DB_PATH, mac_time_to_datetime, message_text

SITE_URL = "https://mconno5.github.io/crew-geosports-dashboard/"
CREW_CHAT_ID = "iMessage;+;chat80160861622035091"
RECAP_DAYS = {0, 2, 4, 6}  # Monday, Wednesday, Friday, Sunday
PREVIEW_TTL_HOURS = 48
MAX_RECAP_CHARS = 450
FORM_MIN_GAMES = 3
MAX_SEND_ATTEMPTS = 3
SEND_TIMEOUT_SECONDS = 60

VOICE_PROFILES = (
    {
        "id": "hot_desk",
        "instruction": "Be forceful and decisive, like an original live studio hot-take host. Deliver one sharp verdict without shouting.",
    },
    {
        "id": "chicago_columnist",
        "instruction": "Be dry, wry, and compact, like an original Chicago sports columnist. Use one precise jab, never cruelty.",
    },
    {
        "id": "color_analyst",
        "instruction": "Be vivid and celebratory, like an original color analyst. Make the result feel like a moment without exaggerating it.",
    },
)


@dataclass
class RecapPaths:
    root: Path
    data_dir: Path
    dashboard_json: Path
    state_json: Path
    recaps_dir: Path
    latest_txt: Path
    latest_json: Path
    env_file: Path


class RecapSendError(RuntimeError):
    pass


def default_paths(root: Path | None = None) -> RecapPaths:
    root = root or Path(__file__).resolve().parent.parent
    data_dir = root / "data"
    recaps_dir = data_dir / "recaps"
    return RecapPaths(
        root=root,
        data_dir=data_dir,
        dashboard_json=data_dir / "dashboard_data.json",
        state_json=data_dir / "recap_state.json",
        recaps_dir=recaps_dir,
        latest_txt=recaps_dir / "latest.txt",
        latest_json=recaps_dir / "latest.json",
        env_file=root / "config" / "recap.local.env",
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
    for key in ("OPENAI_API_KEY", "OPENAI_MODEL", "RECAP_APPROVAL_HANDLE"):
        if os.environ.get(key):
            config[key] = os.environ[key]
    return config


def missing_approval_config(config: dict[str, str]) -> list[str]:
    return [key for key in ("RECAP_APPROVAL_HANDLE",) if not config.get(key)]


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_dashboard(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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
    window = data.get("meta", {}).get("recentFormWindow") or {}
    if window.get("endDate"):
        return date.fromisoformat(window["endDate"])
    month, day = map(int, dates[latest_index].split("-"))
    return date.today().replace(month=month, day=day)


def draft_status(draft: dict) -> str:
    if not draft or not draft.get("token"):
        return "none"
    if draft.get("sent_at"):
        return "sent"
    if draft.get("discarded_at"):
        return "discarded"
    if draft.get("expired_at"):
        return "expired"
    if draft.get("abandoned_at"):
        return "abandoned"
    if draft.get("send_failed_at"):
        return "send_failed"
    if draft.get("approved_at"):
        return "approved"
    if draft.get("preview_send_failed_at"):
        return "preview_send_failed"
    return "pending_review"


def draft_blocks_replacement(draft: dict) -> bool:
    return draft_status(draft) in {"pending_review", "approved"}


def concise_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


def latest_local_payload(paths: RecapPaths) -> dict:
    try:
        return json.loads(paths.latest_json.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def is_recap_day(run_date: date) -> bool:
    return run_date.weekday() in RECAP_DAYS


def voice_profile_for(run_date: date) -> dict[str, str]:
    return VOICE_PROFILES[(run_date.isocalendar().week + run_date.weekday()) % len(VOICE_PROFILES)]


def migrate_legacy_draft(state: dict) -> bool:
    """Close an old GitHub/iCloud review draft so it cannot block direct review."""
    draft = state.get("draft") or {}
    if not draft.get("token") or draft.get("approval_channel") == "imessage":
        return False
    if draft_status(draft) in {"sent", "abandoned", "send_failed"}:
        return False
    draft["abandoned_at"] = now_utc().isoformat()
    draft["abandoned_reason"] = "Legacy GitHub/iCloud approval flow replaced by direct iMessage approval"
    state["draft"] = draft
    state["legacy_review_migrated_at"] = now_utc().isoformat()
    return True


def expire_draft_if_needed(state: dict) -> bool:
    draft = state.get("draft") or {}
    expires_at = parse_iso(draft.get("expires_at"))
    if draft_blocks_replacement(draft) and expires_at and expires_at <= now_utc():
        draft["expired_at"] = now_utc().isoformat()
        draft["expired_reason"] = "Preview expired before a direct approval reply"
        state["draft"] = draft
        return True
    return False


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
    if not is_recap_day(run_date):
        return False, "not a recap day"
    draft = state.get("draft") or {}
    if draft_blocks_replacement(draft) and not replace_pending:
        return False, "draft already pending"
    score_count = data.get("meta", {}).get("scoreCount", 0)
    latest = latest_score_date(data)
    latest_iso = latest.isoformat() if latest else None
    if (
        state.get("last_drafted_score_count") == score_count
        and state.get("last_drafted_latest_score_date") == latest_iso
    ):
        return False, "no new scores since last draft"
    return True, "due"


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
    latest_scores = []
    for player in players:
        values = daily_scores.get(player["id"], [])
        score = values[latest_idx] if 0 <= latest_idx < len(values) else None
        if score is not None:
            latest_scores.append({"name": player["name"], "score": score})
    latest_scores.sort(key=lambda item: item["score"], reverse=True)
    top_score = latest_scores[0]["score"] if latest_scores else None
    latest_winners = [item for item in latest_scores if item["score"] == top_score]
    window = meta.get("recentFormWindow") or {}
    window_days = int(window.get("days") or 7)
    form_start = max(0, latest_idx - window_days + 1)
    form_rows = []
    for player in players:
        values = [v for v in daily_scores.get(player["id"], [])[form_start : latest_idx + 1] if v is not None]
        if not values:
            continue
        recent_avg = avg(values)
        form_rows.append({
            "name": player["name"], "recent_avg": recent_avg, "games": len(values),
            "season_avg": player["avg"], "delta": recent_avg - player["avg"], "best": max(values),
        })
    form_rows.sort(key=lambda item: (item["recent_avg"], item["games"]), reverse=True)
    rated_rows = [item for item in form_rows if item["games"] >= FORM_MIN_GAMES]
    risers = sorted(rated_rows, key=lambda item: item["delta"], reverse=True)
    return {
        "site_url": SITE_URL,
        "latest_score_date": latest_score_date(data).isoformat() if latest_idx >= 0 else None,
        "latest_day_label": dates[latest_idx] if latest_idx >= 0 else None,
        "latest_scores": latest_scores,
        "latest_winner": latest_scores[0] if latest_scores else None,
        "latest_winners": latest_winners,
        "latest_is_tie": len(latest_winners) > 1,
        "recent_window": window,
        "recent_leader": rated_rows[0] if rated_rows else (form_rows[0] if form_rows else None),
        "biggest_riser": risers[0] if risers else None,
    }


def fallback_recap(facts: dict, voice: dict[str, str] | None = None) -> str:
    winners = facts.get("latest_winners") or []
    leader = facts.get("recent_leader")
    riser = facts.get("biggest_riser")
    if len(winners) > 1:
        opening = f"{ ' and '.join(item['name'] for item in winners) } split the latest crown at {winners[0]['score']}; no tiebreaker, no fake controversy."
    elif winners:
        opening = f"{winners[0]['name']} took the latest slate with {winners[0]['score']} and left the board chasing."
    else:
        opening = "No fresh slate to call, so the form table gets the spotlight."
    parts = [opening]
    if leader:
        parts.append(f"Over {facts.get('recent_window', {}).get('label', 'the last seven days')}, {leader['name']} leads at {leader['recent_avg']} across {leader['games']} games.")
    if riser and riser.get("delta", 0) > 0 and (not leader or riser["name"] != leader["name"]):
        parts.append(f"{riser['name']} is the mover, up {riser['delta']:+d} versus season pace.")
    parts.append(SITE_URL)
    return " ".join(parts)[:MAX_RECAP_CHARS]


def openai_recap(facts: dict, config: dict[str, str], voice: dict[str, str]) -> str:
    api_key, model = config.get("OPENAI_API_KEY"), config.get("OPENAI_MODEL")
    if not api_key or not model:
        raise RuntimeError("OPENAI_API_KEY and OPENAI_MODEL are required")
    prompt = (
        "Write a GeoSports group-chat recap from only the fact JSON below. "
        f"{voice['instruction']} "
        "Use exactly two short sentences plus the dashboard URL as a third line. Target 280-430 characters total. "
        "Sentence one must name the latest winner, or every co-winner for a tie, and include the score. "
        "Sentence two must name the seven-day leader and, if available, one riser. "
        "No headings, hashtags, emoji, generic greeting, sign-off, profanity, personal attacks, invented facts, or imitation of a real person. "
        "Avoid these worn phrases: scoreboard has opinions, shared crown alert, form belt, market mover, keep the scores coming, fans. "
        "If latest_is_tie is true, call it a tie plainly and never imply one co-winner beat another.\n\nFACTS:\n"
        + json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    )
    body = json.dumps({"model": model, "input": prompt, "max_output_tokens": 160}).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses", data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))
    text = payload.get("output_text")
    if not text:
        chunks = [content.get("text", "") for item in payload.get("output", []) for content in item.get("content", []) if content.get("type") in {"output_text", "text"}]
        text = "\n".join(chunk for chunk in chunks if chunk)
    text = (text or "").strip()
    if not text:
        raise RuntimeError("OpenAI response did not include text")
    return text[:MAX_RECAP_CHARS]


def write_local_draft(paths: RecapPaths, text: str, payload: dict) -> None:
    paths.recaps_dir.mkdir(parents=True, exist_ok=True)
    paths.latest_txt.write_text(text, encoding="utf-8")
    paths.latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def send_message(text: str, chat_id: str = CREW_CHAT_ID) -> None:
    script = '''on run argv
set messageText to item 1 of argv
set targetChatId to item 2 of argv
tell application "Messages"
  activate
  with timeout of 45 seconds
    send messageText to chat id targetChatId
  end timeout
end tell
end run'''
    run_applescript(script, [text, chat_id])


def send_preview_message(text: str, handle: str) -> None:
    script = '''on run argv
set messageText to item 1 of argv
set targetHandle to item 2 of argv
tell application "Messages"
  activate
  set targetService to first service whose service type = iMessage
  set targetBuddy to buddy targetHandle of targetService
  with timeout of 45 seconds
    send messageText to targetBuddy
  end timeout
end tell
end run'''
    run_applescript(script, [text, handle])


def run_applescript(script: str, args: list[str]) -> None:
    try:
        subprocess.run(["osascript", "-e", script, *args], check=True, capture_output=True, text=True, timeout=SEND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise RecapSendError(f"Messages AppleScript timed out after {SEND_TIMEOUT_SECONDS}s") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RecapSendError(detail.splitlines()[-1] if detail else "Messages AppleScript failed") from exc


def preview_envelope(draft_text: str, token: str) -> str:
    return (
        "GeoSports recap preview\n\n"
        f"{draft_text}\n\n"
        "Reply APPROVE to send this exact recap to The Crew.\n"
        "Reply SKIP to discard it.\n"
        f"Ref: {token}"
    )


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
    data, state = load_dashboard(paths.dashboard_json), load_state(paths.state_json)
    changed = migrate_legacy_draft(state) or expire_draft_if_needed(state)
    if changed:
        save_state(paths.state_json, state)
    run_date = date.fromisoformat(args.run_date) if args.run_date else date.today()
    ok, reason = should_draft(data, state, run_date, force=args.force, replace_pending=args.replace_pending)
    if not ok:
        if args.if_due:
            print(f"No recap drafted: {reason}")
            return 0
        raise SystemExit(f"Refusing to draft recap: {reason}")
    facts, voice = build_fact_pack(data), voice_profile_for(run_date)
    try:
        draft_text, generator = openai_recap(facts, config, voice), "openai"
    except Exception as exc:
        draft_text, generator = fallback_recap(facts, voice), f"fallback: {concise_error(exc)}"
    created = now_utc()
    token = secrets.token_urlsafe(6)
    payload = {"token": token, "created_at": created.isoformat(), "expires_at": (created + timedelta(hours=PREVIEW_TTL_HOURS)).isoformat(), "generator": generator, "voice": voice["id"], "draft_text": draft_text, "facts": facts}
    write_local_draft(paths, draft_text, payload)
    latest = latest_score_date(data)
    draft = {
        "token": token, "created_at": payload["created_at"], "expires_at": payload["expires_at"],
        "draft_path": str(paths.latest_txt), "approval_channel": "imessage", "approval_handle": config["RECAP_APPROVAL_HANDLE"],
        "preview_sent_at": None, "preview_send_attempt_count": 0, "preview_send_error": None, "preview_send_failed_at": None,
        "approved_at": None, "approval_message_rowid": None, "approval_message_guid": None,
        "discarded_at": None, "sent_at": None, "send_attempt_count": 0, "last_send_attempt_at": None,
        "last_send_error": None, "send_failed_at": None, "abandoned_at": None,
    }
    state.update({"last_drafted_score_count": data.get("meta", {}).get("scoreCount"), "last_drafted_latest_score_date": latest.isoformat() if latest else None, "draft": draft})
    save_state(paths.state_json, state)
    try:
        send_preview_message(preview_envelope(draft_text, token), config["RECAP_APPROVAL_HANDLE"])
        draft["preview_sent_at"] = now_utc().isoformat()
    except Exception as exc:
        draft["preview_send_attempt_count"] = 1
        draft["preview_send_error"] = concise_error(exc)
    state["draft"] = draft
    save_state(paths.state_json, state)
    print(f"Drafted recap token {token}; direct Messages preview {'sent' if draft.get('preview_sent_at') else 'failed'}")
    return 0


def approval_messages(handle: str, since: datetime | None, db_path: Path = DB_PATH) -> list[dict]:
    if not db_path.exists():
        raise RecapSendError(f"Cannot find Messages database at {db_path}")
    normalized = "".join(char for char in handle if char.isdigit())
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError as exc:
        raise RecapSendError("Unable to open Messages database; grant Full Disk Access to the poller terminal") from exc
    try:
        rows = conn.execute("""
            SELECT m.ROWID, m.guid, m.date, m.text, m.attributedBody, m.is_from_me, COALESCE(h.id, '')
            FROM message m
            JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            JOIN chat c ON c.ROWID = cmj.chat_id
            LEFT JOIN handle h ON h.ROWID = m.handle_id
            WHERE c.chat_identifier = ?
            ORDER BY m.date ASC
        """, (handle,)).fetchall()
    finally:
        conn.close()
    messages = []
    for rowid, guid, raw_date, text, attributed, is_from_me, sender in rows:
        timestamp = mac_time_to_datetime(raw_date)
        body = message_text(text, attributed)
        if is_from_me or not timestamp or not body:
            continue
        if "".join(char for char in sender if char.isdigit()) != normalized:
            continue
        if since and timestamp <= since:
            continue
        messages.append({"rowid": rowid, "guid": guid, "timestamp": timestamp, "text": body.strip()})
    return messages


def record_send_failure(paths: RecapPaths, state: dict, error: str) -> dict:
    draft = state["draft"]
    draft["send_attempt_count"] = int(draft.get("send_attempt_count") or 0) + 1
    draft["last_send_attempt_at"] = now_utc().isoformat()
    draft["last_send_error"] = error[:500]
    if draft["send_attempt_count"] >= MAX_SEND_ATTEMPTS:
        draft["send_failed_at"] = now_utc().isoformat()
    save_state(paths.state_json, state)
    return draft


def send_recap(args: argparse.Namespace) -> int:
    paths, state = default_paths(), load_state(default_paths().state_json)
    draft = state.get("draft") or {}
    token = args.token or draft.get("token")
    if not token or token != draft.get("token"):
        raise SystemExit("No matching draft token found")
    status = draft_status(draft)
    if status in {"sent", "discarded", "expired", "abandoned", "send_failed"} and not args.force:
        raise SystemExit(f"Draft cannot be sent while status is {status}")
    if parse_iso(draft.get("expires_at")) and parse_iso(draft.get("expires_at")) < now_utc() and not args.force:
        raise SystemExit("Draft token expired")
    if int(draft.get("send_attempt_count") or 0) >= MAX_SEND_ATTEMPTS and not args.force:
        raise SystemExit("Draft reached the send retry limit")
    text = paths.latest_txt.read_text(encoding="utf-8").strip()
    if not args.yes:
        print(text)
        if input(f"Send this recap to The Crew with token {token}? [y/N] ").lower() != "y":
            print("Send cancelled")
            return 0
    try:
        send_message(text)
    except Exception as exc:
        failed = record_send_failure(paths, state, concise_error(exc))
        print(f"Failed to send recap token {token} ({failed['send_attempt_count']}/{MAX_SEND_ATTEMPTS} attempts): {failed['last_send_error']}")
        return 0
    sent_at = now_utc().isoformat()
    draft["sent_at"] = sent_at
    draft["last_send_error"] = None
    state.update({"draft": draft, "last_sent_token": token, "last_sent_at": sent_at})
    save_state(paths.state_json, state)
    print(f"Sent recap token {token}")
    return 0


def retry_preview_if_needed(paths: RecapPaths, state: dict) -> bool:
    draft = state["draft"]
    if draft.get("preview_sent_at") or int(draft.get("preview_send_attempt_count") or 0) >= MAX_SEND_ATTEMPTS:
        return False
    try:
        send_preview_message(preview_envelope(paths.latest_txt.read_text(encoding="utf-8").strip(), draft["token"]), draft["approval_handle"])
        draft["preview_sent_at"] = now_utc().isoformat()
        draft["preview_send_error"] = None
        save_state(paths.state_json, state)
        return True
    except Exception as exc:
        draft["preview_send_attempt_count"] = int(draft.get("preview_send_attempt_count") or 0) + 1
        draft["preview_send_error"] = concise_error(exc)
        if draft["preview_send_attempt_count"] >= MAX_SEND_ATTEMPTS:
            draft["preview_send_failed_at"] = now_utc().isoformat()
        save_state(paths.state_json, state)
        return False


def poll_approvals(args: argparse.Namespace) -> int:
    paths, state = default_paths(), load_state(default_paths().state_json)
    if migrate_legacy_draft(state) or expire_draft_if_needed(state):
        save_state(paths.state_json, state)
    draft = state.get("draft") or {}
    status = draft_status(draft)
    if status in {"none", "sent", "discarded", "expired", "abandoned", "send_failed"}:
        print(f"No actionable direct review ({status})")
        return 0
    if status == "preview_send_failed":
        print("Direct Messages preview failed after retry limit")
        return 0
    if not draft.get("preview_sent_at"):
        retry_preview_if_needed(paths, state)
        print("Retried direct Messages preview")
        return 0
    try:
        replies = approval_messages(draft["approval_handle"], parse_iso(draft["preview_sent_at"]))
    except Exception as exc:
        print(f"Could not check direct Messages approval: {concise_error(exc)}")
        return 0
    for reply in replies:
        command = reply["text"].strip().lower()
        if command not in {"approve", "skip"}:
            continue
        if draft.get("approval_message_rowid") == reply["rowid"]:
            continue
        draft["approval_message_rowid"] = reply["rowid"]
        draft["approval_message_guid"] = reply["guid"]
        draft["approval_reply_at"] = reply["timestamp"].isoformat()
        if command == "skip":
            draft["discarded_at"] = now_utc().isoformat()
            state["draft"] = draft
            save_state(paths.state_json, state)
            print(f"Discarded recap token {draft['token']} from direct Messages reply")
            return 0
        draft["approved_at"] = now_utc().isoformat()
        state["draft"] = draft
        save_state(paths.state_json, state)
        print(f"Received direct Messages approval for token {draft['token']}")
        return send_recap(argparse.Namespace(token=draft["token"], force=False, yes=True))
    print("No direct Messages approve/skip reply found")
    return 0


def status_recap(args: argparse.Namespace) -> int:
    paths, state = default_paths(), load_state(default_paths().state_json)
    changed = migrate_legacy_draft(state) or expire_draft_if_needed(state)
    if changed:
        save_state(paths.state_json, state)
    draft = state.get("draft") or {}
    data = load_dashboard(paths.dashboard_json) if paths.dashboard_json.exists() else {}
    local = latest_local_payload(paths)
    print(f"Draft status: {draft_status(draft)}")
    print(f"Blocks next draft: {'yes' if draft_blocks_replacement(draft) else 'no'}")
    print(f"Token: {draft.get('token') or '-'}")
    print(f"Approval channel: {draft.get('approval_channel') or '-'}")
    print(f"Preview sent: {draft.get('preview_sent_at') or '-'}")
    print(f"Preview attempts: {draft.get('preview_send_attempt_count') or 0}/{MAX_SEND_ATTEMPTS}")
    print(f"Preview error: {draft.get('preview_send_error') or '-'}")
    print(f"Approved: {draft.get('approved_at') or '-'}")
    print(f"Sent: {draft.get('sent_at') or '-'}")
    print(f"Discarded: {draft.get('discarded_at') or '-'}")
    print(f"Expired: {draft.get('expired_at') or '-'}")
    print(f"Send attempts: {draft.get('send_attempt_count') or 0}/{MAX_SEND_ATTEMPTS}")
    print(f"Last send error: {draft.get('last_send_error') or '-'}")
    print(f"Latest score date: {latest_score_date(data).isoformat() if data and latest_score_date(data) else '-'}")
    if local.get("token") and local.get("token") != draft.get("token"):
        print(f"Orphan local draft detected: {local['token']}")
    return 0


def abandon_recap(args: argparse.Namespace) -> int:
    paths, state = default_paths(), load_state(default_paths().state_json)
    draft = state.get("draft") or {}
    if not draft.get("token"):
        print("No draft to abandon")
        return 0
    if draft.get("sent_at") and not args.force:
        raise SystemExit("Refusing to abandon an already-sent draft without --force")
    draft["abandoned_at"] = now_utc().isoformat()
    draft["abandoned_reason"] = args.reason
    state["draft"] = draft
    save_state(paths.state_json, state)
    print(f"Abandoned recap token {draft['token']}: {args.reason}")
    return 0


def add_recap_subparser(subparsers) -> None:
    recap_parser = subparsers.add_parser("recap", help="Draft, review, and send GeoSports recaps.")
    commands = recap_parser.add_subparsers(dest="recap_command")
    draft = commands.add_parser("draft", help="Draft a recap and send a private Messages preview.")
    draft.add_argument("--if-due", action="store_true")
    draft.add_argument("--force", action="store_true")
    draft.add_argument("--replace-pending", action="store_true")
    draft.add_argument("--run-date", help="YYYY-MM-DD override for testing.")
    draft.set_defaults(func=draft_recap)
    poll = commands.add_parser("poll-approvals", help="Poll direct Messages replies for approve or skip.")
    poll.set_defaults(func=poll_approvals)
    send = commands.add_parser("send", help="Send a saved recap draft to The Crew.")
    send.add_argument("--token")
    send.add_argument("--force", action="store_true")
    send.add_argument("--yes", action="store_true")
    send.set_defaults(func=send_recap)
    status = commands.add_parser("status", help="Show recap draft and automation state.")
    status.set_defaults(func=status_recap)
    abandon = commands.add_parser("abandon", help="Abandon the current unsent recap draft.")
    abandon.add_argument("--reason", required=True)
    abandon.add_argument("--force", action="store_true")
    abandon.set_defaults(func=abandon_recap)
