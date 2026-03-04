"""Unit tests for configuration management."""

import json
import tempfile
from pathlib import Path

import pytest

from mythic_cli.config import ConfigManager, MythicConfig


def test_mythic_config_defaults():
    """Test MythicConfig default values."""
    config = MythicConfig()
    assert config.server_url == "http://127.0.0.1:7443"
    assert config.api_key is None
    assert config.username is None
    assert config.verify_ssl is True
    assert config.timeout == 30


def test_config_manager_load_empty():
    """Test ConfigManager with no existing config file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.json"
        manager = ConfigManager(config_path)
        
        assert manager.config.server_url == "http://127.0.0.1:7443"
        assert manager.config.api_key is None


def test_config_manager_save_and_load():
    """Test saving and loading configuration."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.json"
        manager = ConfigManager(config_path)
        
        # Update and save
        manager.update_config(
            server_url="https://mythic.test.com",
            api_key="test-token-123",
            username="testuser"
        )
        
        # Load in new instance
        manager2 = ConfigManager(config_path)
        assert manager2.config.server_url == "https://mythic.test.com"
        assert manager2.config.api_key == "test-token-123"
        assert manager2.config.username == "testuser"


def test_config_manager_env_override(monkeypatch):
    """Test environment variable override."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.json"
        
        # Set environment variables
        monkeypatch.setenv("MYTHIC_SERVER_URL", "https://env.test.com")
        monkeypatch.setenv("MYTHIC_API_KEY", "env-token")
        monkeypatch.setenv("MYTHIC_VERIFY_SSL", "false")
        
        manager = ConfigManager(config_path)
        assert manager.config.server_url == "https://env.test.com"
        assert manager.config.api_key == "env-token"
        assert manager.config.verify_ssl is False
