"""Presentational pieces in the style of olostep.com tool pages.

Nav bar, sky hero, macOS-style tool windows, stat tiles and the
"how it works" step grid. Pure HTML/CSS; no pipeline logic.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from ui import theme

STYLES_FILE = Path(__file__).parent / "styles.css"
OLOSTEP_LINKS = {
    "Docs": "https://docs.olostep.com",
    "All tools": "https://www.olostep.com/tools",
}
API_KEY_URL = "https://www.olostep.com/dashboard/api-keys"

# Simple 24px outline icons (stroke = currentColor, like the site's icons).
ICONS = {
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "layers": '<path d="M12 2 2 7l10 5 10-5-10-5Z"/><path d="m2 17 10 5 10-5"/>'
    '<path d="m2 12 10 5 10-5"/>',
    "download": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
}


@dataclass(frozen=True)
class Tile:
    """One stat tile: a label, a value and an optional caption."""

    label: str
    value: str
    caption: str = ""
    tone: str = ""  # "", "primary", "success" or "error"


def apply_theme() -> None:
    """Inject the theme variables and the stylesheet."""
    css = theme.css_variables() + "\n" + STYLES_FILE.read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def nav() -> None:
    """The site's top bar: wordmark left, links and a blue pill right."""
    links = "".join(
        f'<a href="{url}" target="_blank">{_e(label)}</a>'
        for label, url in OLOSTEP_LINKS.items()
    )
    st.markdown(
        f'<div class="op-nav op-bleed"><div class="op-nav-inner">'
        f'<img class="op-nav-logo" src="{theme.LOGO_IMAGE}" alt="olostep">'
        f'<div class="op-nav-links">{links}'
        f'<a class="op-btn primary" href="{API_KEY_URL}" target="_blank">'
        f"Get API key</a></div></div></div>",
        unsafe_allow_html=True,
    )


def hero(title: str, subtitle: str, actions: list[tuple[str, str, str]]) -> None:
    """Sky hero with a centred title. ``actions`` are ``(label, href, kind)``."""
    buttons = "".join(
        f'<a class="op-btn {kind}" href="{href}" target="_self">{_e(label)}</a>'
        for label, href, kind in actions
    )
    st.markdown(
        f'<div class="op-hero op-bleed"><h1>{_e(title)}</h1>'
        f"<p>{_e(subtitle)}</p>"
        f'<div class="op-hero-actions">{buttons}</div></div>',
        unsafe_allow_html=True,
    )


def window(
    key: str, title: str, *, footer: bool = False
) -> tuple[DeltaGenerator, DeltaGenerator | None]:
    """A macOS-style tool window: title bar with three dots, a padded body
    and, optionally, a bordered footer row for actions.

    Returns ``(body, footer)`` containers; write into them with ``with``.
    """
    with st.container(key=f"window-{key}"):
        st.markdown(
            '<div class="op-titlebar">'
            + '<span class="op-dot"></span>' * 3
            + f'<span class="op-titlebar-title">{_e(title)}</span></div>',
            unsafe_allow_html=True,
        )
        body = st.container(key=f"body-{key}")
        bottom = st.container(key=f"footer-{key}") if footer else None
    return body, bottom


def label(text: str) -> None:
    st.markdown(f'<div class="op-label">{_e(text)}</div>', unsafe_allow_html=True)


def help_line(items: list[str]) -> None:
    """Small grey help text; several items are joined with a dot."""
    sep = '<span class="op-sep">·</span>'
    body = sep.join(_e(item) for item in items)
    st.markdown(f'<div class="op-help">{body}</div>', unsafe_allow_html=True)


def tiles(items: list[Tile]) -> None:
    """A row of joined stat tiles (the site's bordered-column style)."""
    cells = "".join(
        f'<div class="op-tile"><div class="op-tile-label">{_e(t.label)}</div>'
        f'<div class="op-tile-value {t.tone}">{_e(t.value)}</div>'
        f'<div class="op-tile-caption">{_e(t.caption)}</div></div>'
        for t in items
    )
    columns = f"grid-template-columns: repeat({len(items)}, 1fr)"
    st.markdown(
        f'<div class="op-tiles" style="{columns}">{cells}</div>',
        unsafe_allow_html=True,
    )


def section(title: str, subtitle: str) -> None:
    """A centred section heading, like "How to ... in 3 steps"."""
    st.markdown(
        f'<div class="op-section"><h2>{_e(title)}</h2><p>{_e(subtitle)}</p></div>',
        unsafe_allow_html=True,
    )


def steps(items: list[tuple[str, str, str]]) -> None:
    """The step grid. Each item is ``(icon_name, title, text)``."""
    cells = "".join(
        f'<div class="op-tile"><span class="op-step-icon">{_icon(icon)}</span>'
        f'<div class="op-step-title">{_e(title)}</div>'
        f'<div class="op-step-text">{_e(text)}</div></div>'
        for icon, title, text in items
    )
    columns = f"grid-template-columns: repeat({len(items)}, 1fr)"
    st.markdown(
        f'<div class="op-tiles" style="{columns}">{cells}</div>',
        unsafe_allow_html=True,
    )


def chips(items: list[tuple[str, str]]) -> None:
    """Count chips, e.g. ``[("1,095", "No Markdown")]``."""
    body = "".join(
        f'<span class="op-chip"><b>{_e(count)}</b>{_e(text)}</span>'
        for count, text in items
    )
    st.markdown(f'<div class="op-chips">{body}</div>', unsafe_allow_html=True)


def milestone(line: str) -> None:
    st.markdown(f'<div class="op-milestone">{_e(line)}</div>', unsafe_allow_html=True)


def note(text: str, tone: str = "") -> None:
    """Small status text; ``tone`` is "", "ok" or "error"."""
    st.markdown(f'<p class="op-note {tone}">{_e(text)}</p>', unsafe_allow_html=True)


def empty(text: str) -> None:
    """An empty state: what is missing and how to get it."""
    st.markdown(f'<div class="op-empty">{_e(text)}</div>', unsafe_allow_html=True)


def _icon(name: str) -> str:
    return (
        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
        f'stroke-linejoin="round">{ICONS[name]}</svg>'
    )


def _e(text: object) -> str:
    return html.escape(str(text))
