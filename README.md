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

The recap agent drafts a compact, fact-grounded sports update after a successful dashboard publish. Drafting runs only Monday, Wednesday, Friday, and Sunday, when new scores exist.

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
RECAP_APPROVAL_HANDLE=+1YOURMOBILENUMBER
```

Draft manually:

```bash
./scripts/draft_recap.sh --if-due
```

The Mac sends the preview privately to `RECAP_APPROVAL_HANDLE` in Messages. Review it on the phone and reply with exactly one of:

```text
APPROVE
SKIP
```

`APPROVE` sends the exact saved recap to The Crew from the Mac. `SKIP` discards it. The approval reply must be sent after the preview; it is recorded so it cannot send twice.

Poll approvals manually:

```bash
./scripts/poll_recap_approvals.sh
```

Check the current recap state:

```bash
python3 -m geosports recap status
```

The status output includes the private Messages preview and send status. Drafts expire after 48 hours so an unanswered preview does not block the next scheduled recap.

Send latest local draft directly from the Mac:

```bash
./scripts/send_latest_recap.sh --token <token>
```

If an approved draft gets stuck because Messages fails to send, abandon it so the next normal Monday/Wednesday/Friday/Sunday recap can draft fresh data:

```bash
python3 -m geosports recap abandon --reason "stale approved draft after Messages timeout"
```

The approval poller checks direct Messages replies every five minutes. It retries a failed private preview a limited number of times; a Crew send still requires a fresh `APPROVE` reply and failures are recorded locally without blocking future recaps.

Install the five-minute approval poller:

```bash
cp launchd/com.mark.geosports-recap-approval.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mark.geosports-recap-approval.plist
```

Unload it:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.mark.geosports-recap-approval.plist
```
