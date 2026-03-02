"""Configuration management for Mythic CLI."""

import json
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class MythicConfig(BaseModel):
    """Configuration for Mythic server connection."""

    server_url: str = Field(default="http://127.0.0.1:7443", description="Mythic server URL")
    api_key: Optional[str] = Field(default=None, description="API token for authentication")
    username: Optional[str] = Field(default=None, description="Username for login")
    password: Optional[str] = Field(default=None, description="Password for login")
    verify_ssl: bool = Field(default=True, description="Verify SSL certificates")
    timeout: int = Field(default=30, description="Request timeout in seconds")
    tui_theme: str = Field(
        default="default",
        description="TUI theme (default, monokai, dracula, nord, solarized)",
    )


class ConfigManager:
    """Manages configuration loading and saving."""

    def __init__(self, config_path: Optional[Path] = None):
        """Initialize the config manager.

        Args:
            config_path: Path to the configuration file. If None, uses default location.
        """
        if config_path is None:
            config_dir = Path.home() / ".config" / "mythic-cli"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = config_dir / "config.json"

        self.config_path = config_path
        self.config = self.load_config()

    def load_config(self) -> MythicConfig:
        """Load configuration from file or environment variables.

        Returns:
            MythicConfig instance with loaded settings.
        """
        config_data = {}

        if self.config_path.exists():
            try:
                with open(self.config_path, "r") as f:
                    config_data = json.load(f)
            except (json.JSONDecodeError, OSError):
                # Fall back to environment/defaults if config file is unreadable/corrupt.
                config_data = {}

        env_mappings = {
            "MYTHIC_SERVER_URL": "server_url",
            "MYTHIC_API_KEY": "api_key",
            "MYTHIC_USERNAME": "username",
            "MYTHIC_PASSWORD": "password",
            "MYTHIC_VERIFY_SSL": "verify_ssl",
            "MYTHIC_TIMEOUT": "timeout",
            "MYTHIC_TUI_THEME": "tui_theme",
        }

        for env_var, config_key in env_mappings.items():
            value = os.getenv(env_var)
            if value is not None:
                # Handle boolean conversion
                if config_key == "verify_ssl":
                    value = value.lower() in ("true", "1", "yes")
                # Handle int conversion
                elif config_key == "timeout":
                    value = int(value)
                config_data[config_key] = value

        return MythicConfig(**config_data)

    def save_config(self) -> None:
        """Save current configuration to file."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        # Restrict directory permissions (best effort on non-POSIX systems).
        try:
            os.chmod(self.config_path.parent, 0o700)
        except OSError:
            pass

        temp_path = self.config_path.with_suffix(".tmp")
        with open(temp_path, "w") as f:
            json.dump(self.config.model_dump(exclude_none=True), f, indent=2)

        # Restrict file permissions before replacing.
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass

        os.replace(temp_path, self.config_path)

        # Ensure final file permissions remain restrictive.
        try:
            os.chmod(self.config_path, 0o600)
        except OSError:
            pass

    def update_config(self, **kwargs) -> None:
        """Update configuration with new values.

        Args:
            **kwargs: Configuration key-value pairs to update.
        """
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        self.save_config()
