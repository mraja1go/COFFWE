"""Ground-truth storage: the simulator's continuous state as Parquet files.

Layout:  data/sim/<run_id>/<dataset>/year=2026/month=3/<file>.parquet
         data/sim/<run_id>/run.json

Partitioning by year/month (the "hive-style" key=value folder names) lets readers
load just the months they need; pandas/pyarrow understand the layout natively.

This is *truth* -- what really happened on the estate. Phase 3's sensors will report
a noisy, gappy version of it, and the digital twin will be judged against this.
"""

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd

DEFAULT_ROOT = Path("data/sim")


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    seed: int
    start: str  # ISO dates; physics runs over [start, end)
    end: str
    events_start: str  # first day the simulator writes Postgres events
    datasets: list[str]


def run_id_for(seed: int, start: date, end: date) -> str:
    return f"{start:%Y%m%d}-{end:%Y%m%d}-seed{seed}"


class TruthStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    @classmethod
    def create(cls, root: Path, run_id: str) -> TruthStore:
        """A fresh, empty run directory (an earlier run with the same id is replaced)."""
        run_dir = root / run_id
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True)
        return cls(run_dir)

    def write(self, dataset: str, df: pd.DataFrame) -> None:
        """Write a time-indexed frame, partitioned by the index's year and month."""
        ts = df.index
        out = df.reset_index()
        out["year"], out["month"] = ts.year, ts.month
        out.to_parquet(self.run_dir / dataset, partition_cols=["year", "month"], index=False)

    def read(self, dataset: str) -> pd.DataFrame:
        df = pd.read_parquet(self.run_dir / dataset)
        # Partition columns come back as categories; drop them, restore the time index.
        df = df.drop(columns=["year", "month"])
        time_col = df.columns[0]
        return df.sort_values(time_col).set_index(time_col)

    def write_info(self, info: RunInfo) -> None:
        (self.run_dir / "run.json").write_text(json.dumps(asdict(info), indent=2) + "\n")

    def read_info(self) -> RunInfo:
        return RunInfo(**json.loads((self.run_dir / "run.json").read_text()))
