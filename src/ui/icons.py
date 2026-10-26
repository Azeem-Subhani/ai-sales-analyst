"""Lucide icons for the dashboard (Week 7), drawn with CSS masks.

Source: lucide-static 1.52.0 (https://lucide.dev), ISC licence:
Copyright (c) 2026 Lucide Icons and Contributors. Permission to use, copy, modify,
and/or distribute this software for any purpose with or without fee is hereby granted,
provided that the above copyright notice and this permission notice appear in all copies.

st.html strips inline <svg> (its sanitiser allows HTML only), so each icon becomes a CSS
class that paints currentColor through the icon as a mask: <span class="icon i-eye">.
Widgets (page links, buttons) get their icon from a ::before rule on their st-key-...
container. Every icon is 24x24, stroke 1.5, shown at 16px.
"""

from urllib.parse import quote

STROKE_WIDTH = 1.5
# Icon name -> the SVG shapes inside the <svg> element, copied from lucide-static.
SHAPES = {
    "arrow-up": '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    "arrow-down": '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    "minus": '<path d="M5 12h14"/>',
    "chart-no-axes-column": '<path d="M5 21v-6"/><path d="M12 21V3"/><path d="M19 21V9"/>',
    "chart-column": (
        '<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M18 17V9"/>'
        '<path d="M13 17V5"/><path d="M8 17v-3"/>'
    ),
    "package": (
        '<path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4'
        'a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"/><path d="M12 22V12"/>'
        '<polyline points="3.29 7 12 12 20.71 7"/><path d="m7.5 4.27 9 5.15"/>'
    ),
    "message-square": (
        '<path d="M22 17a2 2 0 0 1-2 2H6.828a2 2 0 0 0-1.414.586l-2.202 2.202'
        'A.71.71 0 0 1 2 21.286V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2z"/>'
    ),
    "file-text": (
        '<path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588'
        'A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"/><path d="M14 2v5a1 1 0 0 0 1 1h5"/>'
        '<path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>'
    ),
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    # Our own brain (not from Lucide), drawn in the same 24px, stroke-1.5 style: two
    # rounded lobes on a centre line, with two folds each. Shown while the model thinks.
    "brain": (
        '<path d="M12 4.5C11 3 8.5 3 7.5 4.5 5.2 4.4 4 6.5 4.6 8.4 3 9.4 2.8 12 4.4 13.2'
        ' 3.6 15 4.6 17.4 6.8 17.6 7.4 19.6 10.2 20.4 12 19"/>'
        '<path d="M12 4.5C13 3 15.5 3 16.5 4.5 18.8 4.4 20 6.5 19.4 8.4 21 9.4 21.2 12 19.6'
        ' 13.2 20.4 15 19.4 17.4 17.2 17.6 16.6 19.6 13.8 20.4 12 19"/>'
        '<path d="M12 4.5V19"/><path d="M8 8.2c1.3.1 2.3 1 2.3 2.3"/>'
        '<path d="M6.8 13.6c1.2.6 2.6.3 3.4-.8"/><path d="M16 8.2c-1.3.1-2.3 1-2.3 2.3"/>'
        '<path d="M17.2 13.6c-1.2.6-2.6.3-3.4-.8"/>'
    ),
    "wrench": (
        '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.106-3.105c.32-.322.863-.22'
        ".983.218a6 6 0 0 1-8.259 7.057l-7.91 7.91a1 1 0 0 1-2.999-3l7.91-7.91a6 6 0 0 1"
        ' 7.057-8.259c.438.12.54.662.219.984z"/>'
    ),
    "circle-check": '<circle cx="12" cy="12" r="10"/><path d="m16 9-5.5 5.5L8 12"/>',
    "circle-x": '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "calendar": (
        '<path d="M8 2v3"/><path d="M16 2v3"/><rect x="3" y="3" width="18" height="18" rx="2"/>'
        '<path d="M3 9h18"/>'
    ),
    "calendar-range": (
        '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M16 2v3"/><path d="M3 9h18"/>'
        '<path d="M8 2v3"/><path d="M17 13h-6"/><path d="M13 17H7"/><path d="M7 13h.01"/>'
        '<path d="M17 17h.01"/>'
    ),
    "shield-check": (
        '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1'
        " 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"
        '"/><path d="m9 12 2 2 4-4"/>'
    ),
    "pencil-line": (
        '<path d="M13 21h8"/><path d="m15 5 4 4"/>'
        '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352'
        'a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/>'
    ),
    "trash-2": (
        '<path d="M10 11v6"/><path d="M14 11v6"/>'
        '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M3 6h18"/>'
        '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
    ),
    "users": (
        '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><path d="M16 3.128a4 4 0 0 1 0 7.744"/>'
        '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/><circle cx="9" cy="7" r="4"/>'
    ),
    "package-x": (
        '<path d="M12 22V12"/><path d="m16.5 14.5 5 5"/><path d="m16.5 19.5 5-5"/>'
        '<path d="M21 10.5V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8'
        'a2 2 0 0 0 1 1.729l7 4a2 2 0 0 0 2 .001l.13-.074"/><path d="M3.29 7 12 12l8.71-5"/>'
        '<path d="m7.5 4.27 8.997 5.148"/>'
    ),
    "target": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    "factory": (
        '<path d="M12 16h.01"/><path d="M16 16h.01"/><path d="M3 19a2 2 0 0 0 2 2h14a2 2 0 0 0'
        " 2-2V8.5a.5.5 0 0 0-.769-.422l-4.462 2.844A.5.5 0 0 1 15 10.5v-2a.5.5 0 0 0-.769-.422"
        'L9.77 10.922A.5.5 0 0 1 9 10.5V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2z"/><path d="M8 16h.01"/>'
    ),
    "eye": (
        '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696'
        ' 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/>'
    ),
    "download": (
        '<path d="M12 15V3"/><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<path d="m7 10 5 5 5-5"/>'
    ),
    "triangle-alert": (
        '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
        '<path d="M12 9v4"/><path d="M12 17h.01"/>'
    ),
    "circle-alert": (
        '<circle cx="12" cy="12" r="10"/><line x1="12" x2="12" y1="8" y2="12"/>'
        '<line x1="12" x2="12.01" y1="16" y2="16"/>'
    ),
}


