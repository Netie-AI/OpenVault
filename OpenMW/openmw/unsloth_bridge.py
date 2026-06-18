"""Optional Unsloth load / fine-tune / export wrappers for the OpenMW training pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

import structlog

from openmw.device_profile import DeviceProfile
from openmw.training_config import TrainingConfig

log = structlog.get_logger()

_MIN_VRAM_GB_LORA_7B = 8.0
_DEFAULT_PARAMS_B_7B = 7.0

_UNSLOTH_AVAILABLE = False

try:
    import unsloth  # noqa: F401

    _UNSLOTH_AVAILABLE = True
except ImportError:
    pass


class UnslothNotAvailable(RuntimeError):
    """Raised when Unsloth is not installed or failed to import."""


class InsufficientVramError(ValueError):
    """Raised when GPU VRAM is below the LoRA training minimum."""


@dataclass(frozen=True)
class UnslothSession:
    """Loaded Unsloth model + tokenizer pair."""

    model_id: str
    model: object
    tokenizer: object


@dataclass(frozen=True)
class FinetuneResult:
    """Outcome of an SFT fine-tune pass."""

    model_id: str
    dataset_path: Path
    output_dir: Path
    steps: int
    session: UnslothSession


@dataclass(frozen=True)
class GgufExportResult:
    """Outcome of a GGUF export."""

    output_path: Path
    quantization_method: str


@dataclass(frozen=True)
class VllmExportResult:
    """Merged-weight export suitable for ``vllm serve``."""

    output_path: Path
    serve_command: str


@runtime_checkable
class _FastLanguageModelProtocol(Protocol):
    @staticmethod
    def from_pretrained(
        model_name: str,
        *,
        max_seq_length: int,
        load_in_4bit: bool,
        fast_inference: bool = ...,
        gpu_memory_utilization: float = ...,
        unsloth_vllm_standby: bool = ...,
    ) -> tuple[object, object]: ...

    @staticmethod
    def get_peft_model(
        model: object,
        *,
        r: int,
        lora_alpha: int,
        lora_dropout: float,
        target_modules: list[str],
        use_gradient_checkpointing: str,
    ) -> object: ...

    @staticmethod
    def for_training(model: object) -> object: ...

    @staticmethod
    def for_inference(model: object) -> object: ...


def is_unsloth_available() -> bool:
    """Return True when the optional ``unsloth`` package is importable."""
    return _UNSLOTH_AVAILABLE


def require_unsloth() -> None:
    """Raise :class:`UnslothNotAvailable` when Unsloth is not installed."""
    if not _UNSLOTH_AVAILABLE:
        raise UnslothNotAvailable(
            "Unsloth is not installed. Install the optional training extra "
            "(CUDA required): uv pip install unsloth"
        )


def validate_training_profile(
    profile: DeviceProfile,
    *,
    params_b: float = _DEFAULT_PARAMS_B_7B,
    min_vram_gb: float = _MIN_VRAM_GB_LORA_7B,
) -> None:
    """Confirm the device has enough VRAM for 4-bit LoRA on a ~7B base model."""
    if profile.cpu_inference_mode:
        raise InsufficientVramError(
            "LoRA fine-tuning requires a CUDA GPU; profile is in cpu_inference_mode"
        )

    effective_vram = _effective_vram_gb(profile)
    if effective_vram < min_vram_gb:
        raise InsufficientVramError(
            f"LoRA on ~{params_b}B @ 4-bit needs >= {min_vram_gb} GB VRAM; "
            f"detected {effective_vram:.1f} GB"
        )


def unsloth_load(
    model_id: str,
    *,
    load_in_4bit: bool = True,
    max_seq_length: int = 2048,
    fast_inference: bool = False,
    gpu_memory_utilization: float = 0.95,
    unsloth_vllm_standby: bool = False,
    fast_language_model_cls: _FastLanguageModelProtocol | None = None,
) -> UnslothSession:
    """Load a base model via Unsloth ``FastLanguageModel.from_pretrained``.

    For GRPO/PPO rollouts with in-process vLLM, set ``UNSLOTH_VLLM_STANDBY=1`` in the
    environment **before** importing Unsloth (or pass ``unsloth_vllm_standby=True``).
    That reclaims vLLM KV-cache memory during training while preserving shared LoRA
    weights. Pair with ``gpu_memory_utilization=0.95`` for rollout-heavy workloads.

    After loading, call :func:`prepare_for_training` before fine-tuning and
    :func:`prepare_for_inference` before generation benchmarks.
    """
    require_unsloth()
    flm = _resolve_fast_language_model(fast_language_model_cls)

    if fast_inference:
        model, tokenizer = flm.from_pretrained(
            model_id,
            max_seq_length=max_seq_length,
            load_in_4bit=load_in_4bit,
            fast_inference=True,
            gpu_memory_utilization=gpu_memory_utilization,
            unsloth_vllm_standby=unsloth_vllm_standby,
        )
    else:
        model, tokenizer = flm.from_pretrained(
            model_id,
            max_seq_length=max_seq_length,
            load_in_4bit=load_in_4bit,
        )
    log.info(
        "unsloth_loaded",
        model_id=model_id,
        load_in_4bit=load_in_4bit,
        fast_inference=fast_inference,
    )
    return UnslothSession(model_id=model_id, model=model, tokenizer=tokenizer)


def prepare_for_training(
    session: UnslothSession,
    *,
    fast_language_model_cls: _FastLanguageModelProtocol | None = None,
) -> UnslothSession:
    """Switch Unsloth kernels to training mode via ``FastLanguageModel.for_training``.

    Call before each fine-tune or RL training step, especially after inference or
    vLLM standby rollouts.
    """
    require_unsloth()
    flm = _resolve_fast_language_model(fast_language_model_cls)
    flm.for_training(session.model)
    return session


def prepare_for_inference(
    session: UnslothSession,
    *,
    fast_language_model_cls: _FastLanguageModelProtocol | None = None,
) -> UnslothSession:
    """Switch Unsloth kernels to inference mode via ``FastLanguageModel.for_inference``."""
    require_unsloth()
    flm = _resolve_fast_language_model(fast_language_model_cls)
    flm.for_inference(session.model)
    return session


def load_alpaca_dataset(dataset_path: Path) -> list[dict[str, str]]:
    """Load Alpaca-style JSON records with ``instruction`` and ``output`` fields."""
    if not dataset_path.is_file():
        raise FileNotFoundError(f"dataset not found: {dataset_path}")

    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        raw_rows = payload.get("data", payload.get("examples"))
        if not isinstance(raw_rows, list):
            raise ValueError("dataset object must contain a 'data' or 'examples' list")
    elif isinstance(payload, list):
        raw_rows = payload
    else:
        raise ValueError("dataset must be a JSON list or object")

    rows: list[dict[str, str]] = []
    for index, row in enumerate(raw_rows):
        if not isinstance(row, dict):
            raise ValueError(f"dataset row {index} must be an object")
        instruction = row.get("instruction")
        output = row.get("output")
        if not isinstance(instruction, str) or not isinstance(output, str):
            raise ValueError(
                f"dataset row {index} requires string 'instruction' and 'output' fields"
            )
        rows.append({"instruction": instruction, "output": output})
    if not rows:
        raise ValueError("dataset is empty")
    return rows


def format_alpaca_examples(rows: list[dict[str, str]]) -> list[str]:
    """Format Alpaca rows into plain-text SFT prompts."""
    texts: list[str] = []
    for row in rows:
        text = (
            "### Instruction:\n"
            f"{row['instruction']}\n\n"
            "### Response:\n"
            f"{row['output']}"
        )
        texts.append(text)
    return texts


def unsloth_finetune(
    session: UnslothSession,
    dataset_path: Path,
    *,
    config: TrainingConfig | None = None,
    output_dir: Path | None = None,
    fast_language_model_cls: _FastLanguageModelProtocol | None = None,
    sft_trainer_factory: Callable[..., object] | None = None,
) -> FinetuneResult:
    """Fine-tune *session* on an Alpaca-style JSON dataset using LoRA SFT.

    Applies PART 5 LoRA defaults (``lora_r=16``, ``lora_alpha=16``,
    ``lora_dropout=0``, ``target_modules=['q_proj','v_proj']``) and calls
    ``FastLanguageModel.for_training`` before the trainer runs.
    """
    require_unsloth()
    cfg = config or TrainingConfig()
    flm = _resolve_fast_language_model(fast_language_model_cls)
    rows = load_alpaca_dataset(dataset_path)
    texts = format_alpaca_examples(rows)

    prepare_for_training(session, fast_language_model_cls=flm)
    model = flm.get_peft_model(
        session.model,
        r=cfg.lora_r,
        lora_alpha=cfg.lora_alpha,
        lora_dropout=cfg.lora_dropout,
        target_modules=list(cfg.target_modules),
        use_gradient_checkpointing="unsloth",
    )

    resolved_output = output_dir or dataset_path.parent / "unsloth_checkpoints"
    resolved_output.mkdir(parents=True, exist_ok=True)

    trainer = _build_sft_trainer(
        model=model,
        tokenizer=session.tokenizer,
        texts=texts,
        config=cfg,
        output_dir=resolved_output,
        sft_trainer_factory=sft_trainer_factory,
    )
    train_result = getattr(trainer, "train")()
    steps = int(getattr(train_result, "global_step", 0) or cfg.num_train_epochs)
    trained_session = UnslothSession(model_id=session.model_id, model=model, tokenizer=session.tokenizer)

    log.info(
        "unsloth_finetune_complete",
        model_id=session.model_id,
        dataset=str(dataset_path),
        steps=steps,
        output_dir=str(resolved_output),
    )
    return FinetuneResult(
        model_id=session.model_id,
        dataset_path=dataset_path,
        output_dir=resolved_output,
        steps=steps,
        session=trained_session,
    )


def export_gguf(
    session: UnslothSession,
    output_path: Path,
    *,
    quant: str = "q4_k_m",
) -> GgufExportResult:
    """Export merged weights to GGUF via ``model.save_pretrained_gguf``."""
    require_unsloth()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_pretrained_gguf = getattr(session.model, "save_pretrained_gguf", None)
    if save_pretrained_gguf is None:
        raise UnslothNotAvailable("loaded model lacks save_pretrained_gguf")

    save_pretrained_gguf(str(output_path), session.tokenizer, quantization_method=quant)
    log.info(
        "unsloth_gguf_exported",
        model_id=session.model_id,
        path=str(output_path),
        quant=quant,
    )
    return GgufExportResult(output_path=output_path, quantization_method=quant)


def unsloth_to_vllm(
    session: UnslothSession,
    output_path: Path,
    *,
    save_method: str = "merged_16bit",
) -> VllmExportResult:
    """Export merged 16-bit weights for ``vllm serve`` (not in-process vLLM)."""
    require_unsloth()
    output_path = Path(output_path)
    output_path.mkdir(parents=True, exist_ok=True)
    save_pretrained_merged = getattr(session.model, "save_pretrained_merged", None)
    if save_pretrained_merged is None:
        raise UnslothNotAvailable("loaded model lacks save_pretrained_merged")

    save_pretrained_merged(str(output_path), session.tokenizer, save_method=save_method)
    serve_command = f"vllm serve {output_path}"
    log.info(
        "unsloth_vllm_exported",
        model_id=session.model_id,
        path=str(output_path),
        save_method=save_method,
    )
    return VllmExportResult(output_path=output_path, serve_command=serve_command)


def gguf_quant_to_registry_level(quant: str) -> str:
    """Map Unsloth GGUF quant strings (``q4_k_m``) to registry labels (``Q4_K_M``)."""
    normalized = quant.strip().upper().replace("-", "_")
    if normalized in {"Q4_K_M", "Q5_K_M", "Q8_0", "FP16"}:
        return normalized
    return normalized


def _effective_vram_gb(profile: DeviceProfile) -> float:
    if profile.unified_memory:
        return profile.system_ram_gb * 0.90
    return profile.gpu_vram_gb


def _resolve_fast_language_model(
    override: _FastLanguageModelProtocol | None,
) -> _FastLanguageModelProtocol:
    if override is not None:
        return override
    require_unsloth()
    from unsloth import FastLanguageModel

    return FastLanguageModel  # type: ignore[no-any-return]


def _build_sft_trainer(
    *,
    model: object,
    tokenizer: object,
    texts: list[str],
    config: TrainingConfig,
    output_dir: Path,
    sft_trainer_factory: Callable[..., object] | None,
) -> object:
    if sft_trainer_factory is not None:
        return sft_trainer_factory(
            model=model,
            tokenizer=tokenizer,
            texts=texts,
            config=config,
            output_dir=output_dir,
        )

    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    dataset = Dataset.from_dict({"text": texts})
    training_args = SFTConfig(
        output_dir=str(output_dir),
        num_train_epochs=config.num_train_epochs,
        per_device_train_batch_size=config.per_device_train_batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        learning_rate=config.learning_rate,
        warmup_ratio=config.warmup_ratio,
        logging_steps=config.logging_steps,
        save_steps=config.save_steps if config.save_steps > 0 else 500,
        seed=config.seed,
        dataset_text_field=config.dataset_text_field,
        max_seq_length=config.max_seq_length,
        packing=config.packing,
    )
    return SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=training_args,
    )
