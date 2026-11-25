"""The Streamlit config must use the same colours as ui/theme.py."""

import re
from pathlib import Path

from ui import theme

CONFIG = Path(__file__).parents[1] / ".streamlit" / "config.toml"


def test_streamlit_config_matches_theme() -> None:
    # A regex instead of tomllib, which needs Python 3.11+.
    config = dict(re.findall(r'^(\w+) = "([^"]*)"', CONFIG.read_text(), re.M))
    assert config["primaryColor"] == theme.PRIMARY
    assert config["backgroundColor"] == theme.BACKGROUND
    assert config["secondaryBackgroundColor"] == theme.SURFACE_MUTED
    assert config["textColor"] == theme.TEXT


def test_css_variables_cover_every_colour() -> None:
    css = theme.css_variables()
    for colour in (theme.PRIMARY, theme.SUCCESS, theme.ERROR, theme.BORDER):
        assert colour in css
