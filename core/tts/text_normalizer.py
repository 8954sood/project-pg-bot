import re


IMAGE_TEXT = "(이미지)"
EMOJI_TEXT = "이모지"
CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:[^:\s>]+:\d+>")


def normalize_tts_text(content: str) -> str:
    text = CUSTOM_EMOJI_PATTERN.sub(EMOJI_TEXT, content)
    stripped = text.strip()
    if stripped and all(ch == "." for ch in stripped):
        count = min(3, stripped.count("."))
        return "점" * count
    if stripped and all(ch == "?" for ch in stripped):
        count = min(3, stripped.count("?"))
        return "물음표" * count
    return text.replace("?", "물음표").replace(".", "(점)")


def build_tts_text(content: str, has_image: bool) -> str:
    text = normalize_tts_text(content or "")
    if has_image:
        if text:
            return f"{IMAGE_TEXT}{text}"
        return IMAGE_TEXT
    return text
