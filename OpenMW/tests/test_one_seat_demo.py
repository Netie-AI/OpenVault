"""One-seat demo script regression (OpenVault#32).

Exercises the same auto-safe path the buyer script runs — mocks/simulate only.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "one_seat_demo.py"


def _load_demo():
    spec = importlib.util.spec_from_file_location("one_seat_demo", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["one_seat_demo"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_one_seat_demo_path_emits_vault_freeroute_ship_deny(tmp_path: Path) -> None:
    demo = _load_demo()
    out = tmp_path / "evidence.json"
    evidence = demo.run_demo(out_path=out)
    assert evidence["ok"] is True
    assert evidence["claims"]["ht1_ht5"].startswith("human-only")
    assert out.is_file()

    by_name = {s["step"]: s for s in evidence["steps"]}
    assert by_name["vault_key"]["status"] == "ok"
    assert by_name["freeroute_empty_refuse"]["status"] == "ok"
    assert by_name["freeroute_sealed_refuse"]["status"] == "ok"
    assert by_name["ship_allow_local_demo_simulate"]["status"] == "ok"
    assert by_name["gate_deny"]["status"] == "ok"

    ship = by_name["ship_allow_local_demo_simulate"]
    assert ship["public_url"] == ""
    assert ship["mode"] == "simulated"
    assert ship["target"] == "local_demo"

    deny = by_name["gate_deny"]
    assert deny["allowed"] is False
    assert deny["reasons"]
