"""Model manager tests — mocked Hugging Face hub, no network."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openmw.device_profile import DeviceProfile
from openmw.model_manager import (
    DownloadOutcome,
    InsufficientDiskSpaceError,
    IntegrityError,
    LicenseGateError,
    download,
    auto_select_and_download,
    delete,
    list_local,
    register_local,
    repo_id_from_url,
    verify,
)
from openmw.model_router import ModelRouter, RoutingDecision


def _profile(*, gpu_vram_gb: float = 10.0, system_ram_gb: float = 32.0) -> DeviceProfile:
    return DeviceProfile(
        gpu_name="Test GPU",
        gpu_vram_gb=gpu_vram_gb,
        gpu_bandwidth_gbps=360.0,
        system_ram_gb=system_ram_gb,
        cpu_cores=8,
        nvme_model="Mock NVMe",
        nvme_seq_read_gbps=3.5,
        nvme_endurance_tbw=600.0,
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Sibling:
    def __init__(self, *, filename: str, size: int, sha256: str | None) -> None:
        self.rfilename = filename
        if sha256 is None:
            self.lfs = None
            self.size = size
        else:
            self.lfs = MagicMock(size=size, sha256=sha256)
            self.size = size


def _mock_hf_api(
    *,
    repo_files: list[str],
    filename: str,
    size: int,
    sha256: str,
) -> MagicMock:
    api = MagicMock()
    api.list_repo_files.return_value = repo_files
    repo_info = MagicMock()
    repo_info.siblings = [_Sibling(filename=filename, size=size, sha256=sha256)]
    api.repo_info.return_value = repo_info
    return api


@pytest.fixture
def models_dir(tmp_path: Path) -> Path:
    return tmp_path / "models"


@pytest.fixture
def router() -> ModelRouter:
    return ModelRouter()


class TestRepoParsing:
    def test_repo_id_from_hf_url(self) -> None:
        url = "https://huggingface.co/microsoft/Phi-4-mini-instruct-gguf"
        assert repo_id_from_url(url) == "microsoft/Phi-4-mini-instruct-gguf"


class TestLicenseGate:
    def test_mit_model_downloads(self, models_dir: Path, router: ModelRouter, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = b"gguf-payload"
        digest = _sha256_bytes(payload)
        hf_filename = "Phi-4-mini-instruct-Q4_K_M.gguf"
        api = _mock_hf_api(
            repo_files=[hf_filename],
            filename=hf_filename,
            size=len(payload),
            sha256=digest,
        )

        cache_file = models_dir / "cache" / hf_filename
        cache_file.parent.mkdir(parents=True)
        cache_file.write_bytes(payload)

        monkeypatch.setattr(
            "openmw.model_manager.hf_hub_download",
            lambda **kwargs: str(cache_file),
        )
        monkeypatch.setattr("openmw.model_manager.shutil.disk_usage", lambda _path: MagicMock(free=10**12))

        outcome = download(
            "phi-4-mini",
            profile=_profile(gpu_vram_gb=4.0),
            models_dir=models_dir,
            router=router,
            hf_api=api,
        )

        assert isinstance(outcome, DownloadOutcome)
        assert outcome.skipped is False
        assert outcome.path == models_dir / "phi-4-mini" / f"{outcome.quant_level}.gguf"
        assert outcome.path.exists()

    def test_gated_license_raises_before_download(
        self,
        models_dir: Path,
        router: ModelRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "openmw.model_manager.hf_hub_download",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not download")),
        )
        with pytest.raises(LicenseGateError, match="HF_TOKEN"):
            download("llama-3.3-8b", quant_level="Q4_K_M", models_dir=models_dir, router=router)


class TestDownloadAndVerify:
    def test_verify_detects_hash_mismatch(self, models_dir: Path) -> None:
        model_dir = models_dir / "phi-4-mini"
        model_dir.mkdir(parents=True)
        gguf_path = model_dir / "Q4_K_M.gguf"
        gguf_path.write_bytes(b"bad")
        metadata = {
            "model_id": "phi-4-mini",
            "quants": {
                "Q4_K_M": {
                    "filename": "Q4_K_M.gguf",
                    "sha256": "a" * 64,
                    "size_bytes": 3,
                }
            },
        }
        (model_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

        with pytest.raises(IntegrityError):
            verify("phi-4-mini", "Q4_K_M", models_dir=models_dir)

    def test_download_skips_verified_existing_file(
        self,
        models_dir: Path,
        router: ModelRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        payload = b"existing"
        digest = _sha256_bytes(payload)
        model_dir = models_dir / "qwen2.5-1.5b"
        model_dir.mkdir(parents=True)
        (model_dir / "Q4_K_M.gguf").write_bytes(payload)
        metadata = {
            "model_id": "qwen2.5-1.5b",
            "quants": {
                "Q4_K_M": {
                    "filename": "Q4_K_M.gguf",
                    "sha256": digest,
                    "size_bytes": len(payload),
                }
            },
        }
        (model_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

        monkeypatch.setattr(
            "openmw.model_manager.hf_hub_download",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not download")),
        )

        progress: list[float] = []
        outcome = download(
            "qwen2.5-1.5b",
            quant_level="Q4_K_M",
            models_dir=models_dir,
            router=router,
            progress_callback=progress.append,
        )
        assert outcome.skipped is True
        assert progress == [100.0]

    def test_disk_space_precheck(
        self,
        models_dir: Path,
        router: ModelRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        hf_filename = "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
        api = _mock_hf_api(
            repo_files=[hf_filename],
            filename=hf_filename,
            size=8 * 1024**3,
            sha256="b" * 64,
        )
        monkeypatch.setattr(
            "openmw.model_manager.shutil.disk_usage",
            lambda _path: MagicMock(free=1024),
        )
        with pytest.raises(InsufficientDiskSpaceError):
            download(
                "qwen2.5-1.5b",
                quant_level="Q4_K_M",
                models_dir=models_dir,
                router=router,
                hf_api=api,
            )


class TestAutoSelect:
    def test_auto_select_small_tier_downloads_qwen9b(
        self,
        models_dir: Path,
        router: ModelRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        payload = b"qwen9b"
        digest = _sha256_bytes(payload)
        hf_filename = "Qwen3.5-9B-Q4_K_M.gguf"
        api = _mock_hf_api(
            repo_files=[hf_filename],
            filename=hf_filename,
            size=len(payload),
            sha256=digest,
        )
        cache_file = models_dir / "cache" / hf_filename
        cache_file.parent.mkdir(parents=True)
        cache_file.write_bytes(payload)

        monkeypatch.setattr(
            "openmw.model_manager.hf_hub_download",
            lambda **kwargs: str(cache_file),
        )
        monkeypatch.setattr("openmw.model_manager.shutil.disk_usage", lambda _path: MagicMock(free=10**12))

        outcome = auto_select_and_download(
            _profile(gpu_vram_gb=10.0),
            models_dir=models_dir,
            router=router,
            hf_api=api,
        )

        assert outcome.model_id == "qwen3.5-9b"
        assert isinstance(outcome.routing, RoutingDecision)
        assert outcome.path.exists()

    def test_auto_select_raises_for_gated_tier_default_override(
        self,
        models_dir: Path,
        router: ModelRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "openmw.model_manager._TIER_DEFAULT_MODEL",
            {"SMALL": "llama-3.3-8b"},
        )
        with pytest.raises(LicenseGateError):
            auto_select_and_download(
                _profile(gpu_vram_gb=10.0),
                models_dir=models_dir,
                router=router,
            )


class TestLocalRegistry:
    def test_register_list_delete_round_trip(self, models_dir: Path, tmp_path: Path) -> None:
        source = tmp_path / "custom.gguf"
        source.write_bytes(b"registered")

        record = register_local(
            "mistral-7b-v0.3",
            "Q4_K_M",
            source,
            models_dir=models_dir,
        )
        assert record.gguf_path.exists()

        records = list_local(models_dir=models_dir)
        assert len(records) == 1
        assert records[0].model_id == "mistral-7b-v0.3"
        assert records[0].quant_level == "Q4_K_M"
        assert verify("mistral-7b-v0.3", "Q4_K_M", models_dir=models_dir)

        delete("mistral-7b-v0.3", "Q4_K_M", models_dir=models_dir)
        assert list_local(models_dir=models_dir) == []

    def test_delete_entire_model_directory(self, models_dir: Path, tmp_path: Path) -> None:
        source = tmp_path / "custom.gguf"
        source.write_bytes(b"registered")
        register_local("mistral-7b-v0.3", "Q4_K_M", source, models_dir=models_dir)
        register_local("mistral-7b-v0.3", "Q8_0", source, models_dir=models_dir)

        delete("mistral-7b-v0.3", models_dir=models_dir)
        assert not (models_dir / "mistral-7b-v0.3").exists()
