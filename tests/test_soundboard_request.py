import unittest
import tempfile
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

from cogs.soundboard_request import SoundboardRequest, MAX_TITLE_LENGTH


class SoundboardRequestValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(delete=False)
        self.temp_file.close()
        self.cog = SoundboardRequest(MagicMock(), forwarded_messages_path=self.temp_file.name)

    def tearDown(self):
        if os.path.exists(self.temp_file.name):
            os.remove(self.temp_file.name)

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

    def test_forwarded_message_persisted_locally(self):
        self.cog._remember_forwarded_message(12345)
        reloaded = SoundboardRequest(MagicMock(), forwarded_messages_path=self.temp_file.name)
        self.assertIn(12345, reloaded.forwarded_messages)


if __name__ == "__main__":
    unittest.main()
