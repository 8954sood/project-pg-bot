IMAGE_TEXT = "(이미지)"


def normalize_tts_text(content: str) -> str:
    stripped = content.strip()
    if stripped and all(ch == "." for ch in stripped):
        count = min(3, stripped.count("."))
        return "점" * count
    if stripped and all(ch == "?" for ch in stripped):
        count = min(3, stripped.count("?"))
        return "물음표" * count
    return content.replace("?", "물음표").replace(".", "(점)")


def build_tts_text(content: str, has_image: bool) -> str:
    text = normalize_tts_text(content or "")
    if has_image:
        if text:
            return f"{IMAGE_TEXT}{text}"
        return IMAGE_TEXT
    return text
