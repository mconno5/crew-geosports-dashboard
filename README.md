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
4. Applies the dedupe rule: first score per sender per day, and first instance of each score per day.
5. Writes CSV/JSON data into `data/`.
6. Writes the final dashboard to `dist/dashboard.html`.

## Files

- `config/players.json`: maps iMessage sender handles to dashboard names and colors.
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