def svg(name: str) -> str:
    """The full SVG document of one icon (black strokes, for use as a mask)."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="black" stroke-width="{STROKE_WIDTH}" stroke-linecap="round" '
        f'stroke-linejoin="round">{SHAPES[name]}</svg>'
    )


def mask_url(name: str) -> str:
    """The icon as a CSS url() value, percent-encoded so the CSS holds no '<'."""
    return f'url("data:image/svg+xml,{quote(svg(name), safe=" =:/")}")'


def mask_rule(selector: str, name: str) -> str:
    """A CSS rule that draws an icon on the elements matching selector."""
    url = mask_url(name)
    return f"{selector}{{-webkit-mask-image:{url};mask-image:{url}}}"


def icon(name: str, extra_class: str = "") -> str:
    """An inline icon element (its look comes from the .icon rule in style.css)."""
    classes = f"icon i-{name} {extra_class}".strip()
    return f'<span class="{classes}" aria-hidden="true"></span>'


def icon_css(widget_icons: dict[str, str]) -> str:
    """CSS for every icon class, plus pseudo-element icons on keyed widgets.

    widget_icons maps a CSS selector (such as '.st-key-nav-sales a') to an icon name.
    The icon goes on ::before unless the selector already names a pseudo-element
    (e.g. '... > summary::after').
    """
    rules = [mask_rule(f".i-{name}", name) for name in SHAPES]
    rules += [
        mask_rule(selector if "::" in selector else f"{selector}::before", name)
        for selector, name in widget_icons.items()
    ]
    return "\n".join(rules)
