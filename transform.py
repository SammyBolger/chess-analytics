"""Turn the raw chess.com JSON dumps into a clean DuckDB table for analysis.

I keep two layers here:

    raw_games   one row per game, exactly as chess.com returns it (json string)
    games       flat, typed table with the columns I actually query on

This is basically a poor-man's bronze/silver split. It lets me re-run the
transform step without hitting the API again.
"""
import io
import json
from pathlib import Path

import chess.pgn
import duckdb

USERNAME = "bizarechess"
DATA_DIR = Path(__file__).parent / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "chess.duckdb"


def parse_opening_from_eco_url(eco_url: str | None) -> str | None:
    """The eco field is a URL like .../openings/Sicilian-Defense-Najdorf.
    Grab the last segment and turn dashes into spaces.
    """
    if not eco_url:
        return None
    slug = eco_url.rstrip("/").split("/")[-1]
    return slug.replace("-", " ")


def parse_pgn_features(pgn_text: str) -> dict:
    """Pull ECO code, termination, and move count out of the PGN text."""
    if not pgn_text:
        return {"eco_code": None, "termination": None, "num_moves": None}
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return {"eco_code": None, "termination": None, "num_moves": None}
    headers = game.headers
    # ply count divided by 2 gives full moves, rounded up
    ply = sum(1 for _ in game.mainline_moves())
    return {
        "eco_code": headers.get("ECO"),
        "termination": headers.get("Termination"),
        "num_moves": (ply + 1) // 2,
    }


def flatten_game(g: dict) -> dict:
    white = g.get("white", {}) or {}
    black = g.get("black", {}) or {}
    pgn_feats = parse_pgn_features(g.get("pgn", ""))
    accs = g.get("accuracies") or {}

    # figure out which side is me so I don't have to do this in SQL later
    if white.get("username", "").lower() == USERNAME:
        my_color = "white"
        my_result = white.get("result")
        my_rating = white.get("rating")
        opp_rating = black.get("rating")
        opp_username = black.get("username")
        my_accuracy = accs.get("white")
        opp_accuracy = accs.get("black")
    else:
        my_color = "black"
        my_result = black.get("result")
        my_rating = black.get("rating")
        opp_rating = white.get("rating")
        opp_username = white.get("username")
        my_accuracy = accs.get("black")
        opp_accuracy = accs.get("white")

    return {
        "game_id": g.get("uuid"),
        "url": g.get("url"),
        "played_at": g.get("end_time"),
        "time_control": g.get("time_control"),
        "time_class": g.get("time_class"),
        "rated": g.get("rated"),
        "opening_name": parse_opening_from_eco_url(g.get("eco")),
        "eco_code": pgn_feats["eco_code"],
        "termination": pgn_feats["termination"],
        "num_moves": pgn_feats["num_moves"],
        "my_color": my_color,
        "my_result": my_result,
        "my_rating": my_rating,
        "opp_username": opp_username,
        "opp_rating": opp_rating,
        "my_accuracy": my_accuracy,
        "opp_accuracy": opp_accuracy,
    }


def load_all_raw() -> list[dict]:
    games = []
    for path in sorted(RAW_DIR.glob("*.json")):
        month_data = json.loads(path.read_text())
        for g in month_data.get("games", []):
            games.append(g)
    return games


def main() -> None:
    if not RAW_DIR.exists() or not any(RAW_DIR.glob("*.json")):
        raise SystemExit("no raw data found, run ingest.py first")

    raw_games = load_all_raw()
    print(f"loaded {len(raw_games)} raw games")

    flat = [flatten_game(g) for g in raw_games]

    con = duckdb.connect(str(DB_PATH))
    con.execute("DROP TABLE IF EXISTS raw_games")
    con.execute("DROP TABLE IF EXISTS games")

    # store the raw payload alongside the flat table so I can go back to it
    # if I ever need to pull a field I forgot to extract
    con.execute("CREATE TABLE raw_games (game_id VARCHAR, payload JSON)")
    con.executemany(
        "INSERT INTO raw_games VALUES (?, ?)",
        [(g.get("uuid"), json.dumps(g)) for g in raw_games],
    )

    # let duckdb infer schema from a pandas-shaped list of dicts via a view
    import pandas as pd
    df = pd.DataFrame(flat)
    # convert unix epoch to timestamp for nicer querying
    df["played_at"] = pd.to_datetime(df["played_at"], unit="s", utc=True)
    con.register("flat_df", df)
    con.execute("CREATE TABLE games AS SELECT * FROM flat_df")

    print("wrote raw_games and games tables to", DB_PATH)
    print(con.execute("SELECT COUNT(*) AS n FROM games").fetchone())


if __name__ == "__main__":
    main()
