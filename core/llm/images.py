import base64
import io
from dataclasses import dataclass

from PIL import Image, ImageOps

MAX_LLM_IMAGES = 3
IMAGE_COMPRESS_THRESHOLD_BYTES = 1_000_000
IMAGE_MAX_DIMENSION = 1568
JPEG_QUALITY_STEPS = (85, 75, 65)

_EXTENSION_MEDIA_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


@dataclass(frozen=True, slots=True)
class LLMImageInput:
    media_type: str
    data_base64: str
    original_bytes: int
    processed_bytes: int
    filename: str = ""

    def to_openai_content_part(self) -> dict[str, object]:
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{self.media_type};base64,{self.data_base64}"},
        }


def is_supported_image(filename: str, content_type: str | None) -> bool:
    return _detect_media_type(filename, content_type) is not None


def prepare_llm_image(filename: str, content_type: str | None, data: bytes) -> LLMImageInput | None:
    media_type = _detect_media_type(filename, content_type)
    if media_type is None:
        return None

    processed, processed_media_type = _compress_if_needed(data, media_type)
    return LLMImageInput(
        media_type=processed_media_type,
        data_base64=base64.b64encode(processed).decode("ascii"),
        original_bytes=len(data),
        processed_bytes=len(processed),
        filename=filename,
    )


def _detect_media_type(filename: str, content_type: str | None) -> str | None:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized in set(_EXTENSION_MEDIA_TYPES.values()):
        return normalized
    lowered = (filename or "").lower()
    for suffix, media_type in _EXTENSION_MEDIA_TYPES.items():
        if lowered.endswith(suffix):
            return media_type
    return None


def _compress_if_needed(data: bytes, media_type: str) -> tuple[bytes, str]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            should_compress = len(data) > IMAGE_COMPRESS_THRESHOLD_BYTES or max(width, height) > IMAGE_MAX_DIMENSION
            if not should_compress:
                return data, media_type

            image = ImageOps.exif_transpose(image)
            image.thumbnail((IMAGE_MAX_DIMENSION, IMAGE_MAX_DIMENSION), Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")

            best = data
            best_media_type = media_type
            force_resized = max(width, height) > IMAGE_MAX_DIMENSION
            for quality in JPEG_QUALITY_STEPS:
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=True)
                candidate = output.getvalue()
                if len(candidate) < len(best) or force_resized:
                    best = candidate
                    best_media_type = "image/jpeg"
                if len(candidate) <= IMAGE_COMPRESS_THRESHOLD_BYTES:
                    break
            return best, best_media_type
    except Exception:
        return data, media_type
