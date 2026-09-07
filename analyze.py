"""Run the analytical queries, render charts, and write the HTML report.

Everything reads from the DuckDB file that transform.py builds. The docs/
folder is what GitHub Pages serves, so everything visual lands there.

Charts share one palette + one matplotlib style so they feel like a set.
"""
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
DOCS_DIR = Path(__file__).parent / "docs"
DB_PATH = DATA_DIR / "chess.duckdb"

USERNAME = "BizareChess"

# palette lifted from a chess book: parchment + moss green + wood browns
COLORS = {
    "bg": "#f4ecd8",
    "card": "#fff9ec",
    "ink": "#2a1a0e",
    "muted": "#6b5b45",
    "green": "#6b8e4e",       # win / positive
    "brown": "#8b5a2b",        # neutral / secondary
    "red": "#a04a3a",          # loss / negative
    "sand": "#d8c8a8",         # borders / grids
    "cream": "#eeeed2",        # light board square
    "moss": "#769656",         # dark board square
}

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Georgia", "Times New Roman", "DejaVu Serif"],
    "axes.facecolor": COLORS["card"],
    "figure.facecolor": COLORS["card"],
    "axes.edgecolor": COLORS["muted"],
    "axes.labelcolor": COLORS["ink"],
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
    "axes.titlecolor": COLORS["ink"],
    "xtick.color": COLORS["muted"],
    "ytick.color": COLORS["muted"],
    "grid.color": COLORS["sand"],
    "grid.alpha": 0.5,
    "axes.grid": True,
    "grid.linestyle": ":",
})


# ---------- helpers ----------

def score(result: str) -> float:
    """Chess scoring convention. Win = 1, draw = 0.5, loss = 0."""
    if result == "win":
        return 1.0
    if result in ("agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"):
        return 0.5
    return 0.0


def classify_outcome(row) -> str:
    """Bucket each game into won-by-X or lost-by-X for the termination chart."""
    r = row["my_result"]
    if r == "win":
        # the termination string tells me how, e.g. 'BizareChess won by checkmate'
        term = str(row["termination"] or "").lower()
        if "checkmate" in term:
            return "won: checkmate"
        if "resignation" in term or "resigned" in term:
            return "won: resignation"
        if "timeout" in term or "time" in term:
            return "won: timeout"
        return "won: other"
    if r in ("agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"):
        return f"drawn: {r}"
    if r == "checkmated":
        return "lost: checkmate"
    if r == "resigned":
        return "lost: resignation"
    if r == "timeout":
        return "lost: timeout"
    return f"lost: {r}"


def rating_gap_bucket(gap: float) -> str:
    """Bucket opponent - my rating for the performance-vs-gap chart."""
    if pd.isna(gap):
        return "unknown"
    if gap <= -100:
        return "much weaker (<=-100)"
    if gap <= -25:
        return "weaker (-99..-25)"
    if gap < 25:
        return "similar (-24..24)"
    if gap < 100:
        return "stronger (25..99)"
    return "much stronger (>=100)"


BUCKET_ORDER = [
    "much weaker (<=-100)",
    "weaker (-99..-25)",
    "similar (-24..24)",
    "stronger (25..99)",
    "much stronger (>=100)",
]


def elo_expected(my_rating: float, opp_rating: float) -> float:
    """Standard Elo expected score for me against opponent."""
    return 1.0 / (1.0 + 10.0 ** ((opp_rating - my_rating) / 400.0))


# ---------- charts ----------

