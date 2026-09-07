"""Run the analytical queries and save chart images to /charts.

Everything reads from the DuckDB file that transform.py builds. If I ever want
a new chart I just add a query + plot block below.
"""
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt

DATA_DIR = Path(__file__).parent / "data"
CHARTS_DIR = Path(__file__).parent / "charts"
DB_PATH = DATA_DIR / "chess.duckdb"


def score(result: str) -> float:
    """Chess scoring convention. Win = 1, draw = 0.5, loss = 0."""
    if result == "win":
        return 1.0
    if result in ("agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"):
        return 0.5
    return 0.0


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit("no duckdb file, run transform.py first")

    CHARTS_DIR.mkdir(exist_ok=True)
    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.create_function("score", score, ["VARCHAR"], "DOUBLE")

    # --- overall record ---
    overall = con.execute("""
        SELECT
            COUNT(*) AS games,
            SUM(CASE WHEN my_result = 'win' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN my_result IN ('checkmated','timeout','resigned','abandoned') THEN 1 ELSE 0 END) AS losses,
            ROUND(AVG(score(my_result)) * 100, 1) AS score_pct
        FROM games
    """).fetchone()
    print(f"overall: {overall[0]} games, {overall[1]}W / {overall[2]}L, score {overall[3]}%")

    # --- win rate by color ---
    by_color = con.execute("""
        SELECT my_color,
               COUNT(*) AS games,
               ROUND(AVG(score(my_result)) * 100, 1) AS score_pct
        FROM games
        GROUP BY my_color
        ORDER BY my_color
    """).df()
    print("\nby color:")
    print(by_color.to_string(index=False))

    # --- most-played openings ---
    openings = con.execute("""
        SELECT opening_name,
               COUNT(*) AS games,
               ROUND(AVG(score(my_result)) * 100, 1) AS score_pct
        FROM games
        WHERE opening_name IS NOT NULL
        GROUP BY opening_name
        HAVING COUNT(*) >= 2
        ORDER BY games DESC, score_pct DESC
        LIMIT 10
    """).df()
    print("\ntop openings (>=2 games):")
    print(openings.to_string(index=False))

    # --- rating trend over time ---
    rating_trend = con.execute("""
        SELECT played_at, time_class, my_rating
        FROM games
        WHERE my_rating IS NOT NULL
        ORDER BY played_at
    """).df()

    fig, ax = plt.subplots(figsize=(9, 4))
    for tc, grp in rating_trend.groupby("time_class"):
        ax.plot(grp["played_at"], grp["my_rating"], marker="o", label=tc, alpha=0.8)
    ax.set_title(f"BizareChess rating over time")
    ax.set_ylabel("rating")
    ax.set_xlabel("game date")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(CHARTS_DIR / "rating_trend.png", dpi=120)
    plt.close(fig)

    # --- win rate by opening (bar chart) ---
    if not openings.empty:
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.barh(openings["opening_name"], openings["score_pct"])
        ax.set_xlabel("score %")
        ax.set_title("score % by opening (>=2 games)")
        ax.invert_yaxis()
        fig.tight_layout()
        fig.savefig(CHARTS_DIR / "openings.png", dpi=120)
        plt.close(fig)

    print(f"\ncharts saved to {CHARTS_DIR}/")


if __name__ == "__main__":
    main()
