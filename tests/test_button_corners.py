"""Button images have transparent rounded corners that show the widget's
background. clam swaps that background for its own greys when a button is
disabled, hovered or pressed, which lit the corners of disabled buttons
(Pause, Stop and Edit settings with no target selected)."""
from tkinter import ttk


def test_buttons_keep_their_surface_behind_the_corners_in_every_state(app):
    st = ttk.Style(app.root)
    c = app.look.c
    for style, surface in (("TButton", c["bg"]), ("Accent.TButton", c["bg"]),
                           ("Subtle.TButton", c["bg"]), ("Card.TButton", c["surface"]),
                           ("Card.Accent.TButton", c["surface"]), ("Card.Icon.Subtle.TButton", c["surface"])):
        for state in ("disabled", "active", "pressed"):
            assert st.lookup(style, "background", [state]).lower() == surface.lower(), (style, state)
