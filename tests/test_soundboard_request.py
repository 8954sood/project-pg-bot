import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from cogs.soundboard_request import SoundboardRequest, MAX_TITLE_LENGTH


class SoundboardRequestValidationTests(unittest.TestCase):
    def setUp(self):
        self.cog = SoundboardRequest(MagicMock())

    def test_is_audio_attachment_by_content_type(self):
        attachment = SimpleNamespace(content_type="audio/mpeg", filename="file.bin")
        self.assertTrue(self.cog._is_audio_attachment(attachment))

    def test_is_audio_attachment_by_filename_extension(self):
        attachment = SimpleNamespace(content_type=None, filename="voice.ogg")
        self.assertTrue(self.cog._is_audio_attachment(attachment))

    def test_invalid_request_when_title_too_long(self):
        message = SimpleNamespace(
            content="a" * (MAX_TITLE_LENGTH + 1),
            attachments=[SimpleNamespace(content_type="audio/mpeg", filename="voice.mp3")],
        )
        self.assertFalse(self.cog._is_valid_request_message(message))

    def test_valid_request_message(self):
        message = SimpleNamespace(
            content="효과음",
            attachments=[SimpleNamespace(content_type="audio/mpeg", filename="voice.mp3")],
        )
        self.assertTrue(self.cog._is_valid_request_message(message))


if __name__ == "__main__":
    unittest.main()
