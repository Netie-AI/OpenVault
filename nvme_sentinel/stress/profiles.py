"""Enterprise-style SSD characterization job profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# JESD-style characterization: 4K rand read QD32 (IOPS), 4K rand write QD1 (latency),
# 128K seq (bandwidth), 70/30 mixed OLTP-like.


@dataclass(frozen=True, slots=True)
class JobProfile:
    """Immutable fio/diskspd job descriptor."""

    name: str
    rw: Literal["read", "write", "randread", "randwrite", "randrw", "rw"]
    block_size_kb: int
    io_depth: int
    num_jobs: int
    duration_sec: int
    read_percent: int | None = None
    direct: bool = True


SEQ_READ_128K_QD16 = JobProfile("seq_read", "read", 128, 16, 1, 60)
SEQ_WRITE_128K_QD16 = JobProfile("seq_write", "write", 128, 16, 1, 60)
RAND_READ_4K_QD32 = JobProfile("rand_read_4k", "randread", 4, 32, 4, 60)
RAND_WRITE_4K_QD1 = JobProfile("rand_write_4k_qd1", "randwrite", 4, 1, 1, 60)
MIXED_70_30_4K_QD32 = JobProfile(
    "mixed_70_30_4k",
    "randrw",
    4,
    32,
    4,
    60,
    read_percent=70,
)

STANDARD_PROFILES: tuple[JobProfile, ...] = (
    SEQ_READ_128K_QD16,
    SEQ_WRITE_128K_QD16,
    RAND_READ_4K_QD32,
    RAND_WRITE_4K_QD1,
    MIXED_70_30_4K_QD32,
)
