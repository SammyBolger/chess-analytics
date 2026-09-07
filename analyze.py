"""Run the analytical queries and write an interactive HTML dashboard.

Uses Plotly for every chart so hover, zoom, and pan work on the live site.
All queries read from the DuckDB file transform.py builds. All chart divs
are inlined into a single docs/index.html with one Plotly CDN script.
"""
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go

DATA_DIR = Path(__file__).parent / "data"
DOCS_DIR = Path(__file__).parent / "docs"
DB_PATH = DATA_DIR / "chess.duckdb"

USERNAME = "BizareChess"
DISPLAY_NAME = "Sammy Bolger Chess Analytics"

# palette: parchment + moss green + wood browns, one distinct color per time class
COLORS = {
    "bg": "#e6d3a3",          # deeper parchment for body
    "card": "#f6ecd2",         # cream card
    "ink": "#2a1a0e",
    "muted": "#6b5b45",
    "green": "#6b8e4e",        # rapid / positive
    "brown": "#a06845",        # neutral / secondary
    "red": "#a04a3a",          # loss / negative
    "sand": "#c9b489",
    "cream": "#eeeed2",
    "moss": "#769656",         # dark chess board square
}

# each time class gets its own color so the rating chart is readable
TC_COLORS = {
    "rapid": COLORS["green"],
    "blitz": "#3b6ea5",        # steel blue
    "bullet": "#c9873a",       # amber
    "daily": COLORS["moss"],
}

PLOTLY_LAYOUT = dict(
    paper_bgcolor=COLORS["card"],
    plot_bgcolor=COLORS["card"],
    font=dict(family="Georgia, serif", color=COLORS["ink"], size=13),
    margin=dict(l=50, r=20, t=40, b=40),
    xaxis=dict(gridcolor=COLORS["sand"], zerolinecolor=COLORS["sand"]),
    yaxis=dict(gridcolor=COLORS["sand"], zerolinecolor=COLORS["sand"]),
    hoverlabel=dict(bgcolor=COLORS["cream"], font=dict(family="Georgia", color=COLORS["ink"])),
    legend=dict(bgcolor=COLORS["card"], bordercolor=COLORS["sand"], borderwidth=1),
)


# ---------- helpers ----------

def score(result: str) -> float:
    if result == "win":
        return 1.0
    if result in ("agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"):
        return 0.5
    return 0.0


def classify_outcome(row) -> str:
    r = row["my_result"]
    if r == "win":
        term = str(row["termination"] or "").lower()
        if "checkmate" in term:
            return "won: checkmate"
        if "resignation" in term or "resigned" in term:
            return "won: resignation"
        if "time" in term or "abandoned" in term:
            return "won: time/abandon"
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


BUCKET_ORDER = [
    "much weaker (<=-100)",
    "weaker (-99..-25)",
    "similar (-24..24)",
    "stronger (25..99)",
    "much stronger (>=100)",
]


def rating_gap_bucket(gap: float) -> str:
    if pd.isna(gap):
        return "unknown"
    if gap <= -100:
        return BUCKET_ORDER[0]
    if gap <= -25:
        return BUCKET_ORDER[1]
    if gap < 25:
        return BUCKET_ORDER[2]
    if gap < 100:
        return BUCKET_ORDER[3]
    return BUCKET_ORDER[4]


def elo_expected(my_rating: float, opp_rating: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((opp_rating - my_rating) / 400.0))


def to_div(fig: go.Figure, div_id: str, height: int = 340) -> str:
    """Return a Plotly div for inlining. We load plotly.js once via the page's
    own <script> tag, so include_plotlyjs=False here."""
    fig.update_layout(height=height, **PLOTLY_LAYOUT)
    return fig.to_html(
        full_html=False, include_plotlyjs=False, div_id=div_id,
        config={"displayModeBar": False, "responsive": True},
    )


# ---------- charts ----------

