"""Reproducible randomness: one seed per run, an independent stream per subsystem.

If every subsystem shared one random generator, adding a single random draw to the
pest model would shift every number the weather model receives afterwards -- the
same seed would suddenly produce different weather. Instead each subsystem gets its
own stream, derived from (run seed, subsystem name). Streams are statistically
independent, and adding a new subsystem never disturbs the existing ones.
"""

import zlib

import numpy as np


def stream(seed: int, name: str) -> np.random.Generator:
    """The random generator for one subsystem, e.g. stream(42, "weather")."""
    # spawn_key namespaces the seed by subsystem; crc32 makes the name a stable integer
    # (Python's built-in hash() of a string changes between interpreter runs).
    key = zlib.crc32(name.encode())
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=(key,)))
