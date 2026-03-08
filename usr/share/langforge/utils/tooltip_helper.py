"""Reusable tooltip helper for GTK4 using Popover (Wayland) or native (X11)."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gdk, GLib

from utils.i18n import _


def _is_x11_backend() -> bool:
    """Check if running on X11 backend."""
    try:
        display = Gdk.Display.get_default()
        if display is None:
            return False
        return "X11" in type(display).__name__
    except Exception:
        return False


def get_tooltips() -> dict[str, str]:
    """Return all tooltip texts keyed by widget identifier."""
    return {
        "select_project": _(
            "Select a project folder with translatable source code.\n\n"
            "Scans for translatable strings using gettext patterns\n"
            "and generates .po files for each target language.\n\n"
            "Supported project types:\n"
            "Python, C/C++, JavaScript, Shell, Vala\n"
            "and any project using gettext"
        ),
        "select_file": _(
            "Select a single file to translate directly.\n\n"
            "Supported formats:\n"
            ".po / .pot — Gettext translation files\n"
            ".json — Key-value translation files\n"
            ".srt — SubRip subtitle files\n"
            ".txt / .md — Plain text and Markdown"
        ),
        "fix_context": _(
            "Re-check existing translations using context-aware AI.\n\n"
            "Detects entries translated without app name/context\n"
            "and fixes only those. Requires an LLM-based API.\n\n"
            "Compatible APIs:\n"
            "OpenAI, Gemini, Groq, Mistral, OpenRouter, Grok\n\n"
            "Not compatible:\n"
            "DeepL, LibreTranslate (no context support)"
        ),
        "api_type": _(
            "Select the API category for translations.\n\n"
            "Free — APIs with free tier or no cost:\n"
            "Groq, Gemini Free, DeepL Free, OpenRouter,\n"
            "Mistral, LibreTranslate\n\n"
            "Paid — APIs that require a billing plan:\n"
            "OpenAI, Gemini Pro, Grok"
        ),
        "api_provider": _(
            "Translation provider to use.\n\n"
            "Only providers with a configured API key\n"
            "are shown here. Add keys in API Settings.\n\n"
            "Providers that don't require a key\n"
            "(e.g. LibreTranslate) are always available."
        ),
        "live_view": _(
            "Open a live view of translations in progress.\n\n"
            "Shows the original text and its translation\n"
            "in real time as each language is processed."
        ),
    }


class TooltipHelper:
    """Singleton popover tooltip with fade animation (Wayland) or native (X11)."""

    def __init__(self):
        self.tooltips = get_tooltips()
        self.active_widget = None
        self.show_timer_id = None
        self._use_native = _is_x11_backend()
        self._color_css_provider = None

        if self._use_native:
            self.popover = None
            self.label = None
            self.css_provider = None
            return

        self.popover = Gtk.Popover()
        self.popover.set_autohide(False)
        self.popover.set_has_arrow(False)
        self.popover.set_position(Gtk.PositionType.TOP)
        self.popover.set_offset(0, -12)

        self.label = Gtk.Label(
            wrap=True,
            max_width_chars=50,
            margin_start=12,
            margin_end=12,
            margin_top=8,
            margin_bottom=8,
            halign=Gtk.Align.START,
        )
        self.popover.set_child(self.label)

        self.css_provider = Gtk.CssProvider()
        css = b"""
        .tooltip-popover {
            opacity: 0;
            transition: opacity 250ms ease-in-out;
        }
        .tooltip-popover.visible {
            opacity: 1;
        }
        """
        self.css_provider.load_from_data(css)
        self.popover.add_css_class("tooltip-popover")

        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            self.css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self.popover.connect("map", self._on_popover_map)
        GLib.idle_add(self._update_colors)

    def _on_popover_map(self, _popover):
        if self.popover:
            self.popover.add_css_class("visible")

    def add_tooltip(self, widget, tooltip_key: str):
        """Bind a tooltip to *widget*."""
        text = self.tooltips.get(tooltip_key, "")
        if self._use_native:
            if text:
                widget.set_tooltip_text(text)
            return

        widget._tooltip_key = tooltip_key
        mc = Gtk.EventControllerMotion.new()
        mc.connect("enter", self._on_enter, widget)
        mc.connect("leave", self._on_leave)
        widget.add_controller(mc)

    # ── hover events ────────────────────────────────────────────

    def _clear_timer(self):
        if self.show_timer_id:
            GLib.source_remove(self.show_timer_id)
            self.show_timer_id = None

    def _on_enter(self, _ctrl, _x, _y, widget):
        if self.active_widget == widget:
            return
        self._clear_timer()
        self._hide()
        self.active_widget = widget
        self.show_timer_id = GLib.timeout_add(250, self._show)

    def _on_leave(self, _ctrl):
        self._clear_timer()
        if self.active_widget:
            self._hide(animate=True)
            self.active_widget = None

    # ── show / hide ─────────────────────────────────────────────

    def _show(self):
        if not self.active_widget or not self.popover:
            return GLib.SOURCE_REMOVE
        try:
            if (
                not self.active_widget.get_mapped()
                or not self.active_widget.get_visible()
                or self.active_widget.get_native() is None
            ):
                self.active_widget = None
                return GLib.SOURCE_REMOVE
        except Exception:
            self.active_widget = None
            return GLib.SOURCE_REMOVE

        key = getattr(self.active_widget, "_tooltip_key", None)
        text = self.tooltips.get(key, "") if key else ""
        if not text:
            return GLib.SOURCE_REMOVE

        try:
            self.label.set_text(text)
            if self.popover.get_parent() is not None:
                self.popover.unparent()
            self.popover.remove_css_class("visible")
            self.popover.set_parent(self.active_widget)
            self.popover.popup()
        except Exception:
            self.active_widget = None
        self.show_timer_id = None
        return GLib.SOURCE_REMOVE

    def _hide(self, animate: bool = False):
        if not self.popover:
            return
        try:
            if not self.popover.is_visible():
                return

            def _cleanup():
                try:
                    if self.popover:
                        self.popover.popdown()
                        if self.popover.get_parent():
                            self.popover.unparent()
                except Exception:
                    pass
                return GLib.SOURCE_REMOVE

            self.popover.remove_css_class("visible")
            if animate:
                GLib.timeout_add(200, _cleanup)
            else:
                _cleanup()
        except Exception:
            pass

    # ── theme-aware colors ──────────────────────────────────────

    def _update_colors(self):
        if self._use_native:
            return
        try:
            is_dark = Adw.StyleManager.get_default().get_dark()
            if is_dark:
                bg, fg, border = "#5c5c5c", "#ffffff", "#707070"
            else:
                bg, fg, border = "#cacaca", "#2e2e2e", "#a0a0a0"
        except Exception:
            bg, fg, border = "#5c5c5c", "#ffffff", "#707070"

        css = (
            f"popover.tooltip-popover > contents {{\n"
            f"    background-color: {bg};\n"
            f"    background-image: none;\n"
            f"    color: {fg};\n"
            f"    border: 1px solid {border};\n"
            f"    border-radius: 8px;\n"
            f"}}\n"
            f"popover.tooltip-popover label {{ color: {fg}; }}\n"
        )

        display = Gdk.Display.get_default()
        if not display:
            return
        if self._color_css_provider:
            try:
                Gtk.StyleContext.remove_provider_for_display(
                    display, self._color_css_provider
                )
            except Exception:
                pass
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        try:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 100
            )
            self._color_css_provider = provider
        except Exception:
            pass

    def cleanup(self):
        """Call on application shutdown."""
        self._clear_timer()
        if not self.popover:
            return
        try:
            if self.popover.get_parent():
                self.popover.unparent()
        except Exception:
            pass
