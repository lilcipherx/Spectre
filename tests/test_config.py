"""Config loading tests."""

import pytest

from spectre.config import Config


def test_dry_run_does_not_require_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setenv("SPECTRE_DRY_RUN", "1")
    cfg = Config.load(env_file=None)
    assert cfg.dry_run is True
    assert cfg.elevenlabs_api_key == ""


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setenv("SPECTRE_DRY_RUN", "0")
    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
        Config.load(env_file=None)


def test_defaults_and_overrides(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    monkeypatch.setenv("SPECTRE_LANGUAGE", "en")
    monkeypatch.setenv("SPECTRE_AUDIO_GAIN", "2.5")
    monkeypatch.setenv("SPECTRE_VAD_THRESHOLD", "0.2")
    monkeypatch.setenv("SPECTRE_DISPLAY", "console")
    monkeypatch.setenv("SPECTRE_DISPLAY_ROTATE", "2")

    cfg = Config.load(env_file=None)
    assert cfg.elevenlabs_api_key == "sk_test"
    assert cfg.language == "en"
    assert cfg.audio_gain == 2.5
    assert cfg.vad_threshold == 0.2
    assert cfg.display == "console"
    assert cfg.display_rotate == 2
    assert cfg.sample_rate == 16000  # fixed


def test_fb_path_default(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    monkeypatch.delenv("SPECTRE_FB_PATH", raising=False)
    monkeypatch.delenv("SPECTRE_DISPLAY", raising=False)
    cfg = Config.load(env_file=None)
    assert cfg.display == "fb1"
    assert cfg.fb_path == "/dev/fb1"


def test_fb_path_override(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    monkeypatch.setenv("SPECTRE_FB_PATH", "/dev/fb0")
    cfg = Config.load(env_file=None)
    assert cfg.fb_path == "/dev/fb0"
