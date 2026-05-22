from typing import Dict, Iterable


class TranslationService:
    """Small translation facade.

    The default implementation is deterministic mock translation so demos keep
    working without cloud credentials. Real providers can be added behind this
    class later.
    """

    MOCK_PREFIX = {
        "en": "Live caption",
        "ja": "ライブ字幕",
        "zh": "实时字幕",
        "ko": "실시간 자막",
    }

    async def translate(self, text: str, target_langs: Iterable[str], source_lang: str = "ko") -> Dict[str, str]:
        translations: Dict[str, str] = {}
        for lang in target_langs:
            prefix = self.MOCK_PREFIX.get(lang, lang.upper())
            translations[lang] = f"{prefix}: {text}"
        return translations
