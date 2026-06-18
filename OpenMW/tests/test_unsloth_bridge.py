"""Unsloth bridge tests — mocked Unsloth/TRL, no GPU."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import openmw.unsloth_bridge as bridge
from openmw.device_profile import DeviceProfile
from openmw.model_manager import list_local, register_local
from openmw.model_router import ModelRouter
from openmw.training_config import TrainingConfig
from openmw.unsloth_bridge import (
    FinetuneResult,
    GgufExportResult,
    InsufficientVramError,
    UnslothNotAvailable,
    UnslothSession,
    VllmExportResult,
    export_gguf,
    format_alpaca_examples,
    gguf_quant_to_registry_level,
    is_unsloth_available,
    load_alpaca_dataset,
    prepare_for_inference,
    prepare_for_training,
    require_unsloth,
    unsloth_finetune,
    unsloth_load,
    unsloth_to_vllm,
    validate_training_profile,
)


def _profile(*, gpu_vram_gb: float = 10.0, cpu_inference_mode: bool = False) -> DeviceProfile:
    return DeviceProfile(
        gpu_name="Test GPU",
        gpu_vram_gb=gpu_vram_gb,
        gpu_bandwidth_gbps=360.0,
        system_ram_gb=32.0,
        cpu_cores=8,
        nvme_model="Mock NVMe",
        nvme_seq_read_gbps=3.5,
        nvme_endurance_tbw=600.0,
        cpu_inference_mode=cpu_inference_mode,
    )


def _alpaca_dataset(path: Path) -> None:
    rows = [
        {"instruction": "Say hello.", "output": "Hello!"},
        {"instruction": "Count to two.", "output": "One, two."},
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")


class _MockTrainResult:
    global_step = 42


class _MockFastLanguageModel:
    from_pretrained_calls: list[dict[str, object]] = []
    peft_calls: list[dict[str, object]] = []

    @classmethod
    def reset(cls) -> None:
        cls.from_pretrained_calls = []
        cls.peft_calls = []

    @staticmethod
    def from_pretrained(
        model_name: str,
        *,
        max_seq_length: int,
        load_in_4bit: bool,
        fast_inference: bool = False,
        gpu_memory_utilization: float = 0.95,
        unsloth_vllm_standby: bool = False,
    ) -> tuple[MagicMock, MagicMock]:
        _MockFastLanguageModel.from_pretrained_calls.append(
            {
                "model_name": model_name,
                "max_seq_length": max_seq_length,
                "load_in_4bit": load_in_4bit,
                "fast_inference": fast_inference,
                "gpu_memory_utilization": gpu_memory_utilization,
                "unsloth_vllm_standby": unsloth_vllm_standby,
            }
        )
        model = MagicMock(name="model")
        model.save_pretrained_gguf = MagicMock()
        model.save_pretrained_merged = MagicMock()
        tokenizer = MagicMock(name="tokenizer")
        return model, tokenizer

    @staticmethod
    def get_peft_model(
        model: object,
        *,
        r: int,
        lora_alpha: int,
        lora_dropout: float,
        target_modules: list[str],
        use_gradient_checkpointing: str,
    ) -> MagicMock:
        _MockFastLanguageModel.peft_calls.append(
            {
                "r": r,
                "lora_alpha": lora_alpha,
                "lora_dropout": lora_dropout,
                "target_modules": target_modules,
                "use_gradient_checkpointing": use_gradient_checkpointing,
            }
        )
        peft_model = MagicMock(name="peft_model")
        peft_model.save_pretrained_gguf = model.save_pretrained_gguf
        peft_model.save_pretrained_merged = model.save_pretrained_merged
        return peft_model

    @staticmethod
    def for_training(model: object) -> object:
        return model

    @staticmethod
    def for_inference(model: object) -> object:
        return model


@pytest.fixture(autouse=True)
def _enable_mock_unsloth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "_UNSLOTH_AVAILABLE", True)
    _MockFastLanguageModel.reset()


def test_training_config_defaults() -> None:
    cfg = TrainingConfig()
    assert cfg.lora_r == 16
    assert cfg.lora_alpha == 16
    assert cfg.lora_dropout == 0.0
    assert cfg.target_modules == ["q_proj", "v_proj"]


def test_validate_training_profile_rejects_low_vram() -> None:
    with pytest.raises(InsufficientVramError, match=">= 8.0 GB"):
        validate_training_profile(_profile(gpu_vram_gb=6.0))


def test_validate_training_profile_rejects_cpu_mode() -> None:
    with pytest.raises(InsufficientVramError, match="cpu_inference_mode"):
        validate_training_profile(_profile(cpu_inference_mode=True))


def test_validate_training_profile_accepts_sufficient_vram() -> None:
    validate_training_profile(_profile(gpu_vram_gb=8.0))


def test_require_unsloth_raises_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "_UNSLOTH_AVAILABLE", False)
    with pytest.raises(UnslothNotAvailable):
        require_unsloth()


def test_is_unsloth_available_reflects_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "_UNSLOTH_AVAILABLE", False)
    assert is_unsloth_available() is False
    monkeypatch.setattr(bridge, "_UNSLOTH_AVAILABLE", True)
    assert is_unsloth_available() is True


def test_load_alpaca_dataset(tmp_path: Path) -> None:
    dataset_path = tmp_path / "train.json"
    _alpaca_dataset(dataset_path)
    rows = load_alpaca_dataset(dataset_path)
    assert len(rows) == 2
    assert rows[0]["instruction"] == "Say hello."


def test_format_alpaca_examples() -> None:
    texts = format_alpaca_examples([{"instruction": "Hi", "output": "Hey"}])
    assert texts[0].startswith("### Instruction:")
    assert "Hey" in texts[0]


def test_unsloth_load_passes_kwargs() -> None:
    session = unsloth_load(
        "unsloth/Qwen3-8B-Base",
        load_in_4bit=True,
        max_seq_length=1024,
        fast_inference=True,
        unsloth_vllm_standby=True,
        fast_language_model_cls=_MockFastLanguageModel,
    )
    assert isinstance(session, UnslothSession)
    assert session.model_id == "unsloth/Qwen3-8B-Base"
    call = _MockFastLanguageModel.from_pretrained_calls[0]
    assert call["load_in_4bit"] is True
    assert call["fast_inference"] is True
    assert call["unsloth_vllm_standby"] is True


def test_prepare_for_training_and_inference() -> None:
    session = unsloth_load(
        "base-model",
        fast_language_model_cls=_MockFastLanguageModel,
    )
    prepare_for_training(session, fast_language_model_cls=_MockFastLanguageModel)
    prepare_for_inference(session, fast_language_model_cls=_MockFastLanguageModel)


def test_unsloth_finetune_uses_lora_defaults(tmp_path: Path) -> None:
    dataset_path = tmp_path / "train.json"
    _alpaca_dataset(dataset_path)

    trainer = MagicMock()
    trainer.train.return_value = _MockTrainResult()

    def factory(**kwargs: object) -> MagicMock:
        assert kwargs["texts"]
        return trainer

    session = unsloth_load("base-model", fast_language_model_cls=_MockFastLanguageModel)
    result = unsloth_finetune(
        session,
        dataset_path,
        config=TrainingConfig(),
        sft_trainer_factory=factory,
        fast_language_model_cls=_MockFastLanguageModel,
    )

    assert isinstance(result, FinetuneResult)
    assert result.steps == 42
    peft = _MockFastLanguageModel.peft_calls[0]
    assert peft["r"] == 16
    assert peft["lora_alpha"] == 16
    assert peft["lora_dropout"] == 0.0
    assert peft["target_modules"] == ["q_proj", "v_proj"]
    trainer.train.assert_called_once()


def test_export_gguf(tmp_path: Path) -> None:
    session = unsloth_load("base-model", fast_language_model_cls=_MockFastLanguageModel)
    output_path = tmp_path / "finetuned.gguf"
    result = export_gguf(session, output_path, quant="q4_k_m")

    assert isinstance(result, GgufExportResult)
    session.model.save_pretrained_gguf.assert_called_once_with(
        str(output_path),
        session.tokenizer,
        quantization_method="q4_k_m",
    )


def test_unsloth_to_vllm(tmp_path: Path) -> None:
    session = unsloth_load("base-model", fast_language_model_cls=_MockFastLanguageModel)
    output_path = tmp_path / "merged"
    result = unsloth_to_vllm(session, output_path)

    assert isinstance(result, VllmExportResult)
    assert result.serve_command == f"vllm serve {output_path}"
    session.model.save_pretrained_merged.assert_called_once_with(
        str(output_path),
        session.tokenizer,
        save_method="merged_16bit",
    )


def test_gguf_quant_to_registry_level() -> None:
    assert gguf_quant_to_registry_level("q4_k_m") == "Q4_K_M"


def test_training_pipeline_export_and_register(tmp_path: Path) -> None:
    """End-to-end mocked pipeline: profile → load → finetune → export → register_local."""
    profile = _profile(gpu_vram_gb=12.0)
    validate_training_profile(profile)

    router = ModelRouter()
    model_id = "qwen2.5-1.5b"
    assert model_id in router.registry

    dataset_path = tmp_path / "train.json"
    _alpaca_dataset(dataset_path)
    models_dir = tmp_path / "models"

    trainer = MagicMock()
    trainer.train.return_value = _MockTrainResult()

    session = unsloth_load(model_id, fast_language_model_cls=_MockFastLanguageModel)
    finetune_result = unsloth_finetune(
        session,
        dataset_path,
        sft_trainer_factory=lambda **_: trainer,
        fast_language_model_cls=_MockFastLanguageModel,
    )

    gguf_path = tmp_path / "export" / f"{model_id}.gguf"
    export_gguf(finetune_result.session, gguf_path, quant="q4_k_m")
    quant_level = gguf_quant_to_registry_level("q4_k_m")
    gguf_path.write_bytes(b"mock-gguf")
    register_local(
        model_id,
        quant_level,
        gguf_path,
        models_dir=models_dir,
    )

    records = list_local(models_dir=models_dir)
    assert len(records) == 1
    assert records[0].model_id == model_id
    assert records[0].quant_level == "Q4_K_M"
