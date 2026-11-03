from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HyMT2Model:
    key: str
    model_id: str
    display_name: str
    approximate_download_gb: float
    hardware_note: str


MODELS = (
    HyMT2Model(
        key="hymt2-1.8b",
        model_id="tencent/Hy-MT2-1.8B",
        display_name="Hy-MT2 1.8B",
        approximate_download_gb=4.1,
        hardware_note="Smallest supported model; recommended for local desktops.",
    ),
    HyMT2Model(
        key="hymt2-7b",
        model_id="tencent/Hy-MT2-7B",
        display_name="Hy-MT2 7B",
        approximate_download_gb=16.1,
        hardware_note="Requires substantially more RAM/VRAM than 1.8B.",
    ),
    HyMT2Model(
        key="hymt2-30b-a3b",
        model_id="tencent/Hy-MT2-30B-A3B",
        display_name="Hy-MT2 30B-A3B",
        approximate_download_gb=60.1,
        hardware_note="Large MoE model for high-memory workstations/servers.",
    ),
)
MODEL_BY_KEY = {model.key: model for model in MODELS}


def get_model(key: str) -> HyMT2Model:
    try:
        return MODEL_BY_KEY[key]
    except KeyError as exc:
        valid = ", ".join(MODEL_BY_KEY)
        raise ValueError(f"Unknown Hy-MT2 model {key!r}. Choose one of: {valid}.") from exc
