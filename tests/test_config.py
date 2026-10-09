import json
import tomllib

import pytest
from pydantic import ValidationError

from jasminetea_mcp.cli import client_example
from jasminetea_mcp.config import Settings, load_settings


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test",
        "https://user:secret@example.test",
        "https://example.test?token=secret",
        "https://example.test/../other",
    ],
)
def test_platform_url_validation(url):
    with pytest.raises(ValidationError):
        Settings(base_url=url)


def test_token_stays_external_and_environment_wins(tmp_path, monkeypatch):
    path = tmp_path / "custom.toml"
    path.write_text('token_env = "TEA_TEST_TOKEN"\ncache_dir = "cache"\n', encoding="utf-8")
    (tmp_path / ".env").write_text("TEA_TEST_TOKEN=file-token\n", encoding="utf-8")
    monkeypatch.setenv("TEA_TEST_TOKEN", "environment-token")
    settings = load_settings(str(path))
    assert settings.token() == "environment-token"
    assert settings.cache_dir == tmp_path / "cache"
    generated = client_example("codex", str(path))
    assert tomllib.loads(generated)["mcp_servers"]["jasminetea"]["env_vars"] == ["TEA_TEST_TOKEN"]
    assert "environment-token" not in generated and "file-token" not in generated
    claude = json.loads(client_example("claude", str(path)))
    assert claude["mcpServers"]["jasminetea"]["env"]["TEA_TEST_TOKEN"] == "${TEA_TEST_TOKEN:-}"


def test_default_client_example_has_no_missing_config_dependency():
    example = tomllib.loads(client_example("codex"))["mcp_servers"]["jasminetea"]
    assert "--config" not in example["args"]


def test_empty_forwarded_variable_uses_only_selected_local_credential(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text('token_env = "TEA_FILE_TOKEN"\n', encoding="utf-8")
    (tmp_path / ".env").write_text(
        "TEA_FILE_TOKEN=local-token\nTEA_UNRELATED=should-not-load\n", encoding="utf-8"
    )
    monkeypatch.setenv("TEA_FILE_TOKEN", "")
    monkeypatch.delenv("TEA_UNRELATED", raising=False)
    settings = load_settings(str(tmp_path / "config.toml"))
    assert settings.token() == "local-token"
    import os

    assert "TEA_UNRELATED" not in os.environ
