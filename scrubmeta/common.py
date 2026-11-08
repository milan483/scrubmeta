"""Shared field classification and errors for format handlers."""

import re
from dataclasses import dataclass


class ScrubError(ValueError):
    """An input, format, or safety problem that can be shown to the user."""


@dataclass(frozen=True)
class Field:
    name: str
    disposition: str
    value: str | None = None


_AI_MARKERS = re.compile(
    r"\b(?:stable[ _-]*diffusion|midjourney|dall[ _-]*e|comfy[ _-]*ui|"
    r"automatic1111|invokeai|adobe firefly|openai|chatgpt|gpt[- ](?:3(?:\.5)?|4o?|[56])|"
    r"trainedAlgorithmicMedia|ai[ _-]*(?:generated|model|prompt|workflow)|"
    r"generation[ _-]*(?:model|prompt|workflow)|"
    r"negative[ _-]*prompt|(?:sdxl|flux)\.(?:safetensors|ckpt))\b",
    re.IGNORECASE,
)


def targeted(value: str) -> bool:
    """Match recognizable generation markers in eligible metadata."""
    return bool(_AI_MARKERS.search(value))