def fig_rating(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for tc, grp in df.dropna(subset=["my_rating"]).groupby("time_class"):
        color = TC_COLORS.get(tc, COLORS["muted"])
        fig.add_trace(go.Scatter(
            x=grp["played_at"], y=grp["my_rating"],
            mode="lines+markers", name=tc,
            line=dict(color=color, width=2),
            marker=dict(size=7, color=color, line=dict(color="white", width=1)),
            hovertemplate="<b>%{y}</b><br>%{x|%Y-%m-%d %H:%M}<extra>" + tc + "</extra>",
        ))
    fig.update_layout(title="Rating over time", yaxis_title="rating")
    return fig


def fig_rolling(df: pd.DataFrame, window: int = 5) -> go.Figure:
    d = df.sort_values("played_at").copy()
    d["pts"] = d["my_result"].map(score)
    d["rolling"] = d["pts"].rolling(window=window, min_periods=1).mean() * 100

    fig = go.Figure()
    # baseline at 50%
    fig.add_hline(y=50, line=dict(color=COLORS["muted"], dash="dot", width=1))
    # main line
    fig.add_trace(go.Scatter(
        x=d["played_at"], y=d["rolling"], mode="lines",
        line=dict(color=COLORS["moss"], width=2.5),
        fill="tozeroy", fillcolor="rgba(107,142,78,0.15)",
        hovertemplate="<b>%{y:.1f}%</b><br>%{x|%Y-%m-%d %H:%M}<extra></extra>",
        name=f"rolling {window}-game score",
    ))
    fig.update_layout(
        title=f"Form (rolling {window}-game score)",
        yaxis=dict(title="score %", range=[0, 100], gridcolor=COLORS["sand"]),
    )
    return fig


def fig_termination(df: pd.DataFrame) -> go.Figure:
    d = df.copy()
    d["bucket"] = d.apply(classify_outcome, axis=1)
    counts = d["bucket"].value_counts()

    colors = []
    for label in counts.index:
        if label.startswith("won"):
            colors.append(COLORS["green"])
        elif label.startswith("drawn"):
            colors.append(COLORS["brown"])
        else:
            colors.append(COLORS["red"])

    fig = go.Figure(go.Bar(
        y=counts.index, x=counts.values, orientation="h",
        marker=dict(color=colors),
        hovertemplate="<b>%{y}</b><br>%{x} games<extra></extra>",
    ))
    fig.update_layout(
        title="How games ended", xaxis_title="games",
        yaxis=dict(autorange="reversed"),
    )
    return fig


def fig_perf_vs_gap(df: pd.DataFrame) -> tuple[go.Figure, pd.DataFrame]:
    d = df.dropna(subset=["my_rating", "opp_rating"]).copy()
    d["gap"] = d["opp_rating"] - d["my_rating"]
    d["bucket"] = d["gap"].map(rating_gap_bucket)
    d["pts"] = d["my_result"].map(score)
    d["expected"] = d.apply(lambda r: elo_expected(r["my_rating"], r["opp_rating"]), axis=1)

    g = (d.groupby("bucket")
           .agg(games=("pts", "size"), actual=("pts", "mean"), expected=("expected", "mean"))
           .reindex(BUCKET_ORDER).dropna(how="all"))
    g["actual_pct"] = g["actual"] * 100
    g["expected_pct"] = g["expected"] * 100

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=g.index, y=g["actual_pct"], name="actual",
        marker_color=COLORS["green"],
        hovertemplate="<b>actual</b>: %{y:.1f}%<br>games: " + g["games"].astype(str) + "<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=g.index, y=g["expected_pct"], name="Elo expected",
        marker_color=COLORS["brown"],
        hovertemplate="<b>expected</b>: %{y:.1f}%<extra></extra>",
    ))
    fig.update_layout(
        title="Actual vs Elo-expected score by rating gap",
        yaxis=dict(title="score %", range=[0, 100]),
        barmode="group",
    )
    return fig, g.reset_index()


def fig_openings(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Bar(
        y=df["opening_name"], x=df["score_pct"], orientation="h",
        marker=dict(color=COLORS["green"]),
        hovertemplate="<b>%{y}</b><br>score: %{x}%<br>games: " + df["games"].astype(str) + "<extra></extra>",
    ))
    fig.add_vline(x=50, line=dict(color=COLORS["muted"], dash="dot", width=1))
    fig.update_layout(
        title="Score % by opening (2+ games)",
        xaxis=dict(title="score %", range=[0, 100]),
        yaxis=dict(autorange="reversed"),
    )
    return fig


