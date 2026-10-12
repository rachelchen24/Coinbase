"""
Step 6: trend check against the period we trust, plus reconciliation against the ledger.

Model: OLS on the weekly series, trained on the stable period only (before 2024-07-01), projected forward
with a 95% prediction interval. The detector is a RELATIONSHIP between metrics, completed trades per active
trader, because it is insensitive to business growth. A trend on raw volume is also fitted, only to show it
can't tell growth from a bug: it fires on the ledger too.
Weekly grain on purpose: the metric is weekly, so day-of-week seasonality cancels inside each week, and 26
training weeks cannot identify annual seasonality. A model with terms it cannot estimate would only widen
the interval.

    python trend_check.py      # standalone; run.py calls run(con)
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm

from lib import OUT, connect

TRAIN_END = pd.Timestamp("2024-07-01")    # stable period: before any known product change
RUN_LENGTH = 3                            # consecutive weeks outside the interval before we call it
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#898781", "#e1e0d9", "#fcfcfb"
PIPELINE, LEDGER = "#2a78d6", "#eb6834"   # categorical slots 1 and 2 of the reference palette


def weekly_series(con) -> pd.DataFrame:
    return con.execute("""
        with ledger as (
          select cast(date_trunc('week', t.created_at) as date) as week_start,
                 count(distinct l.trade_id) as ledger_completed_trades,
                 count(distinct l.user_id) as ledger_traders
          from trade_ledger l join trades t on t.trade_id = l.trade_id
          group by 1
        )
        select r.week_start, r.active_traders, r.completed_trades, l.ledger_completed_trades, l.ledger_traders
        from reported_history r join ledger l using (week_start)
        order by r.week_start""").df().assign(week_start=lambda d: pd.to_datetime(d["week_start"]))


def project(df: pd.DataFrame, y: pd.Series, log: bool) -> pd.DataFrame:
    train = df["week_start"] < TRAIN_END
    X = sm.add_constant(np.arange(len(df), dtype=float))
    target = np.log(y) if log else y
    frame = sm.OLS(target[train], X[train]).fit().get_prediction(X).summary_frame(alpha=0.05)
    out = frame[["mean", "obs_ci_lower", "obs_ci_upper"]].set_axis(["proj", "lo", "hi"], axis=1)
    out = np.exp(out) if log else out
    out["actual"] = y.to_numpy()
    out["outside"] = (~train.to_numpy()) & ((out["actual"] > out["hi"]) | (out["actual"] < out["lo"]))
    return out


def divergence(df: pd.DataFrame, p: pd.DataFrame) -> pd.Timestamp | None:
    """Onset = first week of the first run of RUN_LENGTH consecutive weeks outside the interval. A single
    week outside is expected ~5% of the time; a run is not."""
    runs = p["outside"].rolling(RUN_LENGTH).sum().shift(-(RUN_LENGTH - 1)) == RUN_LENGTH
    return df["week_start"].iloc[int(np.argmax(runs.to_numpy()))] if runs.any() else None


def chart(df: pd.DataFrame, ratio: pd.DataFrame, onset, path) -> None:
    plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED,
                         "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True, facecolor=SURFACE)
    x = df["week_start"]
    for ax in axes:
        ax.set_facecolor(SURFACE)
        ax.grid(axis="y", color=GRID, linewidth=0.6)
        ax.axvspan(x.iloc[0], TRAIN_END, color=MUTED, alpha=0.08, linewidth=0)
        if onset is not None:
            ax.axvline(onset, color=INK, linewidth=0.8)

    top, bottom = axes
    top.plot(x, df["completed_trades"], color=PIPELINE, linewidth=2, label="pipeline (v1, as reported)")
    top.plot(x, df["ledger_completed_trades"], color=LEDGER, linewidth=2, label="ledger (independent source)")
    top.set_title("Reconciliation: completed trades per week, pipeline vs. ledger", loc="left", color=INK, fontsize=10)

    bottom.fill_between(x, ratio["lo"], ratio["hi"], color=MUTED, alpha=0.2, linewidth=0,
                        label="95% prediction interval")
    bottom.plot(x, ratio["proj"], color=MUTED, linewidth=1.2, linestyle="--", label="projection from stable period")
    bottom.plot(x, ratio["actual"], color=PIPELINE, linewidth=2, label="pipeline (v1)")
    bottom.scatter(x[ratio["outside"]], ratio["actual"][ratio["outside"]], s=14, color=PIPELINE, zorder=3)
    bottom.plot(x, df["ledger_completed_trades"] / df["ledger_traders"], color=LEDGER, linewidth=2, label="ledger")
    bottom.set_title("Trend check: completed trades per active trader", loc="left", color=INK, fontsize=10)

    for ax in axes:
        ymax = ax.get_ylim()[1]
        ax.text(x.iloc[1], ymax, "training: stable period", color=MUTED, va="top")
        if onset is not None:
            ax.text(onset, ymax, f"  divergence onset {onset:%Y-%m-%d}", color=INK, va="top")
        ax.legend(loc="lower right", frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)


def run(con) -> dict:
    df = weekly_series(con)
    ratio = project(df, df["completed_trades"] / df["active_traders"], log=False)
    ledger_ratio = project(df, df["ledger_completed_trades"] / df["ledger_traders"], log=False)
    # level models, kept to show why they are NOT the detector: business growth trips them on the ledger too
    level = project(df, df["completed_trades"], log=True)
    ledger_level = project(df, df["ledger_completed_trades"], log=True)
    onset = divergence(df, ratio)
    OUT.mkdir(exist_ok=True)
    chart(df, ratio, onset, OUT / "trend_check.png")
    train = df["week_start"] < TRAIN_END
    gap = 100 * (df["completed_trades"] / df["ledger_completed_trades"] - 1)
    return {
        "training_weeks": int(train.sum()),
        "ratio_weeks_outside_interval": int(ratio["outside"].sum()),
        "ratio_onset": onset,
        "ledger_ratio_weeks_outside_interval": int(ledger_ratio["outside"].sum()),
        "level_weeks_outside_interval": int(level["outside"].sum()),
        "ledger_level_weeks_outside_interval": int(ledger_level["outside"].sum()),
        "gap_vs_ledger_training_mean_pct": round(float(gap[train].mean()), 1),
        "gap_vs_ledger_training_sd_pct": round(float(gap[train].std()), 1),
        "gap_vs_ledger_peak_pct": round(float(gap.max()), 1),
        "gap_vs_ledger_peak_week": df["week_start"][gap.idxmax()],
        "gap_vs_ledger_last_pct": round(float(gap.iloc[-1]), 1),
        "series": df.assign(ratio=ratio["actual"].round(3), ratio_projection=ratio["proj"].round(3),
                            ratio_lo=ratio["lo"].round(3), ratio_hi=ratio["hi"].round(3),
                            ratio_outside=ratio["outside"], gap_vs_ledger_pct=gap.round(1)),
    }


if __name__ == "__main__":
    res = run(connect())
    print({k: v for k, v in res.items() if k != "series"})
