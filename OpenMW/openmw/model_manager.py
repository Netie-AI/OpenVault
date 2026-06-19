"""GGUF model download, integrity verification, and local registry management."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import structlog
from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError

from openmw.device_profile import DeviceProfile
from openmw.model_router import HardwareTier, ModelRouter, RoutingDecision, load_registry

log = structlog.get_logger()

_DEFAULT_MODELS_DIR = Path.home() / ".openmw" / "models"
_OPEN_LICENSES = frozenset({"MIT", "Apache-2.0"})
_DISK_HEADROOM_BYTES = 512 * 1024 * 1024

_TIER_DEFAULT_MODEL: dict[HardwareTier, str] = {
    "NANO": "phi-4-mini",
    "SMALL": "qwen3.5-9b",
    "MID": "qwen2.5-14b",
    "LARGE": "qwen3-32b",
    "XLARGE": "qwen2.5-72b",
}

ProgressCallback = Callable[[float], None]


class ModelManagerError(Exception):
    """Base error for model manager operations."""


class LicenseGateError(ModelManagerError):
    """Raised when a model license requires Hugging Face authentication."""


class InsufficientDiskSpaceError(ModelManagerError):
    """Raised when free disk space is below the download requirement."""


class IntegrityError(ModelManagerError):
    """Raised when a local GGUF fails SHA-256 verification."""


class ModelFileNotFoundError(ModelManagerError):
    """Raised when no matching GGUF exists in the Hugging Face repo."""


@dataclass(frozen=True)
class LocalModelRecord:
    """One locally registered quant variant."""

    model_id: str
    quant_level: str
    gguf_path: Path
    metadata_path: Path
    sha256: str | None
    size_bytes: int


@dataclass(frozen=True)
class DownloadOutcome:
    """Result of a download or auto-select-and-download operation."""

    model_id: str
    quant_level: str
    path: Path
    routing: RoutingDecision
    skipped: bool


def default_models_dir() -> Path:
    """Return the default OpenMW model storage root."""
    return _DEFAULT_MODELS_DIR


def list_local(*, models_dir: Path | None = None) -> list[LocalModelRecord]:
    """List GGUF files registered under *models_dir*."""
    root = models_dir or default_models_dir()
    if not root.exists():
        return []

    records: list[LocalModelRecord] = []
    for model_dir in sorted(root.iterdir()):
        if not model_dir.is_dir():
            continue
        metadata_path = model_dir / "metadata.json"
        metadata = _read_metadata(metadata_path) if metadata_path.exists() else {}
        quants = metadata.get("quants", {})
        if isinstance(quants, dict) and quants:
            for quant_level, entry in quants.items():
                if not isinstance(entry, dict):
                    continue
                gguf_path = model_dir / str(entry.get("filename", f"{quant_level}.gguf"))
                if not gguf_path.exists():
                    continue
                records.append(
                    LocalModelRecord(
                        model_id=model_dir.name,
                        quant_level=str(quant_level),
                        gguf_path=gguf_path,
                        metadata_path=metadata_path,
                        sha256=_optional_str(entry.get("sha256")),
                        size_bytes=int(entry.get("size_bytes", gguf_path.stat().st_size)),
                    )
                )
            continue

        for gguf_path in sorted(model_dir.glob("*.gguf")):
            quant_level = gguf_path.stem
            records.append(
                LocalModelRecord(
                    model_id=model_dir.name,
                    quant_level=quant_level,
                    gguf_path=gguf_path,
                    metadata_path=metadata_path,
                    sha256=None,
                    size_bytes=gguf_path.stat().st_size,
                )
            )
    return records


def verify(
    model_id: str,
    quant_level: str,
    *,
    models_dir: Path | None = None,
    hf_api: HfApi | None = None,
    registry_path: Path | None = None,
) -> bool:
    """Verify local SHA-256 against stored metadata and Hugging Face repo metadata."""
    root = models_dir or default_models_dir()
    gguf_path = _local_gguf_path(root, model_id, quant_level)
    if not gguf_path.exists():
        return False

    actual = _file_sha256(gguf_path)
    metadata_path = _model_metadata_path(root, model_id)
    metadata = _read_metadata(metadata_path)
    quants = metadata.get("quants", {})
    entry: dict[str, object] | None = None
    expected: str | None = None
    hf_filename: str | None = None
    if isinstance(quants, dict):
        raw_entry = quants.get(quant_level)
        if isinstance(raw_entry, dict):
            entry = raw_entry
            expected = _optional_str(entry.get("sha256"))
            hf_filename = _optional_str(entry.get("hf_filename"))

    if expected is None:
        registry = load_registry(registry_path)
        if model_id in registry:
            spec = registry[model_id]
            repo_id = repo_id_from_url(spec.download_url)
            if hf_filename is None:
                hf_filename = _resolve_gguf_filename(
                    api=hf_api or HfApi(),
                    repo_id=repo_id,
                    quant_level=quant_level,
                )[0]
            expected = _expected_sha256(hf_api or HfApi(), repo_id, hf_filename)

    if expected is None:
        log.warning("verify_no_expected_hash", model_id=model_id, quant=quant_level)
        return True

    if actual != expected.lower():
        raise IntegrityError(
            f"SHA-256 mismatch for {model_id}/{quant_level}: expected {expected}, got {actual}"
        )
    return True


def delete(
    model_id: str,
    quant_level: str | None = None,
    *,
    models_dir: Path | None = None,
) -> None:
    """Delete one quant variant or an entire model directory."""
    root = models_dir or default_models_dir()
    model_dir = root / model_id
    if not model_dir.exists():
        return

    if quant_level is None:
        shutil.rmtree(model_dir)
        log.info("model_deleted", model_id=model_id, quant="all")
        return

    gguf_path = _local_gguf_path(root, model_id, quant_level)
    if gguf_path.exists():
        gguf_path.unlink()

    metadata_path = _model_metadata_path(root, model_id)
    metadata = _read_metadata(metadata_path)
    quants = metadata.get("quants")
    if isinstance(quants, dict) and quant_level in quants:
        del quants[quant_level]
        if quants:
            _write_metadata(metadata_path, metadata)
        elif metadata_path.exists():
            metadata_path.unlink()

    if model_dir.exists() and not any(model_dir.iterdir()):
        model_dir.rmdir()
    log.info("model_deleted", model_id=model_id, quant=quant_level)


def download(
    model_id: str,
    *,
    quant_level: str | None = None,
    profile: DeviceProfile | None = None,
    models_dir: Path | None = None,
    progress_callback: ProgressCallback | None = None,
    router: ModelRouter | None = None,
    hf_api: HfApi | None = None,
    registry_path: Path | None = None,
) -> DownloadOutcome:
    """Download a GGUF for *model_id*; skip when a verified copy already exists."""
    resolved_router = router or ModelRouter(registry_path=registry_path)
    if model_id not in resolved_router.registry:
        raise KeyError(f"unknown model_id: {model_id}")

    spec = resolved_router.registry[model_id]
    _check_license_gate(spec.license, model_id)

    if quant_level is None:
        if profile is None:
            raise ValueError("quant_level or profile is required for download")
        quant_level = resolved_router.route(profile, model_id).quant_level

    root = models_dir or default_models_dir()
    dest_path = _local_gguf_path(root, model_id, quant_level)
    routing = (
        resolved_router.route(profile, model_id)
        if profile is not None
        else resolved_router.route(
            _synthetic_profile_for_quant(spec.tier),
            model_id,
        )
    )

    if dest_path.exists():
        try:
            if verify(
                model_id,
                quant_level,
                models_dir=root,
                hf_api=hf_api,
                registry_path=registry_path,
            ):
                if progress_callback is not None:
                    progress_callback(100.0)
                return DownloadOutcome(
                    model_id=model_id,
                    quant_level=quant_level,
                    path=dest_path,
                    routing=routing,
                    skipped=True,
                )
        except IntegrityError:
            log.warning("redownload_corrupt_gguf", model_id=model_id, quant=quant_level)
            dest_path.unlink(missing_ok=True)

    api = hf_api or HfApi()
    repo_id = repo_id_from_url(spec.download_url)
    hf_filename, size_bytes, expected_sha = _resolve_gguf_filename(
        api=api,
        repo_id=repo_id,
        quant_level=quant_level,
    )
    _check_disk_space(root, size_bytes)

    if progress_callback is not None:
        progress_callback(0.0)

    try:
        cached_path = Path(
            hf_hub_download(
                repo_id=repo_id,
                filename=hf_filename,
                repo_type="model",
            )
        )
    except GatedRepoError as exc:
        raise LicenseGateError(
            f"Model '{model_id}' requires Hugging Face authentication. "
            "Accept the license on the model repo and set HF_TOKEN."
        ) from exc
    except RepositoryNotFoundError as exc:
        raise ModelManagerError(f"Hugging Face repo not found for {model_id}: {repo_id}") from exc

    model_dir = root / model_id
    model_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cached_path, dest_path)

    if expected_sha is None:
        expected_sha = _expected_sha256(api, repo_id, hf_filename)
    actual_sha = _file_sha256(dest_path)
    if expected_sha is not None and actual_sha != expected_sha.lower():
        dest_path.unlink(missing_ok=True)
        raise IntegrityError(
            f"Downloaded file failed SHA-256 verification for {model_id}/{quant_level}"
        )

    metadata_path = _model_metadata_path(root, model_id)
    metadata = _read_metadata(metadata_path)
    quants = metadata.setdefault("quants", {})
    if not isinstance(quants, dict):
        quants = {}
        metadata["quants"] = quants
    quants[quant_level] = {
        "filename": dest_path.name,
        "hf_filename": hf_filename,
        "repo_id": repo_id,
        "sha256": actual_sha,
        "size_bytes": dest_path.stat().st_size,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }
    metadata.update(
        {
            "model_id": model_id,
            "name": spec.name,
            "license": spec.license,
            "download_url": spec.download_url,
        }
    )
    _write_metadata(metadata_path, metadata)

    if progress_callback is not None:
        progress_callback(100.0)

    log.info(
        "model_downloaded",
        model_id=model_id,
        quant=quant_level,
        path=str(dest_path),
        bytes=dest_path.stat().st_size,
    )
    return DownloadOutcome(
        model_id=model_id,
        quant_level=quant_level,
        path=dest_path,
        routing=routing,
        skipped=False,
    )


def auto_select_and_download(
    profile: DeviceProfile,
    *,
    models_dir: Path | None = None,
    progress_callback: ProgressCallback | None = None,
    router: ModelRouter | None = None,
    hf_api: HfApi | None = None,
    registry_path: Path | None = None,
) -> DownloadOutcome:
    """Route hardware to a tier-default model and download it when missing."""
    resolved_router = router or ModelRouter(registry_path=registry_path)
    tier = resolved_router.tier_for_profile(profile)
    model_id = _select_model_for_tier(tier, resolved_router)
    routing = resolved_router.route(profile, model_id)
    outcome = download(
        model_id,
        quant_level=routing.quant_level,
        profile=profile,
        models_dir=models_dir,
        progress_callback=progress_callback,
        router=resolved_router,
        hf_api=hf_api,
        registry_path=registry_path,
    )
    return DownloadOutcome(
        model_id=outcome.model_id,
        quant_level=outcome.quant_level,
        path=outcome.path,
        routing=routing,
        skipped=outcome.skipped,
    )


def register_local(
    model_id: str,
    quant_level: str,
    source_path: Path,
    *,
    models_dir: Path | None = None,
    sha256: str | None = None,
    registry_path: Path | None = None,
) -> LocalModelRecord:
    """Copy an existing GGUF into the OpenMW layout and write metadata."""
    if not source_path.is_file():
        raise FileNotFoundError(f"source GGUF not found: {source_path}")

    registry = load_registry(registry_path)
    if model_id not in registry:
        raise KeyError(f"unknown model_id: {model_id}")
    spec = registry[model_id]

    root = models_dir or default_models_dir()
    model_dir = root / model_id
    model_dir.mkdir(parents=True, exist_ok=True)
    dest_path = _local_gguf_path(root, model_id, quant_level)
    shutil.copy2(source_path, dest_path)

    digest = sha256 or _file_sha256(dest_path)
    metadata_path = _model_metadata_path(root, model_id)
    metadata = _read_metadata(metadata_path)
    quants = metadata.setdefault("quants", {})
    if not isinstance(quants, dict):
        quants = {}
        metadata["quants"] = quants
    quants[quant_level] = {
        "filename": dest_path.name,
        "hf_filename": source_path.name,
        "repo_id": repo_id_from_url(spec.download_url),
        "sha256": digest,
        "size_bytes": dest_path.stat().st_size,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "source": "local_register",
    }
    metadata.update(
        {
            "model_id": model_id,
            "name": spec.name,
            "license": spec.license,
            "download_url": spec.download_url,
        }
    )
    _write_metadata(metadata_path, metadata)

    log.info("model_registered_local", model_id=model_id, quant=quant_level, path=str(dest_path))
    return LocalModelRecord(
        model_id=model_id,
        quant_level=quant_level,
        gguf_path=dest_path,
        metadata_path=metadata_path,
        sha256=digest,
        size_bytes=dest_path.stat().st_size,
    )


def repo_id_from_url(download_url: str) -> str:
    """Parse ``owner/repo`` from a Hugging Face model page URL."""
    parsed = urlparse(download_url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise ValueError(f"invalid Hugging Face URL: {download_url}")
    return f"{parts[0]}/{parts[1]}"


def _select_model_for_tier(tier: HardwareTier, router: ModelRouter) -> str:
    preferred = _TIER_DEFAULT_MODEL.get(tier)
    if preferred is not None and preferred in router.registry:
        return preferred
    for model_id, spec in router.registry.items():
        if spec.tier == tier:
            return model_id
    raise ModelManagerError(f"no registry model for tier {tier}")


def _synthetic_profile_for_quant(tier: HardwareTier) -> DeviceProfile:
    vram_by_tier = {
        "NANO": 4.0,
        "SMALL": 8.0,
        "MID": 14.0,
        "LARGE": 20.0,
        "XLARGE": 48.0,
    }
    vram = vram_by_tier[tier]
    return DeviceProfile(
        gpu_name="Synthetic",
        gpu_vram_gb=vram,
        gpu_bandwidth_gbps=360.0,
        system_ram_gb=max(16.0, vram * 2),
        cpu_cores=8,
        nvme_model=None,
        nvme_seq_read_gbps=3.5,
        nvme_endurance_tbw=0.0,
    )


def _check_license_gate(license_name: str, model_id: str) -> None:
    normalized = license_name.strip()
    if normalized in _OPEN_LICENSES or normalized.upper().startswith("APACHE"):
        return
    raise LicenseGateError(
        f"Model '{model_id}' (license: {license_name}) requires Hugging Face authentication. "
        "Accept the license on the model repo and set HF_TOKEN, or choose an MIT/Apache model."
    )


def _check_disk_space(models_dir: Path, required_bytes: int) -> None:
    target = models_dir if models_dir.exists() else models_dir.parent
    target.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(target)
    needed = required_bytes + _DISK_HEADROOM_BYTES
    if usage.free < needed:
        raise InsufficientDiskSpaceError(
            f"Need {needed} bytes free for download; only {usage.free} bytes available"
        )


def _resolve_gguf_filename(
    api: HfApi,
    *,
    repo_id: str,
    quant_level: str,
) -> tuple[str, int, str | None]:
    files = api.list_repo_files(repo_id, repo_type="model")
    gguf_files = [name for name in files if name.lower().endswith(".gguf")]
    if not gguf_files:
        raise ModelFileNotFoundError(f"no GGUF files in repo {repo_id}")

    quant_token = quant_level.lower().replace("_", "[_-]")
    pattern = re.compile(rf"{quant_token}", re.IGNORECASE)
    ranked = sorted(
        gguf_files,
        key=lambda name: (
            0 if pattern.search(Path(name).stem) else 1,
            len(name),
        ),
    )
    chosen = ranked[0]
    if not pattern.search(Path(chosen).stem):
        raise ModelFileNotFoundError(f"no GGUF matching quant {quant_level} in repo {repo_id}")

    size_bytes, sha256 = _sibling_metadata(api, repo_id, chosen)
    return chosen, size_bytes, sha256


def _sibling_metadata(api: HfApi, repo_id: str, filename: str) -> tuple[int, str | None]:
    info = api.repo_info(repo_id, repo_type="model", files_metadata=True)
    siblings = getattr(info, "siblings", None) or []
    for sibling in siblings:
        if sibling.rfilename != filename:
            continue
        if sibling.lfs is not None:
            return sibling.lfs.size, sibling.lfs.sha256
        return int(sibling.size or 0), None
    return 0, None


def _expected_sha256(api: HfApi, repo_id: str, filename: str) -> str | None:
    _, sha256 = _sibling_metadata(api, repo_id, filename)
    return sha256


def _local_gguf_path(root: Path, model_id: str, quant_level: str) -> Path:
    return root / model_id / f"{quant_level}.gguf"


def _model_metadata_path(root: Path, model_id: str) -> Path:
    return root / model_id / "metadata.json"


def _read_metadata(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"metadata must be a JSON object: {path}")
    return payload


def _write_metadata(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
