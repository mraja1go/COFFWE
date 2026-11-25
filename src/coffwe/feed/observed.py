"""Generate and store a run's observed sensor feed next to its ground truth.

    data/sim/<run_id>/observed_messages/   what reaches the broker, in delivery order
    data/sim/<run_id>/observed_labels/     every scheduled reading: truth, value, fault, fate

The M2 publishers replay observed_messages, paced by the sim clock. The labels never
go over the wire -- they're the answer key for scoring the twin and Phase 6 models.
"""

from pathlib import Path

import pandas as pd

from coffwe.feed.devices import observe
from coffwe.feed.truth import ParquetTruthSource
from coffwe.sim.truth import TruthStore


def generate(run_dir: Path, seed: int | None = None) -> tuple[int, int]:
    store = TruthStore(run_dir)
    seed = store.read_info().seed if seed is None else seed
    messages, labels = observe(ParquetTruthSource(run_dir, seed), seed)
    store.write("observed_messages", messages.set_index("delivered_at"))
    store.write("observed_labels", labels.set_index("measured_at"))
    return len(messages), len(labels)


def load_messages(run_dir: Path) -> pd.DataFrame:
    return TruthStore(run_dir).read("observed_messages").reset_index()
