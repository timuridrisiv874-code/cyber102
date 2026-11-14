"""Pinned local VLM choices shared by the web API and the GPU service."""
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    revision: str
    directory: str


DEFAULT_MODEL = 'internvl3_5'
MODEL_ENV = 'CYBER102_VLM_MODEL'
MODELS = {
    'internvl3_5': ModelSpec('OpenGVLab/InternVL3_5-2B-HF',
                           '3f301ffcf3dcbb47893afae6650ea3e78d96fb6d', 'internvl3_5-2b-hf'),
    'qwen3_vl_4b': ModelSpec('Qwen/Qwen3-VL-4B-Instruct',
                           'ebb281ec70b05090aa6165b016eac8ec08e71b17', 'qwen3-vl-4b-instruct'),
    'qwen3_8_27b': ModelSpec('Qwen/Qwen3.8-27B',
                           '4af51b3542f775b0a8e4b310bfb3f17e01c306c9', 'qwen3.8-27b-q4-k-m'),
}


def model_spec(name=None):
    key = os.environ.get(MODEL_ENV, DEFAULT_MODEL) if name is None else name
    if key not in MODELS:
        raise ValueError('Unknown configured local VLM')
    return MODELS[key]


def configure_from_file(root):
    """Scheduled services read the same trusted selection; no silent fallback on typos."""
    path = Path(root) / 'var/vlm-model.txt'
    key = path.read_text(encoding='utf-8-sig').strip() if path.exists() else DEFAULT_MODEL
    model_spec(key)
    os.environ[MODEL_ENV] = key
    return key
