"""The wire format: what a virtual device publishes, validated with Pydantic.

The same model is used on both sides -- devices build messages with it, ingestion
parses incoming payloads with it -- so a malformed message is caught at the boundary
instead of corrupting the database.
"""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

TOPIC_ROOT = "coffwe/v1"


class Reading(BaseModel):
    """One sensor reading, as published on coffwe/v1/<site>/<station>/<measurement>."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sensor: str = Field(min_length=3, max_length=40)  # "FS-B3-soil_moisture"
    measured_at: AwareDatetime  # the device's own timestamp, in sim time
    value: float  # may be out of physical range: ingestion flags it, never drops it
    unit: str = Field(max_length=10)
    seq: int = Field(ge=0)  # per-device counter: gaps reveal lost messages
    battery_v: float | None = Field(default=None, ge=0, le=6)
    # When the station/gateway actually transmitted it (sim time). Later than
    # measured_at for store-and-forward bursts. Lateness is judged from this, not
    # from when ingestion happened to process it (which, at 3600x, a fraction of a
    # real second of pipeline delay would turn into simulated minutes).
    sent_at: AwareDatetime | None = None


class StationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    station: str
    state: Literal["online", "offline"]


class ClockState(BaseModel):
    """The estate clock: sim_time was the simulated time at real time wall_epoch.

    Any process computes "now" as sim_time + (time.time() - wall_epoch) * speed (when
    running) -- so all processes agree without exchanging a message every tick.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    sim_time: AwareDatetime
    speed: float = Field(gt=0)
    running: bool
    wall_epoch: float  # Unix time (seconds) at which sim_time held


class ClockCommand(BaseModel):
    """Published on coffwe/v1/clock/set by an operator: change speed, pause, resume."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    speed: float | None = Field(default=None, gt=0)
    running: bool | None = None


def reading_topic(site: str, station: str, measurement: str) -> str:
    return f"{TOPIC_ROOT}/{site}/{station}/{measurement}"


def status_topic(site: str, station: str) -> str:
    return f"{TOPIC_ROOT}/{site}/{station}/status"


CLOCK_TOPIC = f"{TOPIC_ROOT}/clock"
CLOCK_SET_TOPIC = f"{CLOCK_TOPIC}/set"


def parse_reading(payload: bytes | str) -> Reading:
    return Reading.model_validate_json(payload)


__all__ = [
    "CLOCK_SET_TOPIC",
    "CLOCK_TOPIC",
    "ClockCommand",
    "ClockState",
    "Reading",
    "StationStatus",
    "datetime",
    "parse_reading",
    "reading_topic",
    "status_topic",
]
