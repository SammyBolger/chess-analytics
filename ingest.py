"""Pull my chess.com game archives and save them as raw JSON.

Chess.com groups every player's games by month. The archives endpoint gives
back a list of month URLs, and each of those returns the games for that month.
"""
import json
import time
from pathlib import Path

import requests

USERNAME = "bizarechess"
RAW_DIR = Path(__file__).parent / "data" / "raw"

# chess.com blocks default python-requests agents, so we set our own
HEADERS = {"User-Agent": "chess-analytics/1.0 (sammybolger1234@gmail.com)"}


def get_archive_urls(username: str) -> list[str]:
    url = f"https://api.chess.com/pub/player/{username}/games/archives"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()["archives"]


def fetch_month(archive_url: str) -> dict:
    r = requests.get(archive_url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    archives = get_archive_urls(USERNAME)
    print(f"found {len(archives)} monthly archives for {USERNAME}")

    total_games = 0
    for url in archives:
        # url looks like .../games/2026/09 so grab the last two segments as the file name
        year, month = url.rstrip("/").split("/")[-2:]
        out_path = RAW_DIR / f"{year}-{month}.json"

        data = fetch_month(url)
        out_path.write_text(json.dumps(data))
        games_this_month = len(data.get("games", []))
        total_games += games_this_month
        print(f"  {year}-{month}: {games_this_month} games")

        # be polite to the public API
        time.sleep(0.3)

    print(f"done. saved {total_games} games across {len(archives)} months.")


if __name__ == "__main__":
    main()
