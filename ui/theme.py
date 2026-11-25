"""Olostep's visual identity, measured from the live site (2026-11-25).

Values are computed styles read from olostep.com tool pages
(/website-to-markdown, /tools/broken-url-detector) and /tools. The site uses
a light theme. Nothing here is invented: each value names where it came
from. These are the only colour definitions in the project;
``.streamlit/config.toml`` mirrors the main ones (a test keeps them in sync).
"""

# --- Colours -----------------------------------------------------------------
PRIMARY = "#1369E6"  # .button-blue: "Convert to Markdown", "Sign Up", "Open and Run"
BACKGROUND = "#FFFFFF"  # page background
SURFACE = "#FFFFFF"  # tool window and inputs
SURFACE_MUTED = "#FAFAFA"  # tool window title bar (bg-[#FAFAFA])
HEADING = "#26251E"  # h1 ("Website to Markdown Converter")
TEXT = "#333333"  # body text, labels, section h2
MUTED = "#737373"  # hero subtitle, help text, window title
BUTTON_TEXT_SECONDARY = "rgba(0, 0, 0, 0.82)"  # .button-white text
BORDER = "#E5E7EB"  # .button-white / .button-blue border
INPUT_BORDER = "#D1D1D1"  # URL input border and title bar bottom border
TILE_BORDER = "#EFEFEF"  # "3 steps" columns (li)
WINDOW_BORDER = "rgba(0, 0, 0, 0.16)"  # tool window border (border-black/[0.16])
WINDOW_DOT = "rgba(223, 222, 222, 0.53)"  # the three title bar dots
NAV_BACKGROUND = "rgba(255, 255, 255, 0.8)"  # .w-nav navbar
SUCCESS = "#009DA3"  # .success-message-3 in olostep.webflow.css
ERROR = "#CD3D64"  # .error-text in olostep.webflow.css

# --- Effects -----------------------------------------------------------------
WINDOW_SHADOW = "rgba(39, 39, 42, 0.16) 0px 10px 40px -10px"  # tool window
# Hero: a white-fading gradient over the sky image, as on the tool pages.
HERO_GRADIENT = "linear-gradient(rgba(240, 245, 250, 0.06) 0%, rgb(255, 255, 255) 100%)"
HERO_IMAGE = "app/static/hero-sky.jpg"  # /images/broken-link-checker/hero-sky.jpg
LOGO_IMAGE = "app/static/olostep-logo.png"  # official wordmark

# --- Type --------------------------------------------------------------------
FONT_BODY = "'Helvetica Neue', Helvetica, Arial, sans-serif"  # labels, help, text
FONT_HEADING = "Arial, Helvetica, sans-serif"  # h1, h2, buttons

# --- Shape and spacing -------------------------------------------------------
RADIUS_WINDOW = "10px"  # tool window
RADIUS_INPUT = "10px"  # URL input
RADIUS_BUTTON = "50px"  # pill buttons
RADIUS_TILE = "5px"  # "3 steps" columns
BUTTON_PADDING = "9px 24px"  # .button-blue
WINDOW_PADDING = "18px 20px 16px"  # tool window form
TILE_PADDING = "20px"  # "3 steps" columns


def css_variables() -> str:
    """The theme as CSS custom properties, used by ``ui/styles.css``."""
    values = {
        "primary": PRIMARY,
        "bg": BACKGROUND,
        "surface": SURFACE,
        "surface-muted": SURFACE_MUTED,
        "heading": HEADING,
        "text": TEXT,
        "muted": MUTED,
        "button-text-secondary": BUTTON_TEXT_SECONDARY,
        "border": BORDER,
        "input-border": INPUT_BORDER,
        "tile-border": TILE_BORDER,
        "window-border": WINDOW_BORDER,
        "window-dot": WINDOW_DOT,
        "nav-bg": NAV_BACKGROUND,
        "success": SUCCESS,
        "error": ERROR,
        "window-shadow": WINDOW_SHADOW,
        "hero-bg": f"{HERO_GRADIENT}, url('{HERO_IMAGE}')",
        "font-body": FONT_BODY,
        "font-heading": FONT_HEADING,
        "radius-window": RADIUS_WINDOW,
        "radius-input": RADIUS_INPUT,
        "radius-button": RADIUS_BUTTON,
        "radius-tile": RADIUS_TILE,
        "button-padding": BUTTON_PADDING,
        "window-padding": WINDOW_PADDING,
        "tile-padding": TILE_PADDING,
    }
    lines = "\n".join(f"  --{name}: {value};" for name, value in values.items())
    return f":root {{\n{lines}\n}}"
