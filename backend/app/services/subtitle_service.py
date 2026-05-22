import itertools
from datetime import datetime
from typing import Dict, List

from app.models import SubtitleMessage
from app.services.cache_service import CacheService
from app.services.translation_service import TranslationService


class SubtitleService:
    SAMPLE_LINES = [
        "안녕하세요. 전 세계 시청자를 위한 라이브 방송입니다.",
        "지금 생성되는 문장은 실시간 자막 파이프라인을 시뮬레이션합니다.",
        "음성 인식 결과가 들어오면 여러 언어로 번역됩니다.",
        "생성된 자막은 캐시에 저장되고 시청자에게 즉시 전달됩니다.",
        "클라우드 환경에서는 이 자막을 S3와 CDN으로 확장할 수 있습니다.",
    ]

    def __init__(self, cache: CacheService, translator: TranslationService):
        self.cache = cache
        self.translator = translator
        self._counters: Dict[str, itertools.count] = {}

    async def generate_mock_subtitle(
        self,
        stream_id: str,
        target_langs: List[str],
        source_lang: str = "ko",
    ) -> SubtitleMessage:
        counter = self._counters.setdefault(stream_id, itertools.count(1))
        index = next(counter)
        text = self.SAMPLE_LINES[(index - 1) % len(self.SAMPLE_LINES)]
        timestamp = float(index * 2)
        translations = await self.translator.translate(text, target_langs, source_lang)

        subtitle = SubtitleMessage(
            id=f"{stream_id}-{index}",
            stream_id=stream_id,
            timestamp=timestamp,
            duration=2.0,
            original_text=text,
            translations=translations,
            created_at=datetime.utcnow(),
        )
        await self.cache.append_subtitle(stream_id, subtitle.model_dump(mode="json"))
        return subtitle

    async def create_subtitle(
        self,
        stream_id: str,
        text: str,
        timestamp: float,
        duration: float,
        target_langs: List[str],
        source_lang: str = "ko",
        subtitle_id: str | None = None,
    ) -> SubtitleMessage:
        translations = await self.translator.translate(text, target_langs, source_lang)
        subtitle = SubtitleMessage(
            id=subtitle_id or f"{stream_id}-{int(timestamp * 1000)}",
            stream_id=stream_id,
            timestamp=timestamp,
            duration=duration,
            original_text=text,
            translations=translations,
            created_at=datetime.utcnow(),
        )
        await self.cache.append_subtitle(stream_id, subtitle.model_dump(mode="json"))
        return subtitle

    async def recent(self, stream_id: str, limit: int = 20):
        return await self.cache.recent_subtitles(stream_id, limit)