def fig_activity_heatmap(df: pd.DataFrame) -> go.Figure:
    """Games played by weekday x hour, colored by score %."""
    d = df.copy()
    d["dow"] = d["played_at"].dt.day_name()
    d["hour"] = d["played_at"].dt.hour
    d["pts"] = d["my_result"].map(score)

    pivot = (d.groupby(["dow", "hour"])
               .agg(games=("pts", "size"), score_pct=("pts", "mean"))
               .reset_index())
    pivot["score_pct"] = (pivot["score_pct"] * 100).round(1)

    dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    z = np.full((len(dow_order), 24), np.nan)
    counts = np.zeros((len(dow_order), 24), dtype=int)
    for _, r in pivot.iterrows():
        i = dow_order.index(r["dow"])
        z[i, r["hour"]] = r["score_pct"]
        counts[i, r["hour"]] = r["games"]

    hover = np.empty_like(z, dtype=object)
    for i in range(len(dow_order)):
        for j in range(24):
            if counts[i, j] > 0:
                hover[i, j] = f"{dow_order[i]} {j:02d}:00<br>{counts[i, j]} games<br>score: {z[i, j]:.1f}%"
            else:
                hover[i, j] = f"{dow_order[i]} {j:02d}:00<br>no games"

    fig = go.Figure(go.Heatmap(
        z=z, x=[f"{h:02d}" for h in range(24)], y=dow_order,
        colorscale=[[0, COLORS["red"]], [0.5, COLORS["cream"]], [1, COLORS["green"]]],
        zmid=50, zmin=0, zmax=100,
        text=hover, hoverinfo="text",
        colorbar=dict(title="score %", thickness=12),
    ))
    fig.update_layout(
        title="When I play, and how I do (score % by day of week and hour, local time)",
        xaxis=dict(title="hour of day"),
        yaxis=dict(autorange="reversed"),
    )
    return fig


def fig_game_length(df: pd.DataFrame) -> go.Figure:
    d = df.dropna(subset=["num_moves"]).copy()
    d["result_bucket"] = d["my_result"].map(
        lambda r: "win" if r == "win" else
                  "draw" if r in ("agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient")
                  else "loss"
    )
    color_map = {"win": COLORS["green"], "draw": COLORS["brown"], "loss": COLORS["red"]}

    fig = go.Figure()
    for name in ("win", "draw", "loss"):
        subset = d[d["result_bucket"] == name]
        if subset.empty:
            continue
        fig.add_trace(go.Histogram(
            x=subset["num_moves"], name=name,
            marker_color=color_map[name], opacity=0.75,
            xbins=dict(size=5),
            hovertemplate="<b>" + name + "</b><br>%{x} moves<br>%{y} games<extra></extra>",
        ))
    fig.update_layout(
        title="Game length distribution",
        xaxis_title="moves", yaxis_title="games",
        barmode="stack",
    )
    return fig


def fig_score_by_hour(df: pd.DataFrame) -> go.Figure:
    d = df.copy()
    d["hour"] = d["played_at"].dt.hour
    d["pts"] = d["my_result"].map(score)
    g = d.groupby("hour").agg(games=("pts", "size"), score_pct=("pts", "mean")).reset_index()
    g["score_pct"] = g["score_pct"] * 100

    fig = go.Figure()
    fig.add_hline(y=50, line=dict(color=COLORS["muted"], dash="dot", width=1))
    fig.add_trace(go.Bar(
        x=g["hour"], y=g["score_pct"],
        marker=dict(
            color=g["score_pct"],
            colorscale=[[0, COLORS["red"]], [0.5, COLORS["cream"]], [1, COLORS["green"]]],
            cmin=0, cmax=100, showscale=False,
            line=dict(color=COLORS["sand"], width=1),
        ),
        hovertemplate="hour %{x}<br>score: %{y:.1f}%<br>games: " + g["games"].astype(str) + "<extra></extra>",
    ))
    fig.update_layout(
        title="Score % by hour of day",
        xaxis=dict(title="hour", tickmode="linear", dtick=2),
        yaxis=dict(title="score %", range=[0, 100]),
    )
    return fig


