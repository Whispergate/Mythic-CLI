"""Theme helpers for Mythic CLI TUI."""

from typing import Dict, List

# User-facing theme names and matching Rich syntax themes.
_SUPPORTED_THEMES: Dict[str, str] = {
    "default": "ansi_dark",
    "monokai": "monokai",
    "dracula": "dracula",
    "nord": "nord",
    "solarized": "solarized-dark",
}

# Prompt palette per theme.
_PROMPT_STYLES: Dict[str, Dict[str, str]] = {
    "default": {"prompt": "#00afaf bold", "": "#ffffff"},
    "monokai": {"prompt": "#66d9ef bold", "": "#ffffff"},
    "dracula": {"prompt": "#bd93f9 bold", "": "#f8f8f2"},
    "nord": {"prompt": "#81a1c1 bold", "": "#eceff4"},
    "solarized": {"prompt": "#b58900 bold", "": "#fdf6e3"},
}


def normalize_theme_name(name: str) -> str:
    """Normalize and validate a theme name."""
    key = (name or "").strip().lower()
    if key in _SUPPORTED_THEMES:
        return key
    return "default"


def get_supported_themes() -> List[str]:
    """Return all supported theme names."""
    return list(_SUPPORTED_THEMES.keys())


def get_syntax_theme(name: str) -> str:
    """Get Rich Syntax theme name for a configured TUI theme."""
    key = normalize_theme_name(name)
    return _SUPPORTED_THEMES[key]


def get_prompt_style_map(name: str) -> Dict[str, str]:
    """Get prompt style map for prompt_toolkit Style.from_dict."""
    key = normalize_theme_name(name)
    return _PROMPT_STYLES.get(key, _PROMPT_STYLES["default"])