def chart_rating_trend(df: pd.DataFrame, path: Path) -> None:
    df = df.dropna(subset=["my_rating"]).sort_values("played_at")
    fig, ax = plt.subplots(figsize=(9, 3.6))
    for tc, grp in df.groupby("time_class"):
        ax.plot(grp["played_at"], grp["my_rating"], marker="o", markersize=4,
                label=tc, color=COLORS["green"] if tc == "rapid" else COLORS["brown"], alpha=0.9)
    ax.set_ylabel("rating")
    ax.set_title("Rating over time")
    ax.legend(loc="best", frameon=False)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def chart_openings(openings: pd.DataFrame, path: Path) -> None:
    if openings.empty:
        return
    fig, ax = plt.subplots(figsize=(9, max(3.5, 0.4 * len(openings))))
    ax.barh(openings["opening_name"], openings["score_pct"], color=COLORS["green"], alpha=0.85)
    ax.axvline(50, color=COLORS["muted"], linestyle="--", linewidth=1, alpha=0.6)
    ax.set_xlabel("score %")
    ax.set_title("Score % by opening (2+ games)")
    ax.set_xlim(0, 100)
    ax.invert_yaxis()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def chart_termination(df: pd.DataFrame, path: Path) -> None:
    df = df.copy()
    df["bucket"] = df.apply(classify_outcome, axis=1)
    counts = df["bucket"].value_counts()

    colors = []
    for label in counts.index:
        if label.startswith("won"):
            colors.append(COLORS["green"])
        elif label.startswith("drawn"):
            colors.append(COLORS["brown"])
        else:
            colors.append(COLORS["red"])

    fig, ax = plt.subplots(figsize=(9, max(3, 0.4 * len(counts))))
    ax.barh(counts.index, counts.values, color=colors, alpha=0.85)
    ax.set_xlabel("games")
    ax.set_title("How games ended")
    ax.invert_yaxis()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def chart_rolling_form(df: pd.DataFrame, path: Path, window: int = 5) -> None:
    """Rolling score % over the last `window` games, over the whole timeline."""
    d = df.sort_values("played_at").copy()
    d["pts"] = d["my_result"].map(score)
    d["rolling"] = d["pts"].rolling(window=window, min_periods=1).mean() * 100

    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.plot(d["played_at"], d["rolling"], color=COLORS["moss"], linewidth=2)
    ax.fill_between(d["played_at"], d["rolling"], 50,
                    where=(d["rolling"] >= 50), color=COLORS["green"], alpha=0.2)
    ax.fill_between(d["played_at"], d["rolling"], 50,
                    where=(d["rolling"] < 50), color=COLORS["red"], alpha=0.2)
    ax.axhline(50, color=COLORS["muted"], linestyle="--", linewidth=1)
    ax.set_ylabel(f"rolling {window}-game score %")
    ax.set_ylim(0, 100)
    ax.set_title(f"Form over time (rolling {window}-game window)")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def chart_perf_vs_gap(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    """Score % by opponent-rating-gap bucket, with Elo-expected as a comparison."""
    d = df.dropna(subset=["my_rating", "opp_rating"]).copy()
    d["gap"] = d["opp_rating"] - d["my_rating"]
    d["bucket"] = d["gap"].map(rating_gap_bucket)
    d["pts"] = d["my_result"].map(score)
    d["expected"] = d.apply(lambda r: elo_expected(r["my_rating"], r["opp_rating"]), axis=1)

    grouped = d.groupby("bucket").agg(
        games=("pts", "size"),
        actual=("pts", "mean"),
        expected=("expected", "mean"),
    ).reindex(BUCKET_ORDER).dropna(how="all")

    grouped["actual"] = grouped["actual"] * 100
    grouped["expected"] = grouped["expected"] * 100

    fig, ax = plt.subplots(figsize=(9, 3.8))
    x = np.arange(len(grouped))
    w = 0.38
    ax.bar(x - w/2, grouped["actual"], w, label="actual score", color=COLORS["green"], alpha=0.9)
    ax.bar(x + w/2, grouped["expected"], w, label="Elo expected", color=COLORS["brown"], alpha=0.7)
    ax.set_xticks(x)
    ax.set_xticklabels(grouped.index, rotation=15, ha="right")
    ax.set_ylabel("score %")
    ax.set_ylim(0, 100)
    ax.set_title("Actual vs Elo-expected score by rating gap")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)

    return grouped.reset_index().rename(columns={"index": "bucket"})


# ---------- html ----------

