"""Pydantic configuration for Unsloth LoRA fine-tuning."""

from __future__ import annotations

from pydantic import BaseModel, Field

_DEFAULT_TARGET_MODULES: list[str] = ["q_proj", "v_proj"]


class TrainingConfig(BaseModel):
    """LoRA SFT hyperparameters aligned with PART 5 PRE-FLIGHT defaults."""

    lora_r: int = 16
    lora_alpha: int = 16
    lora_dropout: float = 0.0
    target_modules: list[str] = Field(default_factory=lambda: list(_DEFAULT_TARGET_MODULES))
    max_seq_length: int = 2048
    num_train_epochs: int = 1
    per_device_train_batch_size: int = 2
    gradient_accumulation_steps: int = 4
    learning_rate: float = 2e-4
    warmup_ratio: float = 0.03
    logging_steps: int = 10
    save_steps: int = 0
    seed: int = 3407
    dataset_text_field: str = "text"
    packing: bool = False
