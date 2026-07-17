from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from bootstrap import RuntimeSettings


class RuntimeSettingsTests(unittest.TestCase):
    def test_uses_fixed_model_and_required_connection_environment_names(self) -> None:
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-key",
            "INCERRY_OPENAI_BASE_URL": "http://model.test/v1",
            "NINO_MODEL_NAME": "ignored-model",
            "NINO_MODEL_API_KEY": "ignored-key",
            "NINO_MODEL_BASE_URL": "http://ignored.test/v1",
            "OPENAI_MODEL": "also-ignored",
            "OPENAI_BASE_URL": "http://also-ignored.test/v1",
        }, clear=True):
            settings = RuntimeSettings.from_env()

        self.assertEqual("gpt-5.4", settings.model_name)
        self.assertEqual("test-key", settings.model_api_key)
        self.assertEqual("http://model.test/v1", settings.model_base_url)


if __name__ == "__main__":
    unittest.main()