# ---------- html ----------

CHESS_BOARD_BG = (
    "linear-gradient(45deg, rgba(118,150,86,0.10) 25%, transparent 25%, transparent 75%, rgba(118,150,86,0.10) 75%),"
    "linear-gradient(45deg, rgba(118,150,86,0.10) 25%, transparent 25%, transparent 75%, rgba(118,150,86,0.10) 75%)"
)


def render_html(ctx: dict, divs: dict[str, str]) -> str:
    color_rows = "".join(
        f"<tr><td class='pill pill-{r.my_color}'>{r.my_color}</td>"
        f"<td>{r.games}</td><td>{r.score_pct}</td></tr>"
        for _, r in ctx["by_color"].iterrows()
    )
    def _tc_row(r):
        color = TC_COLORS.get(r.time_class, COLORS["muted"])
        return (f"<tr><td><span class='dot' style='background:{color}'></span>{r.time_class}</td>"
                f"<td>{r.games}</td><td>{r.score_pct}</td></tr>")
    tc_rows = "".join(_tc_row(r) for _, r in ctx["by_tc"].iterrows())
    h2h_rows = "".join(
        f"<tr><td>{r.opp_username}</td><td>{r.games}</td>"
        f"<td>{r.score_pct}</td><td>{int(r.avg_opp_rating) if pd.notna(r.avg_opp_rating) else '-'}</td></tr>"
        for _, r in ctx["h2h"].iterrows()
    ) or "<tr><td colspan='4' class='dim'>no repeat opponents yet</td></tr>"
    recent_rows = "".join(
        f"<tr><td class='dim'>{r.played_at.strftime('%Y-%m-%d %H:%M')}</td>"
        f"<td class='pill pill-{r.my_color}'>{r.my_color}</td>"
        f"<td><a href='{r.url}' target='_blank'>{r.opp_username}</a>"
        f" <span class='dim'>({int(r.opp_rating) if pd.notna(r.opp_rating) else '?'})</span></td>"
        f"<td class='result-{'w' if r.my_result == 'win' else 'd' if r.my_result in ('agreed','repetition','stalemate','insufficient','50move','timevsinsufficient') else 'l'}'>{r.my_result}</td>"
        f"<td class='dim'>{r.opening_name or ''}</td></tr>"
        for _, r in ctx["recent"].iterrows()
    )
    gap_rows = "".join(
        f"<tr><td>{r.bucket}</td><td>{int(r.games)}</td>"
        f"<td>{r.actual_pct:.1f}%</td><td>{r.expected_pct:.1f}%</td>"
        f"<td class='{ 'good' if r.actual_pct > r.expected_pct else 'bad' }'>{r.actual_pct - r.expected_pct:+.1f}</td></tr>"
        for _, r in ctx["gap"].iterrows()
    ) or "<tr><td colspan='5' class='dim'>no rated games yet</td></tr>"

    streak_html = ""
    if ctx["streaks"]["longest_win"] > 0 or ctx["streaks"]["longest_loss"] > 0:
        streak_html = f"""
        <div class="two" style="margin-bottom:1.5rem;">
          <div class="stat"><div class="v good">W{ctx["streaks"]["longest_win"]}</div><div class="l">longest win streak</div></div>
          <div class="stat"><div class="v bad">L{ctx["streaks"]["longest_loss"]}</div><div class="l">longest loss streak</div></div>
        </div>
        """

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{DISPLAY_NAME}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
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
  html, body {{ margin: 0; padding: 0; }}
  body {{
    font-family: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
    color: var(--ink);
    line-height: 1.55;
    background-color: var(--bg);
    background-image: {CHESS_BOARD_BG};
    background-size: 80px 80px;
    background-position: 0 0, 40px 40px;
    min-height: 100vh;
  }}
  .wrap {{ max-width: 1000px; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }}

  /* hero */
  .hero {{
    display: flex; align-items: center; gap: 1.5rem;
    padding: 1.75rem 2rem;
    background: linear-gradient(135deg, var(--card), #f1e4bf);
    border: 1px solid var(--sand);
    border-radius: 12px;
    margin-bottom: 1.75rem;
    box-shadow: 0 2px 0 rgba(0,0,0,0.03), inset 0 1px 0 rgba(255,255,255,0.4);
  }}
  .king {{
    font-size: 4.25rem; line-height: 1;
    color: var(--moss);
    text-shadow: 2px 3px 0 rgba(42,26,14,0.15);
    font-family: serif;
  }}
  h1 {{
    font-family: "Cormorant Garamond", Georgia, serif;
    font-weight: 700; font-size: 2.4rem;
    margin: 0; letter-spacing: -0.5px; color: var(--ink);
  }}
  .tagline {{
    margin: 0.2rem 0 0; color: var(--muted); font-size: 0.95rem;
  }}
  .tagline a {{ color: var(--moss); text-decoration: none; border-bottom: 1px solid transparent; }}
  .tagline a:hover {{ border-bottom-color: var(--moss); }}

  /* stat grid */
  .stats {{
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.85rem;
    margin-bottom: 1.75rem;
  }}
  .stat {{
    background: var(--card);
    border: 1px solid var(--sand);
    border-radius: 10px;
    padding: 1rem 1.15rem;
    text-align: left;
    box-shadow: 0 1px 0 rgba(0,0,0,0.02);
  }}
  .stat .v {{ font-family: "Cormorant Garamond", Georgia, serif;
              font-size: 2.2rem; font-weight: 700; color: var(--ink); line-height: 1; }}
  .stat .l {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.09em;
              color: var(--muted); margin-top: 0.35rem; font-weight: 500; }}
  .stat .v.good {{ color: var(--green); }}
  .stat .v.bad {{ color: var(--red); }}

  /* two-column layout */
  .two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }}
  @media (max-width: 720px) {{
    .stats {{ grid-template-columns: repeat(2, 1fr); }}
    .two {{ grid-template-columns: 1fr; }}
    h1 {{ font-size: 1.9rem; }}
    .king {{ font-size: 3rem; }}
  }}

  h2 {{
    font-family: "Cormorant Garamond", Georgia, serif;
    font-weight: 700;
    font-size: 1.55rem;
    color: var(--ink);
    margin: 2.5rem 0 0.85rem;
    padding-bottom: 0.35rem;
    border-bottom: 1px solid var(--sand);
  }}
  h2::before {{
    content: "♟";
    color: var(--moss);
    margin-right: 0.55rem;
    font-size: 1.2rem;
  }}
  .card {{
    background: var(--card);
    border: 1px solid var(--sand);
    border-radius: 10px;
    padding: 1rem 1.15rem;
    box-shadow: 0 1px 0 rgba(0,0,0,0.02);
  }}
  .card h3 {{
    margin: 0 0 0.6rem;
    font-family: "Cormorant Garamond", Georgia, serif;
    font-size: 1.15rem;
    color: var(--ink);
    font-weight: 600;
  }}

  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--sand); }}
  th {{ font-weight: 600; color: var(--muted); text-transform: uppercase;
        font-size: 0.7rem; letter-spacing: 0.06em; }}
  tr:last-child td {{ border-bottom: none; }}
  .dim {{ color: var(--muted); }}
  .good {{ color: var(--green); font-weight: 600; }}
  .bad {{ color: var(--red); font-weight: 600; }}
  .result-w {{ color: var(--green); font-weight: 600; }}
  .result-l {{ color: var(--red); }}
  .result-d {{ color: var(--brown); }}
  .pill {{ display: inline-block; padding: 0.1rem 0.55rem; border-radius: 12px;
           font-size: 0.7rem; font-weight: 600; }}
  .pill-white {{ background: var(--cream); color: var(--ink); border: 1px solid var(--sand); }}
  .pill-black {{ background: var(--ink); color: var(--cream); }}
  .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%;
          margin-right: 6px; vertical-align: middle; }}
  a {{ color: var(--moss); }}
  a:hover {{ text-decoration: none; }}

  .chart {{
    background: var(--card);
    border: 1px solid var(--sand);
    border-radius: 10px;
    padding: 0.5rem;
    box-shadow: 0 1px 0 rgba(0,0,0,0.02);
  }}

  .note {{
    font-size: 0.85rem; color: var(--muted); margin: -0.35rem 0 0.8rem;
    font-style: italic;
  }}

  footer {{
    color: var(--muted); font-size: 0.82rem;
    margin-top: 3rem; padding-top: 1rem;
    border-top: 1px solid var(--sand);
    text-align: center;
  }}
