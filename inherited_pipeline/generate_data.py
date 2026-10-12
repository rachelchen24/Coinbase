"""
Generate ~18 months of brokerage data with the problems the inherited pipeline can't see.

Tables (data/*.parquet):
  users                 one row per user; country = CURRENT country (NULL for some)
  user_country_history  country validity ranges (a few users moved)
  devices               app installs; is_active = CURRENT state (retired_at is null)
  trades                one row per order; status is an undocumented int code
  trade_ledger          one row per execution from the clearing ledger: the independent source of truth
  reported_history      what the dashboard showed each week, i.e. v1 run with device state as of report day

The story, for the trend check to find:
  2024-01 .. 2024-09  stable: ~4% of app users have a second device
  2024-10-07          "multi-device sync" launch: second devices take off, so v1's fan-out grows
  2025-03-03          fractional shares launch: new status code 5, which v1's hard-coded (1, 3) ignores
  throughout          web-only users (no device), uninstalls and phone swaps (history drifts on rerun)
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

rng = np.random.default_rng(20250701)
OUT = Path(__file__).parent / "data"

START = pd.Timestamp("2024-01-01")
N_WEEKS = 78
END = START + pd.Timedelta(weeks=N_WEEKS)            # 2025-06-30, exclusive
AS_OF = pd.Timestamp("2025-07-01")                   # "today": device is_active reflects this moment
LAUNCH_SYNC = pd.Timestamp("2024-10-07")
LAUNCH_FRACTIONAL = pd.Timestamp("2025-03-03")
N_USERS = 30_000
COUNTRIES = ["US", "GB", "DE", "CA", "SG", "BR", "AU", "FR"]
COUNTRY_P = [0.45, 0.14, 0.10, 0.09, 0.07, 0.06, 0.05, 0.04]
P_NULL_COUNTRY, P_MOVER = 0.025, 0.015
P_TRADER, P_CANCEL_ONLY = 0.45, 0.03
P_WEB_ONLY, P_SECOND_DEVICE_PRE, P_REPLACE, P_UNINSTALL = 0.08, 0.04, 0.25, 0.07
P_SYNC_ADOPT, P_SYNC_PROMO, P_SYNC_TWO = 0.45, 0.25, 0.10

DAYS = (END - START).days
AS_OF_D = (AS_OF - START).days


def ts(days: np.ndarray) -> pd.Series:
    """Float days since START -> timestamp (UTC, stored naive like the source), whole seconds."""
    return pd.Series(START + pd.to_timedelta(np.round(np.asarray(days) * 86400), unit="s"))


# ------------------------------------------------------------------ users + country history
n_pre = int(N_USERS * 0.4)
signup = np.concatenate([rng.uniform(-365, 0, n_pre), DAYS * rng.uniform(0, 1, N_USERS - n_pre) ** 0.7])
user_ids = np.arange(100_001, 100_001 + N_USERS)
country = rng.choice(COUNTRIES, N_USERS, p=COUNTRY_P).astype(object)
country[rng.random(N_USERS) < P_NULL_COUNTRY] = None
movers = np.flatnonzero((rng.random(N_USERS) < P_MOVER) & pd.notna(country) & (signup < DAYS - 90))
users = pd.DataFrame({"user_id": user_ids, "country": country, "signup_at": ts(signup)})

hist, mover_set = [], set(movers.tolist())
for i in range(N_USERS):
    if i in mover_set:
        continue
    hist.append((user_ids[i], country[i], signup[i], None))
for i in movers:
    move = rng.uniform(max(signup[i], 0) + 30, DAYS - 30)
    old = rng.choice([c for c in COUNTRIES if c != country[i]])
    hist += [(user_ids[i], old, signup[i], move), (user_ids[i], country[i], move, None)]
history = pd.DataFrame(hist, columns=["user_id", "country", "from_d", "to_d"])
history["valid_from"] = ts(history["from_d"].astype(float))
history["valid_to"] = ts(history["to_d"].astype(float)).where(history["to_d"].notna())
history = history.drop(columns=["from_d", "to_d"])

# ------------------------------------------------------------------ devices
devices, web_only, uninstalled, sync_adopters = [], [], [], []
for i, uid in enumerate(user_ids):
    if rng.random() < P_WEB_ONLY:
        web_only.append(uid)
        continue
    s = signup[i]
    user_devs = [[s, None]]
    if rng.random() < P_SECOND_DEVICE_PRE:
        user_devs.append([s + rng.uniform(0, 60), None])
    if rng.random() < P_REPLACE:                                   # phone swap: retire one, install another
        r = rng.uniform(s, AS_OF_D)
        user_devs[0][1] = r
        user_devs.append([r, None])
    if rng.random() < P_UNINSTALL:                                 # gone from the app; may keep trading on web
        u = rng.uniform(s, AS_OF_D)
        user_devs = [[c, rt if rt is not None and rt < u else u] for c, rt in user_devs if c < u]
        uninstalled.append(uid)
    elif rng.random() < P_SYNC_ADOPT:
        launch_d = (LAUNCH_SYNC - START).days
        wait = rng.uniform(0, 14) if rng.random() < P_SYNC_PROMO else rng.exponential(90)
        a = max(s, launch_d) + wait
        if a < AS_OF_D:
            sync_adopters.append(uid)
            user_devs.append([a, None])
            if rng.random() < P_SYNC_TWO:
                user_devs.append([a + rng.uniform(0, 30), None])
    devices += [(uid, c, rt) for c, rt in user_devs if c < AS_OF_D]

dev = pd.DataFrame(devices, columns=["user_id", "c", "r"])
dev = pd.DataFrame({
    "device_id": np.arange(1, len(dev) + 1),
    "user_id": dev["user_id"],
    "platform": rng.choice(["ios", "android", "ipad", "desktop"], len(dev), p=[0.45, 0.35, 0.1, 0.1]),
    "created_at": ts(dev["c"].to_numpy()),
    "retired_at": ts(dev["r"].astype(float).to_numpy()).where(dev["r"].notna()),
})
dev["is_active"] = dev["retired_at"].isna().astype(int)

# ------------------------------------------------------------------ trades
day_w = np.where(pd.date_range(START, periods=DAYS).dayofweek < 5, 1.0, 0.25)   # quieter weekends
t_user, t_day, t_cancel_only = [], [], []
cancel_only_users = []
for i, uid in enumerate(user_ids):
    if rng.random() >= P_TRADER:
        continue
    lo = max(signup[i], 0)
    hi = rng.uniform(lo, DAYS) if rng.random() < 0.35 else DAYS                 # some traders churn
    first, last = int(np.ceil(lo)), int(np.floor(hi))
    if last <= first:
        continue
    rate = min(rng.lognormal(np.log(0.22), 0.8), 3.0)
    n = rng.poisson(rate * (last - first))
    if n == 0:
        continue
    w = day_w[first:last]
    days = rng.choice(np.arange(first, last), n, p=w / w.sum()) + rng.uniform(13 / 24, 21 / 24, n)
    co = rng.random() < P_CANCEL_ONLY
    if co:
        cancel_only_users.append(uid)
    t_user.append(np.full(n, uid))
    t_day.append(days)
    t_cancel_only.append(np.full(n, co))

t_user, t_day, t_co = map(np.concatenate, (t_user, t_day, t_cancel_only))
order = np.argsort(t_day)
t_user, t_day, t_co = t_user[order], t_day[order], t_co[order]
n_trades = len(t_user)
status = rng.choice([1, 2, 3, 4], n_trades, p=[0.78, 0.10, 0.07, 0.05])
status[t_co] = rng.choice([2, 4], t_co.sum(), p=[0.7, 0.3])
created = ts(t_day)
fractional = (created >= LAUNCH_FRACTIONAL).to_numpy() & (status == 1) & (rng.random(n_trades) < 0.15)
status[fractional] = 5
qty = np.round(rng.uniform(1, 100, n_trades), 4)
qty[fractional] = np.round(rng.uniform(0.01, 0.99, fractional.sum()), 4)
filled = np.select([np.isin(status, [1, 5]), status == 3], [qty, np.round(qty * rng.uniform(0.1, 0.9, n_trades), 4)], 0.0)
trades = pd.DataFrame({"trade_id": np.arange(1, n_trades + 1), "user_id": t_user, "created_at": created,
                       "status": status, "quantity": qty, "filled_quantity": filled})

# ------------------------------------------------------------------ ledger: one row per execution of a filled order
fills = trades[trades["status"].isin([1, 3, 5])]
n_exec = np.where(fills["status"] == 1, rng.choice([1, 2], len(fills), p=[0.7, 0.3]), 1)
ledger = fills.loc[fills.index.repeat(n_exec), ["trade_id", "user_id", "created_at", "filled_quantity"]].copy()
ledger["executed_at"] = ledger["created_at"] + pd.to_timedelta(rng.uniform(0.1, 30, len(ledger)).round(3), unit="s")
ledger["filled_quantity"] = ledger["filled_quantity"] / ledger.groupby("trade_id")["trade_id"].transform("size")
ledger = ledger.drop(columns="created_at").reset_index(drop=True)
ledger.insert(0, "ledger_id", np.arange(1, len(ledger) + 1))


# ------------------------------------------------------------------ v1 as the dashboard ran it each week
def v1_totals(week_trades: pd.DataFrame, device_counts: pd.Series) -> tuple[int, int]:
    n = week_trades["user_id"].map(device_counts).fillna(0)
    joined = week_trades[n > 0]
    return joined["user_id"].nunique(), int((n * week_trades["status"].isin([1, 3])).sum())


def active_counts(at: pd.Timestamp) -> pd.Series:
    live = (dev["created_at"] <= at) & (dev["retired_at"].isna() | (dev["retired_at"] > at))
    return dev[live].groupby("user_id").size()


current_counts = dev[dev["is_active"] == 1].groupby("user_id").size()
weeks = pd.date_range(START, periods=N_WEEKS, freq="7D")
trade_week = START + pd.to_timedelta((trades["created_at"] - START).dt.days // 7 * 7, unit="D")
by_week = dict(tuple(trades.groupby(trade_week)))
rows = []
for w in weeks:
    run_at = w + pd.Timedelta(days=8, hours=6)                     # Tuesday-morning refresh after week end
    at, ct = v1_totals(by_week[w], active_counts(run_at))
    rows.append((w.date(), run_at, at, ct))
reported = pd.DataFrame(rows, columns=["week_start", "reported_at", "active_traders", "completed_trades"])

# ------------------------------------------------------------------ ground truth (pandas, independent of the SQL)
def week_truth(w: pd.Timestamp) -> dict:
    wt = by_week[w]
    n = wt["user_id"].map(current_counts).fillna(0)
    c13 = wt["status"].isin([1, 3])
    has_dev = n > 0
    v1_users = set(wt.loc[has_dev, "user_id"])
    completers = set(wt.loc[has_dev & c13, "user_id"])
    v1_ct = int((n * c13).sum())
    return {
        "v1_active_traders_rerun_today": len(v1_users),
        "v1_completed_trades_rerun_today": v1_ct,
        "completed_trades_deduped_same_population": int((c13 & has_dev).sum()),
        "fanout_extra_rows": v1_ct - int((c13 & has_dev).sum()),
        "traders_without_active_device": int(wt.loc[~has_dev, "user_id"].nunique()),
        "v1_active_traders_with_zero_completed": len(v1_users - completers),
        "status_5_trades": int((wt["status"] == 5).sum()),
        "ledger_completed_trades": int(wt["status"].isin([1, 3, 5]).sum()),
        "ledger_traders": int(wt.loc[wt["status"].isin([1, 3, 5]), "user_id"].nunique()),
    }


rerun = pd.DataFrame([(w.date(), *v1_totals(by_week[w], current_counts)) for w in weeks],
                     columns=["week_start", "active_traders", "completed_trades"])
drift = reported.merge(rerun, on="week_start", suffixes=("_reported", "_rerun"))
truth = {
    "weeks": [str(weeks[0].date()), str(weeks[-1].date())],
    "as_of": str(AS_OF.date()),
    "rows": {"users": len(users), "devices": len(dev), "trades": len(trades), "trade_ledger": len(ledger)},
    "launch_multi_device_sync": str(LAUNCH_SYNC.date()),
    "launch_fractional_status_5": str(LAUNCH_FRACTIONAL.date()),
    "users_web_only_no_device": len(web_only),
    "users_uninstalled_no_active_device": len(uninstalled),
    "users_without_active_device": int(N_USERS - current_counts.size),
    "sync_adopters": len(sync_adopters),
    "cancel_only_traders": len(cancel_only_users),
    "users_null_country": int(pd.isna(country).sum()),
    "users_moved_country": len(movers),
    "users_duplicated_in_users_table": 0,
    "weeks_where_rerun_differs_from_reported": int(((drift["active_traders_reported"] != drift["active_traders_rerun"])
                                                    | (drift["completed_trades_reported"] != drift["completed_trades_rerun"])).sum()),
    "first_week": week_truth(weeks[0]),
    "last_week": week_truth(weeks[-1]),
}

if __name__ in ("__main__", "generate"):
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect()
    for name, df in {"users": users, "user_country_history": history, "devices": dev, "trades": trades,
                     "trade_ledger": ledger, "reported_history": reported}.items():
        con.register("df", df)
        con.execute(f"copy (select * from df) to '{(OUT / name).as_posix()}.parquet' (format parquet)")
        con.unregister("df")
    (OUT / "ground_truth.json").write_text(json.dumps(truth, indent=2))
    print(f"Generated {', '.join(f'{k}: {v:,}' for k, v in truth['rows'].items())}")
    print("Injected (ground truth):")
    for k, v in truth.items():
        if k not in ("rows",):
            print(f"  {k}: {v}")
