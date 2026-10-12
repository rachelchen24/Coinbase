"""Shared helpers: connect to the data, render {{ params }} into SQL files, run them."""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).parent
DATA, SQL, OUT, SEEDS = ROOT / "data", ROOT / "sql", ROOT / "output", ROOT / "seeds"
TABLES = ("users", "user_country_history", "devices", "trades", "trade_ledger", "reported_history")


def connect(data_dir: Path = DATA) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for t in TABLES:
        con.execute(f"create view {t} as select * from read_parquet('{(data_dir / t).as_posix()}.parquet')")
    con.execute(f"create table trade_status as select * from read_csv('{(SEEDS / 'trade_status.csv').as_posix()}', "
                "header = true)")
    return con


def week(week_start: str | date) -> dict:
    start = date.fromisoformat(str(week_start))
    return {"week_start": start.isoformat(), "week_end": (start + timedelta(days=7)).isoformat()}


def render(sql: str, **params) -> str:
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(params[m.group(1)]), sql)


def run(con: duckdb.DuckDBPyConnection, name: str, **params) -> pd.DataFrame:
    df = con.execute(render((SQL / f"{name}.sql").read_text(), **params)).df()
    # SUM() over ints comes back as HUGEINT -> float64 in pandas; show counts as integers (display only)
    for c in df.select_dtypes("float").columns:
        if df[c].dropna().eq(df[c].dropna().round()).all() and not c.endswith(("_pct", "_per_trade")):
            df[c] = df[c].astype("Int64")
    return df


def weeks(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [str(d) for d in con.execute("select week_start from reported_history order by 1").df()["week_start"]
            .dt.date]
