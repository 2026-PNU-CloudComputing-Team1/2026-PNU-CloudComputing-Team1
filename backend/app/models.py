from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class TranslationRequest(BaseModel):
    text: str = Field(..., min_length=1)
    source_lang: str = "ko"
    target_langs: List[str] = Field(default_factory=lambda: ["en", "ja", "zh"])


class SubtitleMessage(BaseModel):
    id: str
    stream_id: str
    timestamp: float
    duration: float
    original_text: str
    translations: Dict[str, str]
    created_at: datetime


class StreamInfo(BaseModel):
    stream_id: str
    title: str
    is_active: bool
    started_at: datetime
    viewers: int = 0
    source_language: str = "ko"
    target_languages: List[str] = Field(default_factory=lambda: ["en", "ja", "zh"])


class StreamControlRequest(BaseModel):
    title: Optional[str] = None
    source_language: str = "ko"
    target_languages: List[str] = Field(default_factory=lambda: ["en", "ja", "zh"])