</style>
</head>
<body>
<div class="wrap">

  <header class="hero">
    <div class="king">♔</div>
    <div>
      <h1>{DISPLAY_NAME}</h1>
      <p class="tagline">Auto-generated analytics over <a href="https://www.chess.com/member/{USERNAME}" target="_blank">@{USERNAME}</a> games from chess.com</p>
    </div>
  </header>

  <section class="stats">
    <div class="stat"><div class="v">{ctx["overall"]["games"]}</div><div class="l">games</div></div>
    <div class="stat"><div class="v good">{ctx["overall"]["wins"]}</div><div class="l">wins</div></div>
    <div class="stat"><div class="v bad">{ctx["overall"]["losses"]}</div><div class="l">losses</div></div>
    <div class="stat"><div class="v">{ctx["overall"]["score_pct"]}%</div><div class="l">score</div></div>
  </section>

  {streak_html}

  <div class="two">
    <div class="card">
      <h3>By color</h3>
      <table><tr><th>color</th><th>games</th><th>score</th></tr>{color_rows}</table>
    </div>
    <div class="card">
      <h3>By time control</h3>
      <table><tr><th>class</th><th>games</th><th>score</th></tr>{tc_rows}</table>
    </div>
  </div>

  <h2>Rating over time</h2>
  <p class="note">Hover a point to see the exact rating and timestamp. Drag to zoom, double-click to reset.</p>
  <div class="chart">{divs["rating"]}</div>

  <h2>Form (rolling 5-game score)</h2>
  <p class="note">A moving average of my score over the last 5 games. Above 50% means I'm on a heater.</p>
  <div class="chart">{divs["rolling"]}</div>

  <h2>When I play, and how I do</h2>
  <p class="note">Heatmap of games by day of week and hour, colored by score % in each cell.</p>
  <div class="chart">{divs["activity"]}</div>

  <h2>Score % by hour of day</h2>
  <div class="chart">{divs["hour"]}</div>

  <h2>How games ended</h2>
  <div class="chart">{divs["termination"]}</div>

  <h2>Game length distribution</h2>
  <p class="note">Move counts bucketed by outcome. Do I lose long games or blow out early?</p>
  <div class="chart">{divs["length"]}</div>

  <h2>Actual vs Elo-expected score</h2>
  <p class="note">
    Score by opponent rating gap, next to the score Elo predicts for that gap. Positive delta means overperformance.
  </p>
  <div class="chart">{divs["gap"]}</div>
  <div class="card" style="margin-top:0.75rem;">
    <table>
      <tr><th>rating gap</th><th>games</th><th>actual</th><th>expected</th><th>delta</th></tr>
      {gap_rows}
    </table>
  </div>

  <h2>Openings</h2>
  <div class="chart">{divs["openings"]}</div>

  <h2>Most-played opponents</h2>
  <div class="card">
    <table>
      <tr><th>opponent</th><th>games</th><th>score</th><th>avg rating</th></tr>
      {h2h_rows}
    </table>
  </div>

  <h2>Recent games</h2>
  <div class="card">
    <table>
      <tr><th>played</th><th>color</th><th>opponent</th><th>result</th><th>opening</th></tr>
      {recent_rows}
    </table>
  </div>

  <footer>
    Data pulled from the chess.com public API. Refreshed every ~30 minutes.<br>
    Last updated {ctx["updated_at"]} · <a href="https://github.com/SammyBolger/chess-analytics">source on GitHub</a>
  </footer>

