"""Run the analytical queries, save chart images, and write an HTML report.

Everything reads from the DuckDB file that transform.py builds. The docs/
folder is what GitHub Pages serves, so charts + index.html go in there.
"""
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt

DATA_DIR = Path(__file__).parent / "data"
DOCS_DIR = Path(__file__).parent / "docs"
DB_PATH = DATA_DIR / "chess.duckdb"

USERNAME = "BizareChess"


def score(result: str) -> float:
    """Chess scoring convention. Win = 1, draw = 0.5, loss = 0."""
    if result == "win":
        return 1.0
    if result in ("agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"):
        return 0.5
    return 0.0


def render_html(overall, by_color, openings, recent, updated_at) -> str:
    def openings_rows() -> str:
        if openings.empty:
            return "<tr><td colspan='3'>not enough games yet</td></tr>"
        rows = []
        for _, r in openings.iterrows():
            rows.append(
                f"<tr><td>{r.opening_name}</td><td>{r.games}</td><td>{r.score_pct}%</td></tr>"
            )
        return "\n".join(rows)

    def recent_rows() -> str:
        rows = []
        for _, r in recent.iterrows():
            date = r.played_at.strftime("%Y-%m-%d")
            rows.append(
                f"<tr><td>{date}</td><td>{r.my_color}</td><td>{r.opp_username} ({r.opp_rating})</td>"
                f"<td>{r.my_result}</td><td>{r.opening_name or ''}</td></tr>"
            )
        return "\n".join(rows)

    color_lines = []
    for _, r in by_color.iterrows():
        color_lines.append(f"<li>{r.my_color}: {r.games} games, {r.score_pct}%</li>")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{USERNAME} chess analytics</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #222; }}
  h1 {{ margin-bottom: 0.25rem; }}
  .subtitle {{ color: #666; margin-top: 0; }}
  .stats {{ display: flex; gap: 2rem; padding: 1rem 0; border-top: 1px solid #eee; border-bottom: 1px solid #eee; }}
  .stat {{ font-size: 0.9rem; color: #555; }}
  .stat b {{ display: block; font-size: 1.5rem; color: #111; }}
  img {{ max-width: 100%; margin: 1.5rem 0; border: 1px solid #eee; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; margin: 1rem 0; }}
  th, td {{ text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #eee; }}
  th {{ background: #fafafa; }}
  footer {{ color: #888; font-size: 0.8rem; margin-top: 3rem; }}
</style>
</head>
<body>

<h1>{USERNAME}</h1>
<p class="subtitle">chess.com analytics, auto-generated from my own games</p>

<div class="stats">
  <div class="stat"><b>{overall[0]}</b>games played</div>
  <div class="stat"><b>{overall[1]}</b>wins</div>
  <div class="stat"><b>{overall[2]}</b>losses</div>
  <div class="stat"><b>{overall[3]}%</b>score</div>
</div>

<h2>By color</h2>
<ul>{"".join(color_lines)}</ul>

<h2>Rating over time</h2>
<img src="rating_trend.png" alt="rating trend">

<h2>Score by opening</h2>
<img src="openings.png" alt="score by opening">

<h2>Top openings (2+ games)</h2>
<table>
<tr><th>opening</th><th>games</th><th>score</th></tr>
{openings_rows()}
</table>

<h2>Recent games</h2>
<table>
<tr><th>date</th><th>color</th><th>opponent</th><th>result</th><th>opening</th></tr>
{recent_rows()}
</table>

<footer>
Data pulled from the chess.com public API. Last updated {updated_at}.
Source: <a href="https://github.com/SammyBolger/chess-analytics">github.com/SammyBolger/chess-analytics</a>
</footer>

</body>
</html>
"""


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit("no duckdb file, run transform.py first")

    DOCS_DIR.mkdir(exist_ok=True)
    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.create_function("score", score, ["VARCHAR"], "DOUBLE")

    overall = con.execute("""
        SELECT
            COUNT(*) AS games,
            SUM(CASE WHEN my_result = 'win' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN my_result IN ('checkmated','timeout','resigned','abandoned') THEN 1 ELSE 0 END) AS losses,
            ROUND(AVG(score(my_result)) * 100, 1) AS score_pct
        FROM games
    """).fetchone()
    print(f"overall: {overall[0]} games, {overall[1]}W / {overall[2]}L, score {overall[3]}%")

    by_color = con.execute("""
        SELECT my_color,
               COUNT(*) AS games,
               ROUND(AVG(score(my_result)) * 100, 1) AS score_pct
        FROM games
        GROUP BY my_color
        ORDER BY my_color
    """).df()

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

    recent = con.execute("""
        SELECT played_at, my_color, opp_username, opp_rating, my_result, opening_name
        FROM games
        ORDER BY played_at DESC
        LIMIT 10
    """).df()

    rating_trend = con.execute("""
        SELECT played_at, time_class, my_rating
        FROM games
        WHERE my_rating IS NOT NULL
        ORDER BY played_at
    """).df()

    fig, ax = plt.subplots(figsize=(9, 4))
    for tc, grp in rating_trend.groupby("time_class"):
        ax.plot(grp["played_at"], grp["my_rating"], marker="o", label=tc, alpha=0.85)
    ax.set_title(f"{USERNAME} rating over time")
    ax.set_ylabel("rating")
    ax.set_xlabel("game date")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(DOCS_DIR / "rating_trend.png", dpi=120)
    plt.close(fig)

    if not openings.empty:
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.barh(openings["opening_name"], openings["score_pct"])
        ax.set_xlabel("score %")
        ax.set_title("score % by opening (2+ games)")
        ax.invert_yaxis()
        fig.tight_layout()
        fig.savefig(DOCS_DIR / "openings.png", dpi=120)
        plt.close(fig)

    from datetime import datetime, timezone
    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    html = render_html(overall, by_color, openings, recent, updated_at)
    (DOCS_DIR / "index.html").write_text(html)

    print(f"report + charts written to {DOCS_DIR}/")


if __name__ == "__main__":
    main()
