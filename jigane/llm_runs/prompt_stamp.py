from dataclasses import dataclass


@dataclass(frozen=True)
class PromptStamp:
    version: str
    chars: int