</div>
</body>
</html>
"""


# ---------- streaks ----------

def compute_streaks(df: pd.DataFrame) -> dict:
    """Longest consecutive win and loss streaks over the whole game history."""
    d = df.sort_values("played_at").copy()
    longest_win = cur_win = 0
    longest_loss = cur_loss = 0
    for r in d["my_result"]:
        if r == "win":
            cur_win += 1
            cur_loss = 0
            longest_win = max(longest_win, cur_win)
        elif r in ("checkmated", "timeout", "resigned", "abandoned"):
            cur_loss += 1
            cur_win = 0
            longest_loss = max(longest_loss, cur_loss)
        else:
            cur_win = cur_loss = 0
    return {"longest_win": longest_win, "longest_loss": longest_loss}


# ---------- main ----------

def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit("no duckdb file, run transform.py first")

    DOCS_DIR.mkdir(exist_ok=True)
    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.create_function("score", score, ["VARCHAR"], "DOUBLE")

    row = con.execute("""
        SELECT
            COUNT(*),
            SUM(CASE WHEN my_result = 'win' THEN 1 ELSE 0 END),
            SUM(CASE WHEN my_result IN ('checkmated','timeout','resigned','abandoned') THEN 1 ELSE 0 END),
            ROUND(AVG(score(my_result)) * 100, 1)
        FROM games
    """).fetchone()
    overall = {"games": row[0], "wins": row[1], "losses": row[2], "score_pct": row[3]}
    print(f"overall: {overall['games']} games, {overall['wins']}W / {overall['losses']}L, score {overall['score_pct']}%")

    by_color = con.execute("""
        SELECT my_color,
               COUNT(*) AS games,
               CAST(ROUND(AVG(score(my_result)) * 100, 1) AS VARCHAR) || '%' AS score_pct
        FROM games GROUP BY my_color ORDER BY my_color
    """).df()

    by_tc = con.execute("""
        SELECT time_class,
               COUNT(*) AS games,
               CAST(ROUND(AVG(score(my_result)) * 100, 1) AS VARCHAR) || '%' AS score_pct
        FROM games GROUP BY time_class ORDER BY games DESC
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
        LIMIT 12
    """).df()

    h2h = con.execute("""
        SELECT opp_username,
               COUNT(*) AS games,
               CAST(ROUND(AVG(score(my_result)) * 100, 1) AS VARCHAR) || '%' AS score_pct,
               ROUND(AVG(opp_rating)) AS avg_opp_rating
        FROM games
        WHERE opp_username IS NOT NULL
        GROUP BY opp_username
        HAVING COUNT(*) >= 2
        ORDER BY games DESC
        LIMIT 8
    """).df()

    recent = con.execute("""
        SELECT played_at, my_color, opp_username, opp_rating, my_result, opening_name, url
        FROM games
        ORDER BY played_at DESC
        LIMIT 15
    """).df()

    full = con.execute("""
        SELECT played_at, time_class, my_color, my_result, my_rating, opp_rating,
               termination, opp_username, num_moves
        FROM games ORDER BY played_at
    """).df()

    # played_at is UTC in duckdb, convert to local so the hour-of-day chart lines up
    full["played_at"] = pd.to_datetime(full["played_at"]).dt.tz_convert(None)

    divs = {
        "rating": to_div(fig_rating(full), "rating"),
        "rolling": to_div(fig_rolling(full), "rolling"),
        "activity": to_div(fig_activity_heatmap(full), "activity", height=380),
        "hour": to_div(fig_score_by_hour(full), "hour"),
        "termination": to_div(fig_termination(full), "termination", height=360),
        "length": to_div(fig_game_length(full), "length"),
        "openings": to_div(fig_openings(openings), "openings", height=max(340, 32 * len(openings) + 80)),
    }
    gap_fig, gap_df = fig_perf_vs_gap(full)
    divs["gap"] = to_div(gap_fig, "gap")

    ctx = {
        "overall": overall,
        "by_color": by_color,
        "by_tc": by_tc,
        "openings": openings,
        "h2h": h2h,
        "recent": recent,
        "gap": gap_df,
        "streaks": compute_streaks(full),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    html = render_html(ctx, divs)
    (DOCS_DIR / "index.html").write_text(html)

    # remove the old matplotlib PNGs since the report is now fully interactive
    for stale in DOCS_DIR.glob("*.png"):
        stale.unlink()

    print(f"interactive dashboard written to {DOCS_DIR}/index.html")


if __name__ == "__main__":
    main()
