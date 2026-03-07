"""Live translation viewer — shows original vs translated text in diff style."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib, Pango

from utils.i18n import _


class TranslationViewer(Gtk.Box):
    """Embeddable diff-style viewer for translations in progress.

    Shows original text (red/removed) and translated text (green/added)
    in a scrollable, GitHub-like diff format.  Designed to be packed
    inside a Gtk.Paned on the progress page.
    """

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)

        self._scroll_pending = False
        self._max_lines = 3000  # Trim buffer when exceeded

        # Inline toolbar: status + controls
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_start(8)
        toolbar.set_margin_end(8)
        toolbar.set_margin_top(4)
        toolbar.set_margin_bottom(4)

        self._status = Gtk.Label(label=_("Waiting..."))
        self._status.add_css_class("dim-label")
        self._status.set_xalign(0)
        self._status.set_hexpand(True)
        toolbar.append(self._status)

        self._counter_label = Gtk.Label(label="0")
        self._counter_label.add_css_class("dim-label")
        self._entry_count = 0
        toolbar.append(self._counter_label)

        self._auto_scroll = True
        scroll_toggle = Gtk.ToggleButton(icon_name="go-bottom-symbolic")
        scroll_toggle.set_active(True)
        scroll_toggle.set_tooltip_text(_("Auto-scroll"))
        scroll_toggle.connect("toggled", self._on_scroll_toggled)
        toolbar.append(scroll_toggle)

        clear_btn = Gtk.Button(icon_name="edit-clear-all-symbolic")
        clear_btn.set_tooltip_text(_("Clear"))
        clear_btn.connect("clicked", self._on_clear)
        toolbar.append(clear_btn)

        self.append(toolbar)

        # Separator between toolbar and text
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

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
        self.append(self._scroll)

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
        """Append multiple (original, translated, index) pairs efficiently.

        Builds text in a single string and inserts once to minimise
        buffer change notifications, then applies tags by range.
        """
        parts: list[str] = []
        tag_ranges: list[tuple[str, int, int]] = []
        offset = self._buffer.get_char_count()

        for original, translated, index in pairs:
            if index:
                text = f"@@ {index} @@\n"
                tag_ranges.append(("index", offset, offset + len(text)))
                parts.append(text)
                offset += len(text)
            for line in original.splitlines():
                text = f"- {line}\n"
                tag_ranges.append(("removed", offset, offset + len(text)))
                parts.append(text)
                offset += len(text)
            for line in translated.splitlines():
                text = f"+ {line}\n"
                tag_ranges.append(("added", offset, offset + len(text)))
                parts.append(text)
                offset += len(text)
            text = "·\n"
            tag_ranges.append(("separator", offset, offset + len(text)))
            parts.append(text)
            offset += len(text)

        # Single buffer insert
        end = self._buffer.get_end_iter()
        self._buffer.insert(end, "".join(parts))

        # Apply tags by range
        for tag_name, start_off, end_off in tag_ranges:
            s = self._buffer.get_iter_at_offset(start_off)
            e = self._buffer.get_iter_at_offset(end_off)
            self._buffer.apply_tag_by_name(tag_name, s, e)

        self._entry_count += len(pairs)
        self._counter_label.set_label(str(self._entry_count))
        self._trim_buffer()
        self._do_scroll()

    def mark_done(self, summary: str):
        """Show completion summary at the bottom."""
        end = self._buffer.get_end_iter()
        self._buffer.insert_with_tags_by_name(
            end, f"\n═══ {summary} ═══\n", "lang_header"
        )
        self._status.set_label(summary)
        self._do_scroll()

    def _trim_buffer(self):
        """Remove oldest lines when buffer exceeds max to prevent UI slowdown."""
        line_count = self._buffer.get_line_count()
        if line_count > self._max_lines:
            trim_to = line_count // 3
            start = self._buffer.get_start_iter()
            trim_iter = self._buffer.get_iter_at_line(trim_to)
            self._buffer.delete(start, trim_iter)

    def _do_scroll(self):
        """Scroll to bottom if auto-scroll is enabled (throttled)."""
        if self._auto_scroll and not self._scroll_pending:
            self._scroll_pending = True
            GLib.idle_add(self._scroll_to_end)

    def _scroll_to_end(self):
        adj = self._scroll.get_vadjustment()
        adj.set_value(adj.get_upper())
        self._scroll_pending = False
        return GLib.SOURCE_REMOVE

    def _on_clear(self, _btn):
        self._buffer.set_text("")
        self._entry_count = 0
        self._counter_label.set_label("0")

    def _on_scroll_toggled(self, btn):
        self._auto_scroll = btn.get_active()
        if self._auto_scroll:
            self._scroll_to_end()

    def clear(self):
        """Reset the viewer for a new translation run."""
        self._buffer.set_text("")
        self._entry_count = 0
        self._counter_label.set_label("0")
        self._status.set_label(_("Waiting..."))