def render_html(ctx: dict) -> str:
    def rows(df, cols):
        out = []
        for _, r in df.iterrows():
            cells = "".join(f"<td>{r[c]}</td>" for c in cols)
            out.append(f"<tr>{cells}</tr>")
        return "\n".join(out) or "<tr><td colspan='9'>no data yet</td></tr>"

    color_rows = rows(ctx["by_color"], ["my_color", "games", "score_pct"])
    tc_rows = rows(ctx["by_tc"], ["time_class", "games", "score_pct"])
    opening_rows = rows(ctx["openings"], ["opening_name", "games", "score_pct"])
    h2h_rows = rows(ctx["h2h"], ["opp_username", "games", "score_pct", "avg_opp_rating"])
    recent_rows = "\n".join(
        f"<tr><td>{r.played_at.strftime('%Y-%m-%d %H:%M')}</td>"
        f"<td class='pill pill-{r.my_color}'>{r.my_color}</td>"
        f"<td><a href='{r.url}' target='_blank'>{r.opp_username}</a> <span class='dim'>({int(r.opp_rating) if pd.notna(r.opp_rating) else '?'})</span></td>"
        f"<td class='result-{'w' if r.my_result == 'win' else 'd' if r.my_result in ('agreed','repetition','stalemate','insufficient','50move','timevsinsufficient') else 'l'}'>{r.my_result}</td>"
        f"<td class='dim'>{r.opening_name or ''}</td></tr>"
        for _, r in ctx["recent"].iterrows()
    )
    gap_rows = "\n".join(
        f"<tr><td>{r.bucket}</td><td>{int(r.games)}</td>"
        f"<td>{r.actual:.1f}%</td><td>{r.expected:.1f}%</td>"
        f"<td class='{ 'good' if r.actual > r.expected else 'bad' }'>{r.actual - r.expected:+.1f}</td></tr>"
        for _, r in ctx["gap"].iterrows()
    ) or "<tr><td colspan='5'>no rated games with ratings yet</td></tr>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{USERNAME} · chess analytics</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: {COLORS["bg"]};
    --card: {COLORS["card"]};
    --ink: {COLORS["ink"]};
    --muted: {COLORS["muted"]};
    --green: {COLORS["green"]};
    --brown: {COLORS["brown"]};
    --red: {COLORS["red"]};
    --sand: {COLORS["sand"]};
    --cream: {COLORS["cream"]};
    --moss: {COLORS["moss"]};
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg);
    color: var(--ink);
    line-height: 1.55;
  }}
  .wrap {{ max-width: 960px; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }}

  /* hero */
  .hero {{
    display: flex; align-items: center; gap: 1.25rem;
    padding: 1.5rem 1.75rem;
    background: var(--card);
    border: 1px solid var(--sand);
    border-radius: 10px;
    margin-bottom: 1.5rem;
    box-shadow: 0 1px 0 rgba(0,0,0,0.02);
  }}
  .king {{
    font-size: 3.75rem; line-height: 1;
    color: var(--moss);
    text-shadow: 0 2px 0 rgba(0,0,0,0.06);
    font-family: serif;
  }}
  h1 {{ font-family: "Cormorant Garamond", Georgia, serif; font-weight: 700;
        font-size: 2.4rem; margin: 0; letter-spacing: -0.5px; }}
  .tagline {{ margin: 0.15rem 0 0; color: var(--muted); font-size: 0.95rem; }}

  /* stat grid */
  .stats {{
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.8rem;
    margin-bottom: 2rem;
  }}
  .stat {{
    background: var(--card);
    border: 1px solid var(--sand);
    border-radius: 8px;
    padding: 1rem 1.1rem;
    text-align: left;
  }}
  .stat .v {{ font-family: "Cormorant Garamond", Georgia, serif;
              font-size: 2.1rem; font-weight: 700; color: var(--ink); line-height: 1; }}
  .stat .l {{ font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em;
              color: var(--muted); margin-top: 0.35rem; }}

  /* two-column grid for small tables */
  .two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem; }}
  @media (max-width: 640px) {{
    .stats {{ grid-template-columns: repeat(2, 1fr); }}
    .two {{ grid-template-columns: 1fr; }}
  }}

  /* sections */
  h2 {{
    font-family: "Cormorant Garamond", Georgia, serif;
    font-weight: 700;
    font-size: 1.5rem;
    color: var(--ink);
    margin: 2.25rem 0 0.75rem;
    padding-bottom: 0.35rem;
    border-bottom: 1px solid var(--sand);
  }}
  h2::before {{
    content: "♟ ";
    color: var(--moss);
    margin-right: 0.4rem;
  }}

  /* tables */
  .card {{ background: var(--card); border: 1px solid var(--sand); border-radius: 8px; padding: 1rem 1.1rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--sand); }}
  th {{ font-weight: 600; color: var(--muted); text-transform: uppercase;
        font-size: 0.72rem; letter-spacing: 0.06em; }}
  tr:last-child td {{ border-bottom: none; }}
  .dim {{ color: var(--muted); }}
  .good {{ color: var(--green); font-weight: 600; }}
  .bad {{ color: var(--red); font-weight: 600; }}
  .result-w {{ color: var(--green); font-weight: 600; }}
  .result-l {{ color: var(--red); font-weight: 500; }}
  .result-d {{ color: var(--brown); font-weight: 500; }}
  .pill {{ display: inline-block; padding: 0.1rem 0.5rem; border-radius: 10px;
           font-size: 0.72rem; font-weight: 600; }}
  .pill-white {{ background: var(--cream); color: var(--ink); border: 1px solid var(--sand); }}
  .pill-black {{ background: var(--ink); color: var(--cream); }}
  a {{ color: var(--moss); }}
  a:hover {{ text-decoration: none; }}

  img.chart {{ display: block; width: 100%; margin: 0.5rem 0 0;
               border: 1px solid var(--sand); border-radius: 6px; background: var(--card); }}

  footer {{ color: var(--muted); font-size: 0.82rem; margin-top: 3rem;
            padding-top: 1rem; border-top: 1px solid var(--sand); }}
