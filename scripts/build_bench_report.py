import json
from pathlib import Path

from nvme_sentinel.bench import build_bench_run_report
from nvme_sentinel.snapshot.collect import load_snapshot
from nvme_sentinel.stress.fio import parse_fio_json

before = load_snapshot("reports/baseline_biwin_after.json")
after = load_snapshot("reports/baseline_biwin_postwrite.json")
stress_raw = json.loads(Path("reports/rand_write_4k_qd1.json").read_text(encoding="utf-8"))
stress = parse_fio_json(stress_raw, "rand_write_4k_qd1")
build_bench_run_report(
    r"\\.\PhysicalDrive1",
    before,
    after,
    stress_result=stress,
    enclosure_class="usb-bridge-degraded",
    html_output=Path("reports/bench_biwin_001.html"),
)
print("Wrote", Path("reports/bench_biwin_001.html").resolve())
