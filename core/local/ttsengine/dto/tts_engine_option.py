from dataclasses import dataclass
from typing import Optional


@dataclass
class TTSEngineOption:
    user_id: int
    engine: str
    model_name: Optional[str]