</style>
</head>
<body>
<div class="wrap">

  <header class="hero">
    <div class="king">♔</div>
    <div>
      <h1>{USERNAME}</h1>
      <p class="tagline">chess.com analytics · auto-generated from my games</p>
    </div>
  </header>

  <section class="stats">
    <div class="stat"><div class="v">{ctx["overall"]["games"]}</div><div class="l">games</div></div>
    <div class="stat"><div class="v">{ctx["overall"]["wins"]}</div><div class="l">wins</div></div>
    <div class="stat"><div class="v">{ctx["overall"]["losses"]}</div><div class="l">losses</div></div>
    <div class="stat"><div class="v">{ctx["overall"]["score_pct"]}%</div><div class="l">score</div></div>
  </section>

  <div class="two">
    <div class="card">
      <h2 style="margin-top:0; border:none; padding:0; font-size:1.2rem;">By color</h2>
      <table><tr><th>color</th><th>games</th><th>score</th></tr>{color_rows}</table>
    </div>
    <div class="card">
      <h2 style="margin-top:0; border:none; padding:0; font-size:1.2rem;">By time control</h2>
      <table><tr><th>class</th><th>games</th><th>score</th></tr>{tc_rows}</table>
    </div>
  </div>

  <h2>Rating over time</h2>
  <img class="chart" src="rating_trend.png" alt="rating trend">

  <h2>Form (rolling 5-game score)</h2>
  <img class="chart" src="rolling_form.png" alt="rolling form">

  <h2>How games ended</h2>
  <img class="chart" src="termination.png" alt="termination breakdown">

  <h2>Actual vs Elo-expected score</h2>
  <p class="dim" style="font-size:0.88rem; margin-top:-0.25rem;">
    Score % by opponent rating gap, next to the score Elo predicts. Positive delta means overperformance.
  </p>
  <img class="chart" src="perf_vs_gap.png" alt="performance vs rating gap">
  <table>
    <tr><th>rating gap</th><th>games</th><th>actual</th><th>expected</th><th>delta</th></tr>
    {gap_rows}
  </table>

  <h2>Openings</h2>
  <img class="chart" src="openings.png" alt="openings">
  <table>
    <tr><th>opening</th><th>games</th><th>score</th></tr>
    {opening_rows}
  </table>

  <h2>Most-played opponents</h2>
  <table>
    <tr><th>opponent</th><th>games</th><th>score</th><th>avg opp rating</th></tr>
    {h2h_rows}
  </table>

  <h2>Recent games</h2>
  <table>
    <tr><th>played</th><th>color</th><th>opponent</th><th>result</th><th>opening</th></tr>
    {recent_rows}
  </table>

  <footer>
    Data pulled from the chess.com public API. Last updated {ctx["updated_at"]}.
    Pipeline source · <a href="https://github.com/SammyBolger/chess-analytics">github.com/SammyBolger/chess-analytics</a>
  </footer>

