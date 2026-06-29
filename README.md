# GeoSports Report

Builds a GeoSports dashboard from "The Crew" iMessage chat.

## Run

```bash
python3 -m geosports build
```

That command:

1. Finds chats matching `The Crew`.
2. Extracts GeoSports-looking iMessage messages.
3. Parses scores.
4. Applies the dedupe rule: first score per sender/player per day. Same-score ties from different players are allowed starting June 21, 2026, while older dates keep the original duplicate-score protection.
5. Writes CSV/JSON data into `data/`.
6. Writes the final dashboard to `dist/dashboard.html`.

## Files

- `config/players.json`: public player config keyed by slug (display name and color). Contains no phone numbers.
- `config/senders.local.json`: private, gitignored mapping from iMessage sender handles (phone numbers, "Me") to player slugs. Unmapped senders get an anonymous hashed ID so raw handles never reach generated output.
- `data/geosports_scores.csv`: raw matching messages.
- `data/geosports_parsed.csv`: deduped score rows.
- `data/dashboard_data.json`: generated dashboard payload.
- `dist/dashboard.html`: generated shareable report.

## Useful Commands

Build from Messages:

```bash
python3 -m geosports build --chat-name "The Crew"
```

Build the GitHub Pages version:

```bash
./scripts/build_site.sh
```

This writes the public dashboard to `docs/index.html`.

Build from a known chat ID:

```bash
python3 -m geosports build --chat-id 4
```

Render only from an existing parsed CSV:

```bash
python3 -m geosports render data/geosports_parsed.csv
```

Build from an existing parsed CSV:

```bash
python3 -m geosports build --input-csv data/geosports_parsed.csv
```

## macOS Permission

Reading `~/Library/Messages/chat.db` requires Full Disk Access for the terminal app running the command.

Reply and reaction messages are skipped so replies to prior GeoSports posts do not count as new scores.

## GitHub Pages

Recommended Pages settings:

- Repository visibility: public if you want the easiest free Pages setup.
- Pages source: `Deploy from a branch`.
- Branch: `main`.
- Folder: `/docs`.

After the repository exists on GitHub, publish updates with:

```bash
./scripts/build_site.sh
git add docs/index.html docs/.nojekyll
git commit -m "Update GeoSports dashboard"
git push
```

Or use the publish helper, which skips empty commits:

```bash
./scripts/publish_site.sh
```

## Daily Automation

This repo includes a LaunchAgent template at:

```text
launchd/com.mark.geosports-dashboard.plist
```

It runs every day at 1:00 PM and writes logs to:

```text
logs/geosports-dashboard.out.log
logs/geosports-dashboard.err.log
```

Install it with:

```bash
cd ~/crew-geosports-dashboard
mkdir -p ~/Library/LaunchAgents
cp launchd/com.mark.geosports-dashboard.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mark.geosports-dashboard.plist
```

Trigger a manual scheduled run:

```bash
launchctl kickstart gui/$(id -u)/com.mark.geosports-dashboard
```

Unload it:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.mark.geosports-dashboard.plist
```

## Mobile Recap Drafts

The recap agent can draft a short sports-style message after dashboard publishes.
Drafting is limited to Monday, Wednesday, and Saturday when new scores exist.

Local secret/config file:

```text
config/recap.local.env
```

Start from:

```bash
cp config/recap.example.env config/recap.local.env
```

Required values:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=...
GITHUB_TOKEN=...
GITHUB_REPO=mconno5/crew-geosports-dashboard
GITHUB_APPROVAL_ISSUE_NUMBER=...
```

Draft manually:

```bash
./scripts/draft_recap.sh --if-due
```

Review from iPhone via iCloud Drive:

```text
iCloud Drive/GeoSports Recaps/latest.md
```

Approve from phone by commenting on the configured GitHub issue:

```text
/send <token>
```

Poll approvals manually:

```bash
./scripts/poll_recap_approvals.sh
```

Check the current recap state:

```bash
python3 -m geosports recap status
```

The status output includes GitHub/iCloud posting status and warns if a local draft was created but never saved into recap state. iCloud review-file writes are best-effort; GitHub approval comments are the primary mobile approval path.

Send latest local draft directly from the Mac:

```bash
./scripts/send_latest_recap.sh --token <token>
```

If an approved draft gets stuck because Messages fails to send, abandon it so the next normal Monday/Wednesday/Saturday recap can draft fresh data:

```bash
python3 -m geosports recap abandon --reason "stale approved draft after Messages timeout"
```

The approval poller retries failed Messages sends a limited number of times. If Messages keeps timing out, the draft is marked failed, a GitHub issue comment is posted when possible, and future scheduled recaps are no longer blocked by that failed draft.

Install the hourly approval poller:

```bash
cp launchd/com.mark.geosports-recap-approval.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mark.geosports-recap-approval.plist
```

Unload it:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.mark.geosports-recap-approval.plist
```
