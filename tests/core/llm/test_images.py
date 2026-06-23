import base64
import io

from PIL import Image

from core.llm.images import IMAGE_MAX_DIMENSION, is_supported_image, prepare_llm_image


def test_prepare_llm_image_preserves_small_supported_image():
    original = _png_bytes(32, 32)

    image = prepare_llm_image("small.png", "image/png", original)

    assert image is not None
    assert image.media_type == "image/png"
    assert image.original_bytes == len(original)
    assert image.processed_bytes == len(original)
    assert base64.b64decode(image.data_base64) == original


def test_prepare_llm_image_resizes_large_image_for_model_input():
    original = _png_bytes(IMAGE_MAX_DIMENSION + 100, IMAGE_MAX_DIMENSION + 100)

    image = prepare_llm_image("large.png", "image/png", original)

    assert image is not None
    assert image.media_type == "image/jpeg"
    processed = Image.open(io.BytesIO(base64.b64decode(image.data_base64)))
    assert max(processed.size) <= IMAGE_MAX_DIMENSION


def test_supported_image_detection_uses_content_type_or_filename():
    assert is_supported_image("photo.bin", "image/jpeg")
    assert is_supported_image("photo.webp", None)
    assert not is_supported_image("photo.txt", "text/plain")


def _png_bytes(width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), color=(255, 0, 0)).save(output, format="PNG")
    return output.getvalue()
