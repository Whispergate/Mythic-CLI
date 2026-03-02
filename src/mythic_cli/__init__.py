"""
Mythic CLI - Command-Line Interface for Mythic C2

A powerful, interactive CLI tool for managing Mythic C2 operations.

Quick Start:
    $ pipx install mythic-cli
    $ mythic
    mythic (✗) > login
    mythic (✅) > callbacks

Features:
    - Rich terminal interface with colors and formatting
    - Interactive shell with tab completion and history
    - Callback and task management
    - Payload generation
    - C2 profile configuration
    - File operations
    - Credential tracking
    - Only exits on explicit 'exit' command

For more information, see:
    https://github.com/yourusername/mythic-cli
"""

__version__ = "0.1.0"

from .client import MythicClient, MythicAPIException
from .config import ConfigManager, MythicConfig
from .shell import MythicShell

__all__ = [
    "MythicClient",
    "MythicAPIException",
    "ConfigManager",
    "MythicConfig",
    "MythicShell",
    "__version__",
]
