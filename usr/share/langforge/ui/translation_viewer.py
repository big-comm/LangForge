"""Live translation viewer — shows original vs translated text in diff style."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk, GLib, Pango

from utils.i18n import _


class TranslationViewer(Adw.Window):
    """Real-time diff-style viewer for translations in progress.

    Shows original text (red/removed) and translated text (green/added)
    in a scrollable, GitHub-like diff format.
    """

    def __init__(self, parent: Gtk.Window, **kwargs):
        super().__init__(
            title=_("Live Translation"),
            default_width=700,
            default_height=500,
            transient_for=parent,
            **kwargs,
        )

        # Main layout
        header = Adw.HeaderBar()
        header.add_css_class("flat")

        self._clear_btn = Gtk.Button(icon_name="edit-clear-all-symbolic")
        self._clear_btn.set_tooltip_text(_("Clear"))
        self._clear_btn.connect("clicked", self._on_clear)
        header.pack_start(self._clear_btn)

        self._auto_scroll = True
        scroll_toggle = Gtk.ToggleButton(icon_name="go-bottom-symbolic")
        scroll_toggle.set_active(True)
        scroll_toggle.set_tooltip_text(_("Auto-scroll"))
        scroll_toggle.connect("toggled", self._on_scroll_toggled)
        header.pack_end(scroll_toggle)

        # Counter label in header
        self._counter_label = Gtk.Label(label="0")
        self._counter_label.add_css_class("dim-label")
        header.pack_end(self._counter_label)
        self._entry_count = 0

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)

        # Status bar showing current language
        self._status = Gtk.Label(label=_("Waiting..."))
        self._status.add_css_class("dim-label")
        self._status.set_xalign(0)
        self._status.set_margin_start(12)
        self._status.set_margin_top(4)
        self._status.set_margin_bottom(4)
        box.append(self._status)

        # Text view with diff-style tags
        self._buffer = Gtk.TextBuffer()
        self._setup_tags()

        self._textview = Gtk.TextView(buffer=self._buffer)
        self._textview.set_editable(False)
        self._textview.set_cursor_visible(False)
        self._textview.set_monospace(True)
        self._textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._textview.set_left_margin(12)
        self._textview.set_right_margin(12)
        self._textview.set_top_margin(8)
        self._textview.set_bottom_margin(8)
        self._textview.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Live translation diff view")],
        )

        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_child(self._textview)
        self._scroll.set_vexpand(True)
        self._scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        box.append(self._scroll)

        self.set_content(box)

        # Keyboard: Escape to close
        esc = Gtk.ShortcutController()
        esc.add_shortcut(
            Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                action=Gtk.CallbackAction.new(lambda *_: self.close()),
            )
        )
        self.add_controller(esc)

    def _setup_tags(self):
        """Create text tags for diff-style coloring."""
        # Removed line (original) — red background
        self._buffer.create_tag(
            "removed",
            foreground="#f66",
            paragraph_background="rgba(255,80,80,0.08)",
        )
        # Added line (translated) — green background
        self._buffer.create_tag(
            "added",
            foreground="#5c5",
            paragraph_background="rgba(80,200,80,0.08)",
        )
        # Language header — bold, accent color
        self._buffer.create_tag(
            "lang_header",
            foreground="#78aeed",
            weight=Pango.Weight.BOLD,
            pixels_above_lines=8,
            pixels_below_lines=4,
        )
        # Separator line
        self._buffer.create_tag(
            "separator",
            foreground="rgba(128,128,128,0.4)",
            scale=0.8,
        )
        # Index / context hint
        self._buffer.create_tag(
            "index",
            foreground="rgba(128,128,128,0.6)",
            scale=0.85,
        )

    def set_language(self, lang_code: str, lang_name: str):
        """Show a language header when translation switches to a new language."""
        end = self._buffer.get_end_iter()
        if self._buffer.get_char_count() > 0:
            self._buffer.insert(end, "\n")
            end = self._buffer.get_end_iter()
        header = f"─── {lang_name} ({lang_code}) ───\n"
        self._buffer.insert_with_tags_by_name(end, header, "lang_header")
        self._status.set_label(f"{lang_name} ({lang_code})")
        self._do_scroll()

    def add_pair(self, original: str, translated: str, index: str = ""):
        """Append an original/translated pair in diff style."""
        end = self._buffer.get_end_iter()

        # Optional index (e.g. subtitle number, JSON key)
        if index:
            self._buffer.insert_with_tags_by_name(
                end, f"@@ {index} @@\n", "index"
            )
            end = self._buffer.get_end_iter()

        # Original (removed) lines
        for line in original.splitlines():
            self._buffer.insert_with_tags_by_name(
                end, f"- {line}\n", "removed"
            )
            end = self._buffer.get_end_iter()

        # Translated (added) lines
        for line in translated.splitlines():
            self._buffer.insert_with_tags_by_name(
                end, f"+ {line}\n", "added"
            )
            end = self._buffer.get_end_iter()

        # Thin separator
        self._buffer.insert_with_tags_by_name(end, "·\n", "separator")

        self._entry_count += 1
        self._counter_label.set_label(str(self._entry_count))
        self._do_scroll()

    def add_batch(self, pairs: list[tuple[str, str, str]]):
        """Append multiple (original, translated, index) pairs efficiently."""
        end = self._buffer.get_end_iter()
        for original, translated, index in pairs:
            if index:
                self._buffer.insert_with_tags_by_name(
                    end, f"@@ {index} @@\n", "index"
                )
                end = self._buffer.get_end_iter()
            for line in original.splitlines():
                self._buffer.insert_with_tags_by_name(
                    end, f"- {line}\n", "removed"
                )
                end = self._buffer.get_end_iter()
            for line in translated.splitlines():
                self._buffer.insert_with_tags_by_name(
                    end, f"+ {line}\n", "added"
                )
                end = self._buffer.get_end_iter()
            self._buffer.insert_with_tags_by_name(end, "·\n", "separator")
            end = self._buffer.get_end_iter()

        self._entry_count += len(pairs)
        self._counter_label.set_label(str(self._entry_count))
        self._do_scroll()

    def mark_done(self, summary: str):
        """Show completion summary at the bottom."""
        end = self._buffer.get_end_iter()
        self._buffer.insert_with_tags_by_name(
            end, f"\n═══ {summary} ═══\n", "lang_header"
        )
        self._status.set_label(summary)
        self._do_scroll()

    def _do_scroll(self):
        """Scroll to bottom if auto-scroll is enabled."""
        if self._auto_scroll:
            GLib.idle_add(self._scroll_to_end)

    def _scroll_to_end(self):
        adj = self._scroll.get_vadjustment()
        adj.set_value(adj.get_upper())

    def _on_clear(self, _btn):
        self._buffer.set_text("")
        self._entry_count = 0
        self._counter_label.set_label("0")

    def _on_scroll_toggled(self, btn):
        self._auto_scroll = btn.get_active()
        if self._auto_scroll:
            self._scroll_to_end()