</div>
</body>
</html>
"""


# ---------- main ----------

def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit("no duckdb file, run transform.py first")

    DOCS_DIR.mkdir(exist_ok=True)
    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.create_function("score", score, ["VARCHAR"], "DOUBLE")

    # ---- overall ----
    row = con.execute("""
        SELECT
            COUNT(*) AS games,
            SUM(CASE WHEN my_result = 'win' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN my_result IN ('checkmated','timeout','resigned','abandoned') THEN 1 ELSE 0 END) AS losses,
            ROUND(AVG(score(my_result)) * 100, 1) AS score_pct
        FROM games
    """).fetchone()
    overall = {"games": row[0], "wins": row[1], "losses": row[2], "score_pct": row[3]}
    print(f"overall: {overall['games']} games, {overall['wins']}W / {overall['losses']}L, score {overall['score_pct']}%")

    # ---- by color ----
    by_color = con.execute("""
        SELECT my_color,
               COUNT(*) AS games,
               CAST(ROUND(AVG(score(my_result)) * 100, 1) AS VARCHAR) || '%' AS score_pct
        FROM games
        GROUP BY my_color
        ORDER BY my_color
    """).df()

    # ---- by time control ----
    by_tc = con.execute("""
        SELECT time_class,
               COUNT(*) AS games,
               CAST(ROUND(AVG(score(my_result)) * 100, 1) AS VARCHAR) || '%' AS score_pct
        FROM games
        GROUP BY time_class
        ORDER BY games DESC
    """).df()

    # ---- openings ----
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

    # ---- head-to-head ----
    h2h = con.execute("""
        SELECT opp_username,
               COUNT(*) AS games,
               CAST(ROUND(AVG(score(my_result)) * 100, 1) AS VARCHAR) || '%' AS score_pct,
               ROUND(AVG(opp_rating)) AS avg_opp_rating
        FROM games
        WHERE opp_username IS NOT NULL
        GROUP BY opp_username
        HAVING COUNT(*) >= 2
        ORDER BY games DESC, avg_opp_rating DESC
        LIMIT 8
    """).df()
    if h2h.empty:
        # fall back to unique opponents shown once each so the table isn't empty
        h2h = con.execute("""
            SELECT opp_username,
                   COUNT(*) AS games,
                   CAST(ROUND(AVG(score(my_result)) * 100, 1) AS VARCHAR) || '%' AS score_pct,
                   opp_rating AS avg_opp_rating
            FROM games
            WHERE opp_username IS NOT NULL
            GROUP BY opp_username, opp_rating
            ORDER BY games DESC
            LIMIT 8
        """).df()

    # ---- recent games (for the table) ----
    recent = con.execute("""
        SELECT played_at, my_color, opp_username, opp_rating, my_result, opening_name, url
        FROM games
        ORDER BY played_at DESC
        LIMIT 12
    """).df()

    # ---- rating trend + termination + rolling + gap need the full flat df ----
    full = con.execute("""
        SELECT played_at, time_class, my_color, my_result, my_rating, opp_rating,
               termination, opp_username, num_moves
        FROM games
        ORDER BY played_at
    """).df()

    # ---- charts ----
    chart_rating_trend(full, DOCS_DIR / "rating_trend.png")
    chart_openings(openings, DOCS_DIR / "openings.png")
    chart_termination(full, DOCS_DIR / "termination.png")
    chart_rolling_form(full, DOCS_DIR / "rolling_form.png")
    gap_df = chart_perf_vs_gap(full, DOCS_DIR / "perf_vs_gap.png")

    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ctx = {
        "overall": overall,
        "by_color": by_color,
        "by_tc": by_tc,
        "openings": openings,
        "h2h": h2h,
        "recent": recent,
        "gap": gap_df,
        "updated_at": updated_at,
    }
    html = render_html(ctx)
    (DOCS_DIR / "index.html").write_text(html)

    print(f"report + charts written to {DOCS_DIR}/")


if __name__ == "__main__":
    main()
