"""Main application window - Modern Adwaita Style."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk, Gio
import math
from pathlib import Path

from config.settings import Settings
from core.controller import TranslationController
from core.file_translator import SUPPORTED_EXTENSIONS
from core.languages import SUPPORTED_LANGUAGES
from ui.settings_dialog import SettingsDialog
from ui.translation_viewer import TranslationViewer
from utils.i18n import _
from utils.tooltip_helper import TooltipHelper


def _apply_row_tooltip(row: Gtk.Widget, text: str) -> None:
    """Apply a tooltip to an Adw row using query-tooltip signal."""
    if not text:
        return
    row.set_has_tooltip(True)

    def _on_query(widget, x, y, keyboard, tooltip):
        tooltip.set_text(text)
        return True

    row.connect("query-tooltip", _on_query)


def _humanize_error(e: Exception) -> str:
    """Convert common exceptions to user-friendly messages (M5)."""
    msg = str(e).lower()
    if "connection" in msg or "connect" in msg or "network" in msg:
        return _(
            "Could not connect to the translation service. "
            "Check your internet connection."
        )
    if "401" in msg or "unauthorized" in msg or "invalid api key" in msg:
        return _("Invalid API key. Check your key in Settings.")
    if "403" in msg or "forbidden" in msg:
        return _("Access denied. Your API key may lack the required permissions.")
    if "429" in msg or "rate limit" in msg or "quota" in msg:
        return _("API rate limit reached. Wait a moment and try again.")
    if "timeout" in msg:
        return _("The translation service took too long to respond. Try again later.")
    if "gettext" in msg or "xgettext" in msg:
        return _("gettext tools not found. Install gettext on your system.")
    if "msgfmt" in msg:
        return _("msgfmt not found. Install gettext on your system.")
    # Fallback: show original but truncated
    text = str(e)
    if len(text) > 120:
        text = text[:120] + "…"
    return text


class ProgressRing(Gtk.DrawingArea):
    """Circular progress widget with accessibility support."""

    def __init__(self):
        super().__init__()
        self._progress = 0.0
        self.set_size_request(80, 80)
        self.set_draw_func(self._draw)
        # Accessibility: announce as a progress indicator
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Translation progress")],
        )

    def _draw(self, area, cr, width, height):
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 6
        line_width = 6

        # Query Adwaita theme colors for consistent appearance
        style = self.get_style_context()
        fg_ok, fg_color = style.lookup_color("window_fg_color")
        accent_ok, accent_color = style.lookup_color("accent_bg_color")

        cr.set_line_width(line_width)
        if fg_ok:
            cr.set_source_rgba(fg_color.red, fg_color.green, fg_color.blue, 0.15)
        else:
            cr.set_source_rgba(0.3, 0.3, 0.3, 0.5)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        if self._progress > 0:
            cr.set_line_cap(1)
            if accent_ok:
                cr.set_source_rgba(
                    accent_color.red, accent_color.green, accent_color.blue, 1.0
                )
            else:
                cr.set_source_rgba(0.35, 0.55, 0.95, 1.0)
            start = -math.pi / 2
            end = start + (2 * math.pi * self._progress)
            cr.arc(cx, cy, radius, start, end)
            cr.stroke()

        if fg_ok:
            cr.set_source_rgba(fg_color.red, fg_color.green, fg_color.blue, 0.9)
        else:
            cr.set_source_rgba(1, 1, 1, 0.9)
        cr.select_font_face("Sans", 0, 1)
        cr.set_font_size(16)
        text = f"{int(self._progress * 100)}%"
        ext = cr.text_extents(text)
        cr.move_to(cx - ext.width / 2, cy + ext.height / 3)
        cr.show_text(text)

    def set_progress(self, value: float):
        self._progress = max(0.0, min(1.0, value))
        self.queue_draw()
        # Update accessible description so Orca announces progress changes
        pct = int(self._progress * 100)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Translation progress: {}%").format(pct)],
        )


class MainWindow(Adw.ApplicationWindow):
    """Main window - Modern Adwaita Split View."""

    def __init__(self, app):
        super().__init__(application=app)
        self.settings = Settings()
        self.controller = TranslationController(self.settings)
        self.tooltip_helper = TooltipHelper()
        self.selected_project = None
        self.selected_file = None
        self._string_count = 0
        self._mode = "project"  # "project" or "file"

        self.set_title("LangForge")
        self.set_default_size(1020, 720)

        self._load_css()
        self._build_ui()

    def _load_css(self):
        css_path = Path(__file__).parent / "style.css"
        if css_path.exists():
            provider = Gtk.CssProvider()
            provider.load_from_path(str(css_path))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

    def _build_ui(self):
        """Build the main UI with split view layout."""
        # Toast overlay wraps everything
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        # Split view: sidebar + content
        split_view = Adw.OverlaySplitView()
        split_view.set_min_sidebar_width(260)
        split_view.set_max_sidebar_width(320)
        split_view.set_sidebar_width_fraction(0.32)
        self.toast_overlay.set_child(split_view)

        # Build panes
        split_view.set_sidebar(self._build_sidebar())
        split_view.set_content(self._build_content())

        # Show first-run welcome or drop page
        if self.settings.is_first_run():
            self._show_first_run_welcome()
        else:
            self.stack.set_visible_child_name("drop")

    # ── Sidebar ─────────────────────────────────────────────────

    def _build_sidebar(self):
        """Build the sidebar pane with header and option cards."""
        toolbar = Adw.ToolbarView()

        # Sidebar header — title centered, no window buttons on this side
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)

        # Centered title
        title_label = Gtk.Label(label="LangForge")
        title_label.add_css_class("heading")
        header.set_title_widget(title_label)

        toolbar.add_top_bar(header)

        # Scrollable sidebar content
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(6)
        content.set_margin_bottom(12)

        # ── API Translation card ──
        self.api_group = Adw.PreferencesGroup()

        # Build API type and provider from configured settings
        self._build_api_dropdowns(self.api_group)

        content.append(self.api_group)

        # ── Options card ──
        options_group = Adw.PreferencesGroup()

        compile_row = Adw.ActionRow(title=_("Compile .mo files"))
        self.compile_switch = Gtk.Switch()
        self.compile_switch.set_valign(Gtk.Align.CENTER)
        self.compile_switch.set_active(True)
        compile_row.add_suffix(self.compile_switch)
        compile_row.set_activatable_widget(self.compile_switch)
        options_group.add(compile_row)

        retranslate_row = Adw.ActionRow(title=_("Fix context"))
        retranslate_row.set_subtitle(_("Detect and fix translations missing context"))
        self.retranslate_switch = Gtk.Switch()
        self.retranslate_switch.set_valign(Gtk.Align.CENTER)
        self.retranslate_switch.set_active(False)
        retranslate_row.add_suffix(self.retranslate_switch)
        retranslate_row.set_activatable_widget(self.retranslate_switch)
        self.tooltip_helper.add_tooltip(retranslate_row, "fix_context")
        options_group.add(retranslate_row)

        content.append(options_group)

        # ── Languages card (selectable) ──
        langs_group = Adw.PreferencesGroup()
        langs_group.set_title(_("Languages"))

        # Select/Deselect all in group header
        btn_box = Gtk.Box(spacing=6)
        btn_box.set_halign(Gtk.Align.END)

        select_all_btn = Gtk.Button(label=_("All"))
        select_all_btn.add_css_class("flat")
        select_all_btn.add_css_class("caption")
        select_all_btn.connect("clicked", self._on_select_all_langs)
        btn_box.append(select_all_btn)

        deselect_btn = Gtk.Button(label=_("None"))
        deselect_btn.add_css_class("flat")
        deselect_btn.add_css_class("caption")
        deselect_btn.connect("clicked", self._on_deselect_all_langs)
        btn_box.append(deselect_btn)

        langs_group.set_header_suffix(btn_box)

        # Expander with language checkboxes
        self._lang_expander = Adw.ExpanderRow()
        self._lang_expander.set_icon_name("preferences-desktop-locale-symbolic")

        self._lang_checks: dict[str, Gtk.CheckButton] = {}
        saved_langs = self.settings.get(
            "selected_languages", list(SUPPORTED_LANGUAGES.keys())
        )

        for code, name in SUPPORTED_LANGUAGES.items():
            row = Adw.ActionRow()
            row.set_title(name)
            row.set_subtitle(code.upper())
            check = Gtk.CheckButton()
            check.set_active(code in saved_langs)
            check.connect("toggled", self._on_lang_toggled)
            row.add_suffix(check)
            row.set_activatable_widget(check)
            self._lang_checks[code] = check
            self._lang_expander.add_row(row)

        self._update_lang_selection_title()
        langs_group.add(self._lang_expander)

        content.append(langs_group)

        # ── API Settings button ──
        adv_button = Gtk.Button(label=_("API Settings..."))
        adv_button.add_css_class("suggested-action")
        adv_button.add_css_class("pill")
        adv_button.set_halign(Gtk.Align.CENTER)
        adv_button.connect("clicked", self._on_settings_clicked)
        content.append(adv_button)

        scroll.set_child(content)
        toolbar.set_content(scroll)

        return toolbar

    # ── Content ─────────────────────────────────────────────────

    def _build_content(self):
        """Build the content pane with header, stack, and status bar."""
        toolbar = Adw.ToolbarView()

        # Content header — action buttons + window controls
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)

        # Centered translate button as title widget
        self.translate_button = Gtk.Button(label=_("Start Translation"))
        self.translate_button.add_css_class("suggested-action")
        self.translate_button.set_sensitive(False)
        self.translate_button.connect("clicked", self._on_start_translation)
        header.set_title_widget(self.translate_button)

        # Menu button (only About — API Settings is in sidebar)
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Main menu")],
        )
        menu = Gio.Menu()
        menu.append(_("Open Project") + "  Ctrl+O", "app.open-project")
        menu.append(_("Settings") + "  Ctrl+,", "app.settings")
        menu.append(_("About"), "app.about")
        menu.append(_("Quit") + "  Ctrl+Q", "app.quit")
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)

        toolbar.add_top_bar(header)

        # ── Stack (page switching) ──
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)

        self._build_drop_page()
        self._build_project_page()
        self._build_progress_page()
        self._build_welcome_page()
        self._build_success_page()

        # Drop target on stack
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_drop)
        self.stack.add_controller(drop_target)

        toolbar.set_content(self.stack)

        return toolbar

    # ── Stack Pages ─────────────────────────────────────────────

    def _build_drop_page(self):
        """Build the initial drop zone page."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_valign(Gtk.Align.CENTER)
        page.set_halign(Gtk.Align.CENTER)
        page.set_vexpand(True)
        page.set_hexpand(True)

        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        frame.add_css_class("drop-zone")
        frame.set_valign(Gtk.Align.CENTER)
        frame.set_halign(Gtk.Align.CENTER)
        frame.set_size_request(400, 250)
        # Accessibility: describe the drop zone purpose
        frame.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Project selection area. Drag a folder here or click to select.")],
        )
        # Make entire drop zone clickable (M2: Fitts's Law)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_a: self._on_select_project(None))
        frame.add_controller(click)

        icon = Gtk.Image.new_from_icon_name("folder-open-symbolic")
        icon.set_pixel_size(64)
        icon.set_opacity(0.6)
        frame.append(icon)

        self.drop_title = Gtk.Label(label=_("No project selected"))
        self.drop_title.add_css_class("title-2")
        frame.append(self.drop_title)

        self.drop_subtitle = Gtk.Label(
            label=_("Drag a folder or file here, or click to select")
        )
        self.drop_subtitle.add_css_class("dim-label")
        frame.append(self.drop_subtitle)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.CENTER)

        select_proj_btn = Gtk.Button(label=_("Select Project"))
        select_proj_btn.add_css_class("suggested-action")
        select_proj_btn.add_css_class("pill")
        self.tooltip_helper.add_tooltip(select_proj_btn, "select_project")
        select_proj_btn.connect("clicked", self._on_select_project)
        btn_box.append(select_proj_btn)

        select_file_btn = Gtk.Button(label=_("Select File"))
        select_file_btn.add_css_class("pill")
        self.tooltip_helper.add_tooltip(select_file_btn, "select_file")
        select_file_btn.connect("clicked", self._on_select_file)
        btn_box.append(select_file_btn)

        frame.append(btn_box)

        page.append(frame)
        self.stack.add_named(page, "drop")

    def _build_project_page(self):
        """Build the project-loaded info page."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_valign(Gtk.Align.CENTER)
        page.set_halign(Gtk.Align.CENTER)
        page.set_spacing(16)

        self.project_icon = Gtk.Image.new_from_icon_name("folder-symbolic")
        self.project_icon.set_pixel_size(64)
        self.project_icon.set_opacity(0.5)
        page.append(self.project_icon)

        self.project_name_label = Gtk.Label()
        self.project_name_label.add_css_class("title-2")
        page.append(self.project_name_label)

        self.project_info_label = Gtk.Label()
        self.project_info_label.add_css_class("dim-label")
        page.append(self.project_info_label)

        change_btn = Gtk.Button(label=_("Change"))
        change_btn.set_halign(Gtk.Align.CENTER)
        change_btn.connect("clicked", self._on_select_project)
        page.append(change_btn)

        self.stack.add_named(page, "project")

    def _build_progress_page(self):
        """Build the translation progress page with embedded live viewer."""
        # Vertical paned: progress info on top, live viewer on bottom
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)
        paned.set_resize_start_child(False)
        paned.set_resize_end_child(True)

        # ── Top: progress info ──
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top.set_valign(Gtk.Align.CENTER)
        top.set_halign(Gtk.Align.CENTER)
        top.set_spacing(12)
        top.set_margin_top(16)
        top.set_margin_bottom(8)

        self.progress_ring = ProgressRing()
        top.append(self.progress_ring)

        self.progress_title = Gtk.Label(label=_("Translating..."))
        self.progress_title.add_css_class("title-3")
        self.progress_title.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Translation status")],
        )
        top.append(self.progress_title)

        self.progress_subtitle = Gtk.Label(label=_("Preparing..."))
        self.progress_subtitle.add_css_class("dim-label")
        self.progress_subtitle.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Translation details")],
        )
        top.append(self.progress_subtitle)

        # Language grid
        self.lang_grid = Gtk.FlowBox()
        self.lang_grid.set_max_children_per_line(12)
        self.lang_grid.set_min_children_per_line(6)
        self.lang_grid.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.lang_grid.set_homogeneous(True)
        self.lang_grid.set_margin_top(8)
        self.lang_grid.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Language translation status")],
        )
        self._populate_lang_grid()
        top.append(self.lang_grid)

        # Cancel button
        self.cancel_button = Gtk.Button(label=_("Cancel Translation"))
        self.cancel_button.add_css_class("destructive-action")
        self.cancel_button.add_css_class("pill")
        self.cancel_button.set_halign(Gtk.Align.CENTER)
        self.cancel_button.set_margin_top(8)
        self.cancel_button.connect("clicked", self._on_cancel_translation)
        top.append(self.cancel_button)

        paned.set_start_child(top)

        # ── Bottom: embedded live viewer ──
        self._viewer = TranslationViewer()
        paned.set_end_child(self._viewer)

        # Give viewer ~40% of space initially
        paned.set_position(300)

        self.stack.add_named(paned, "progress")

    def _build_welcome_page(self):
        """Build the first-run welcome page (M1)."""
        page = Adw.StatusPage()
        page.set_icon_name("langforge")
        page.set_title(_("Welcome to LangForge"))
        page.set_description(
            _(
                "Automate gettext translations for your project.\n"
                "To get started, configure an API provider in Settings."
            )
        )
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)

        settings_btn = Gtk.Button(label=_("Open Settings"))
        settings_btn.add_css_class("suggested-action")
        settings_btn.add_css_class("pill")
        settings_btn.connect("clicked", self._on_welcome_settings)
        btn_box.append(settings_btn)

        skip_btn = Gtk.Button(label=_("Skip"))
        skip_btn.add_css_class("pill")
        skip_btn.connect(
            "clicked", lambda _b: self.stack.set_visible_child_name("drop")
        )
        btn_box.append(skip_btn)

        page.set_child(btn_box)
        self.stack.add_named(page, "welcome")

    def _show_first_run_welcome(self):
        """Show welcome page on first run."""
        self.stack.set_visible_child_name("welcome")

    def _on_welcome_settings(self, button):
        """Open settings from welcome page, then go to drop page."""
        dialog = SettingsDialog(self, self.settings)
        dialog.connect("close-request", self._on_welcome_settings_closed)
        dialog.present()

    def _on_welcome_settings_closed(self, dialog):
        """After first-run settings close, go to drop page."""
        self.settings = Settings()
        self._refresh_api_dropdowns()
        self.stack.set_visible_child_name("drop")
        return False

    def _build_success_page(self):
        """Build the translation success page (U5: Peak-End Rule)."""
        self.success_page = Adw.StatusPage()
        self.success_page.set_icon_name("emblem-ok-symbolic")
        self.success_page.set_title(_("Translation Complete!"))
        self.success_page.set_description("")

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)

        translate_again_btn = Gtk.Button(label=_("Translate Again"))
        translate_again_btn.add_css_class("suggested-action")
        translate_again_btn.add_css_class("pill")
        translate_again_btn.connect(
            "clicked", lambda _b: self.stack.set_visible_child_name("project")
        )
        btn_box.append(translate_again_btn)

        new_project_btn = Gtk.Button(label=_("New Project"))
        new_project_btn.add_css_class("pill")
        new_project_btn.connect("clicked", lambda _b: self._on_select_project(None))
        btn_box.append(new_project_btn)

        self.success_page.set_child(btn_box)
        self.stack.add_named(self.success_page, "success")

    def _show_success_page(self, success_count: int, elapsed_secs: float):
        """Show the success celebration page."""
        mins, secs = divmod(int(elapsed_secs), 60)
        time_str = f"{mins}m{secs:02d}s" if mins else f"{secs}s"

        # Build description with optional cost info
        desc = _("{langs} languages translated in {time}").format(
            langs=success_count, time=time_str
        )
        usage = getattr(self.controller, "_last_usage", {})
        cost = usage.get("cost_usd", 0)
        if cost > 0:
            total_tokens = usage.get("total_tokens", 0)
            calls = usage.get("api_calls", 0)
            if total_tokens >= 1_000_000:
                token_str = f"{total_tokens / 1_000_000:.1f}M"
            elif total_tokens >= 1_000:
                token_str = f"{total_tokens / 1_000:.0f}K"
            else:
                token_str = str(total_tokens)
            desc += f"\n💰 ${cost:.4f} · {token_str} tokens · {calls} API calls"

        self.success_page.set_description(desc)
        self.stack.set_visible_child_name("success")

    # ── Language Grid ───────────────────────────────────────────

    def _populate_lang_grid(self, languages: list[str] | None = None):
        """Populate language grid with selected languages."""
        # Clear existing children
        while True:
            child = self.lang_grid.get_first_child()
            if child is None:
                break
            self.lang_grid.remove(child)

        lang_list = languages if languages else list(SUPPORTED_LANGUAGES.keys())
        self.lang_widgets = {}
        for code in lang_list:
            lang_name = SUPPORTED_LANGUAGES[code]
            item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            item.add_css_class("lang-item")
            item.add_css_class("pending")
            # Accessibility: describe language and initial status
            item.update_property(
                [Gtk.AccessibleProperty.LABEL],
                [f"{lang_name}: {_('pending')}"],
            )

            lbl = Gtk.Label(label=code.upper()[:2])
            lbl.add_css_class("caption")
            item.append(lbl)

            icon = Gtk.Image.new_from_icon_name("content-loading-symbolic")
            icon.set_pixel_size(12)
            item.append(icon)

            item.status_icon = icon
            item.set_tooltip_text(lang_name)
            self.lang_grid.append(item)
            self.lang_widgets[code] = item

    def _update_lang_status(self, code: str, status: str):
        if code not in self.lang_widgets:
            return
        w = self.lang_widgets[code]
        for c in ["pending", "translating", "success", "error", "reference"]:
            w.remove_css_class(c)
        w.add_css_class(status)
        icons = {
            "pending": "content-loading-symbolic",
            "translating": "emblem-synchronizing-symbolic",
            "success": "emblem-ok-symbolic",
            "error": "dialog-error-symbolic",
            "reference": "starred-symbolic",
        }
        w.status_icon.set_from_icon_name(icons.get(status, "content-loading-symbolic"))
        # Update accessible label so Orca announces the new status
        lang_name = SUPPORTED_LANGUAGES.get(code, code)
        status_labels = {
            "pending": _("pending"),
            "translating": _("translating"),
            "success": _("completed"),
            "error": _("error"),
            "reference": _("reference"),
        }
        w.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"{lang_name}: {status_labels.get(status, status)}"],
        )

    # ── API Dropdowns ───────────────────────────────────────────

    def _build_api_dropdowns(self, api_group):
        """Build API type and provider dropdowns reflecting saved config."""
        self._configured_free_providers = []  # list of (label, provider_key)
        self._configured_paid_providers = []  # list of (label, provider_key)

        # Providers that do not require an API key
        _NO_KEY_REQUIRED = {"libretranslate"}

        free_provider_labels = {
            "deepl-free": "DeepL Free",
            "groq": "Groq",
            "gemini-free": "Gemini Free",
            "openrouter": "OpenRouter",
            "mistral-free": "Mistral",
            "libretranslate": "LibreTranslate",
        }

        # Free: show providers with configured key + those that need no key
        for key, label in free_provider_labels.items():
            if key in _NO_KEY_REQUIRED:
                self._configured_free_providers.append((label, key))
            elif self.settings.get_provider_key("free_api", key):
                self._configured_free_providers.append((label, key))

        # Paid: show ONLY providers with configured key
        paid_provider_labels = {
            "openai": "OpenAI",
            "gemini": "Gemini",
            "grok": "Grok (xAI)",
        }

        for key, label in paid_provider_labels.items():
            if self.settings.get_provider_key("paid_api", key):
                self._configured_paid_providers.append((label, key))

        # Always show both API types
        configured_types = [(_("Free"), "free"), (_("Paid"), "paid")]

        # Ensure at least one free provider (LibreTranslate needs no key)
        if not self._configured_free_providers:
            self._configured_free_providers.append(("LibreTranslate", "libretranslate"))

        self._configured_types = configured_types

        # API Type row
        self.api_type_row = Adw.ComboRow(title=_("API Type"))
        type_labels = [t[0] for t in configured_types]
        self.api_type_row.set_model(Gtk.StringList.new(type_labels))
        self.api_type_row.connect("notify::selected", self._on_sidebar_api_type_changed)
        _apply_row_tooltip(
            self.api_type_row,
            self.tooltip_helper.tooltips.get("api_type", ""),
        )
        api_group.add(self.api_type_row)

        # Provider row
        self.api_provider_row = Adw.ComboRow(title=_("Provider"))
        _apply_row_tooltip(
            self.api_provider_row,
            self.tooltip_helper.tooltips.get("api_provider", ""),
        )
        api_group.add(self.api_provider_row)

        # Set initial selection based on saved settings
        saved_type = self.settings.get_api_type()
        type_idx = 0
        for i, (_lbl, key) in enumerate(configured_types):
            if key == saved_type:
                type_idx = i
                break
        self.api_type_row.set_selected(type_idx)
        self._update_sidebar_providers()

    def _on_sidebar_api_type_changed(self, combo, pspec):
        """Update provider list when API type changes in sidebar."""
        self._update_sidebar_providers()

    def _update_sidebar_providers(self):
        """Update provider dropdown based on selected API type."""
        idx = self.api_type_row.get_selected()
        if idx >= len(self._configured_types):
            return
        _lbl, type_key = self._configured_types[idx]

        if type_key == "free":
            providers = self._configured_free_providers
        else:
            providers = self._configured_paid_providers

        if providers:
            labels = [p[0] for p in providers]
            self.api_provider_row.set_model(Gtk.StringList.new(labels))
            self.api_provider_row.set_sensitive(True)

            # Select the saved provider
            if type_key == "free":
                saved = self.settings.get("free_api.provider", "")
            else:
                saved = self.settings.get("paid_api.provider", "")

            for i, (_lbl, key) in enumerate(providers):
                if key == saved:
                    self.api_provider_row.set_selected(i)
                    break
        else:
            # No configured providers — show empty dropdown
            self.api_provider_row.set_model(Gtk.StringList.new([]))
            self.api_provider_row.set_sensitive(False)

    # ── Language selection ─────────────────────────────────────

    def _update_lang_selection_title(self):
        """Update expander title with selection count."""
        count = sum(1 for c in self._lang_checks.values() if c.get_active())
        total = len(self._lang_checks)
        self._lang_expander.set_title(
            _("{count} of {total} languages selected").format(count=count, total=total)
        )

    def _on_lang_toggled(self, check):
        self._update_lang_selection_title()
        if getattr(self, "_batch_lang_update", False):
            return
        # Persist selection
        selected = [c for c, chk in self._lang_checks.items() if chk.get_active()]
        self.settings.set("selected_languages", selected)
        self.settings.save()

    def _on_select_all_langs(self, button):
        self._batch_lang_update = True
        for check in self._lang_checks.values():
            check.set_active(True)
        self._batch_lang_update = False
        selected = [c for c, chk in self._lang_checks.items() if chk.get_active()]
        self.settings.set("selected_languages", selected)
        self.settings.save()

    def _on_deselect_all_langs(self, button):
        self._batch_lang_update = True
        for check in self._lang_checks.values():
            check.set_active(False)
        self._batch_lang_update = False
        selected = [c for c, chk in self._lang_checks.items() if chk.get_active()]
        self.settings.set("selected_languages", selected)
        self.settings.save()

    def get_selected_languages(self) -> list[str]:
        """Return list of language codes the user has selected."""
        return [c for c, chk in self._lang_checks.items() if chk.get_active()]

    # ── Callbacks ───────────────────────────────────────────────

    def _on_settings_clicked(self, button):
        dialog = SettingsDialog(self, self.settings)
        dialog.connect("close-request", self._on_settings_closed)
        dialog.present()

    def _on_settings_closed(self, dialog):
        """Refresh sidebar dropdowns after settings dialog closes."""
        # Reload settings from disk
        self.settings = Settings()
        self._refresh_api_dropdowns()
        return False

    def _refresh_api_dropdowns(self):
        """Rebuild API type and provider dropdowns with current settings."""
        # Remove old rows
        if hasattr(self, "api_type_row"):
            self.api_group.remove(self.api_type_row)
        if hasattr(self, "api_provider_row"):
            self.api_group.remove(self.api_provider_row)
        # Rebuild
        self._build_api_dropdowns(self.api_group)

    def _on_select_project(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_modal(True)
        last_dir = self.settings.get("last_project_dir", "")
        if last_dir:
            p = Path(last_dir)
            if p.exists():
                dialog.set_initial_folder(Gio.File.new_for_path(str(p)))
        dialog.select_folder(self, None, self._on_folder_selected)

    def _on_select_file(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_modal(True)
        last_dir = self.settings.get("last_file_dir", "")
        if last_dir:
            p = Path(last_dir)
            if p.exists():
                dialog.set_initial_folder(Gio.File.new_for_path(str(p)))
        # Filter to supported file types
        ff = Gtk.FileFilter()
        ff.set_name(_("Translatable files (.po, .json, .txt, .md, .srt)"))
        for ext in SUPPORTED_EXTENSIONS:
            ff.add_pattern(f"*{ext}")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(ff)
        dialog.set_filters(filters)
        dialog.open(self, None, self._on_file_selected)

    def _on_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                path = folder.get_path()
                self.settings.set("last_project_dir", str(Path(path).parent))
                self.settings.save()
                self._validate_and_set_project(path)
        except Exception as e:
            self._show_toast(f"{_('Error')}: {e}")

    def _on_file_selected(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
            if gfile:
                path = gfile.get_path()
                self.settings.set("last_file_dir", str(Path(path).parent))
                self.settings.save()
                self._validate_and_set_file(path)
        except Exception as e:
            self._show_toast(f"{_('Error')}: {e}")

    def _on_drop(self, target, value, x, y):
        if isinstance(value, Gio.File):
            path = value.get_path()
            if path:
                p = Path(path)
                if p.is_dir():
                    self._validate_and_set_project(path)
                elif p.is_file():
                    self._validate_and_set_file(path)
                return True
        return False

    def _validate_and_set_project(self, path: str):
        try:
            textdomain, strings = self.controller.validate_project(path)
            self.selected_project = Path(path)
            self.selected_file = None
            self._mode = "project"
            self._string_count = strings

            self.project_icon.set_from_icon_name("folder-symbolic")
            self.project_name_label.set_label(textdomain)
            lang_count = len(self.get_selected_languages())
            self.project_info_label.set_label(
                _("{strings} strings · {langs} languages").format(
                    strings=strings, langs=lang_count
                )
            )

            self.translate_button.set_sensitive(True)
            self.compile_switch.set_sensitive(True)
            self.stack.set_visible_child_name("project")

            self._show_toast(_("Project loaded: {}").format(textdomain))

        except ValueError as e:
            self._show_toast(str(e))
        except Exception as e:
            self._show_toast(f"{_('Error')}: {_humanize_error(e)}")

    def _validate_and_set_file(self, path: str):
        try:
            filename, count = self.controller.validate_file(path)
            self.selected_file = Path(path)
            self.selected_project = None
            self._mode = "file"
            self._string_count = count

            self.project_icon.set_from_icon_name("document-edit-symbolic")
            self.project_name_label.set_label(filename)
            lang_count = len(self.get_selected_languages())
            self.project_info_label.set_label(
                _("{items} items · {langs} languages").format(
                    items=count, langs=lang_count
                )
            )

            self.translate_button.set_sensitive(True)
            self.compile_switch.set_sensitive(False)
            self.stack.set_visible_child_name("project")

            self._show_toast(_("File loaded: {}").format(filename))

        except ValueError as e:
            self._show_toast(str(e))
        except Exception as e:
            self._show_toast(f"{_('Error')}: {_humanize_error(e)}")

    def _on_start_translation(self, button):
        if self.controller.is_translating:
            return
        if not self.selected_project and not self.selected_file:
            return

        selected_langs = self.get_selected_languages()
        if not selected_langs:
            self._show_toast(_("Select at least one language"))
            return

        # M6: Confirmation dialog before starting
        strings = self._string_count

        api_type = self.settings.get_api_type()
        if api_type == "free":
            provider = self.settings.get_free_provider()
        else:
            provider = self.settings.get_paid_provider()

        # Validate that the selected API type has a configured provider
        if api_type == "paid" and not self._configured_paid_providers:
            self._show_toast(_("No paid API configured. Add an API key in Settings."))
            return

        if self._mode == "file":
            body = _(
                "Translate {items} items to {langs} languages using {provider}.\n"
                "This may take several minutes."
            ).format(
                items=strings,
                langs=len(selected_langs),
                provider=provider.capitalize(),
            )
        else:
            body = _(
                "Translate {strings} strings to {langs} languages using {provider}.\n"
                "This may take several minutes."
            ).format(
                strings=strings,
                langs=len(selected_langs),
                provider=provider.capitalize(),
            )

        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Start Translation?"))
        dialog.set_body(body)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("start", _("Start"))
        dialog.set_response_appearance("start", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("start")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_confirm_translation)
        dialog.present(self)

    def _on_confirm_translation(self, dialog, response):
        """Handle confirmation dialog response."""
        if response != "start":
            return
        self._begin_translation()

    def _begin_translation(self):
        """Actually start the translation process after confirmation."""
        # A4: Validate API client before starting translation thread
        try:
            self.controller.prepare()
        except Exception as e:
            self._show_toast(f"{_('Error')}: {_humanize_error(e)}")
            return

        import time

        selected_langs = self.get_selected_languages()
        self._active_langs = selected_langs  # for progress tracking

        self._translation_start = time.monotonic()
        self.translate_button.set_sensitive(False)
        self.translate_button.set_label(_("Translating..."))
        self.cancel_button.set_sensitive(True)

        # Clear the embedded viewer for the new translation run
        self._viewer.clear()

        self.stack.set_visible_child_name("progress")
        self.progress_ring.set_progress(0)

        # Rebuild lang grid with only selected languages
        self._populate_lang_grid(selected_langs)

        if selected_langs:
            self._update_lang_status(selected_langs[0], "translating")

        callbacks = dict(
            on_phase=lambda phase: GLib.idle_add(self._on_phase, phase),
            on_lang_progress=lambda *a: GLib.idle_add(self._on_lang_progress, *a),
            on_complete=lambda *a: GLib.idle_add(self._on_translation_complete, *a),
            on_error=lambda e: GLib.idle_add(self._on_translation_error, e),
        )

        if self._mode == "file" and self.selected_file:
            self.controller.start_file(
                self.selected_file,
                languages=selected_langs,
                on_detail=lambda lang, pairs: GLib.idle_add(
                    self._on_detail, lang, pairs
                ),
                **callbacks,
            )
        else:
            self.controller.start(
                self.selected_project,
                languages=selected_langs,
                compile_mo=self.compile_switch.get_active(),
                force_retranslate=self.retranslate_switch.get_active(),
                on_detail=lambda lang, pairs: GLib.idle_add(
                    self._on_detail, lang, pairs
                ),
                **callbacks,
            )

    # ── Controller callbacks (invoked via GLib.idle_add) ────────

    def _on_phase(self, phase: str):
        """Update UI when a pipeline phase starts."""
        labels = {
            "extracting": _("Extracting strings..."),
            "translating": _("Translating..."),
            "compiling": _("Compiling..."),
            "checking context": _("Checking context-aware translations..."),
        }
        label = labels.get(phase, phase)
        self.progress_subtitle.set_label(label)

    def _on_lang_progress(self, lang: str, status: str, current: int, total: int):
        """Update UI per-language progress (M3: ETA display)."""
        import time

        name = SUPPORTED_LANGUAGES.get(lang, lang)

        # Phase 1 of fix_context: checking individual entries, not languages
        if status.startswith("checking context"):
            self.progress_subtitle.set_label(status)
            if total > 0:
                self.progress_ring.set_progress(current / total)
            return

        # Already-fixed langs from resume: mark success but skip ETA calc
        if "already fixed" in status:
            self._update_lang_status(lang, "success")
            if total > 0:
                self.progress_ring.set_progress(current / total)
            self.progress_subtitle.set_label(
                _("Resuming… {current}/{total} languages already done").format(
                    current=current, total=total
                )
            )
            # Track resumed offset so ETA calc discounts them
            self._resumed_offset = current
            self._resumed_time = time.monotonic()
            return

        # In-progress string-level feedback during fix_context Phase 2
        if status.startswith("translating:"):
            # Clear spinner from previous lang if still marked translating
            prev = getattr(self, "_translating_lang", None)
            if prev and prev != lang:
                self._update_lang_status(prev, "pending")
            self._translating_lang = lang
            self._update_lang_status(lang, "translating")
            detail = f"{name}: {status.removeprefix('translating:').strip()}"
            self.progress_subtitle.set_label(detail)
            # Parse sub-progress from "translating: 435/582 subtitles"
            if total > 0:
                sub_fraction = 0.5
                import re as _re

                m = _re.search(r"(\d+)/(\d+)", status)
                if m:
                    sub_done, sub_total = int(m.group(1)), int(m.group(2))
                    if sub_total > 0:
                        sub_fraction = sub_done / sub_total
                self.progress_ring.set_progress((current - 1 + sub_fraction) / total)
            return

        if "error" in status.lower():
            self._update_lang_status(lang, "error")
        elif "reference" in status.lower():
            self._update_lang_status(lang, "reference")
        else:
            self._update_lang_status(lang, "success")

        # Clear translating tracker so next "translating:" callback starts clean
        if getattr(self, "_translating_lang", None) == lang:
            self._translating_lang = None

        elapsed = time.monotonic() - getattr(
            self, "_resumed_time", self._translation_start
        )
        offset = getattr(self, "_resumed_offset", 0)
        done_since_resume = current - offset
        if done_since_resume > 0:
            avg_per_lang = elapsed / done_since_resume
            remaining = avg_per_lang * (total - current)
            mins, secs = divmod(int(remaining), 60)
            eta_str = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
            detail = _("{name} ({current}/{total}) · ~{eta} remaining").format(
                name=name,
                current=current,
                total=total,
                eta=eta_str,
            )
        else:
            detail = f"{name}..."
        self.progress_subtitle.set_label(detail)
        self.progress_ring.set_progress(current / total)

    def _on_translation_complete(
        self, results: dict, elapsed: float, was_cancelled: bool
    ):
        """Handle translation pipeline completion."""
        success = sum(1 for v in results.values() if v)
        failed = sum(1 for v in results.values() if not v)
        mins, secs = divmod(int(elapsed), 60)
        elapsed_str = f"{mins}m{secs:02d}s" if mins else f"{secs}s"

        if was_cancelled:
            # Cancel UI already shown by _on_cancel_translation; just update
            # with the final cost which may be more accurate.
            usage = getattr(self.controller, "_last_usage", {})
            cost = usage.get("cost_usd", 0)
            if cost > 0:
                cancel_msg = _("{} languages translated before cancellation").format(
                    success
                )
                cancel_msg += f"\n💰 ${cost:.4f}"
                self.progress_subtitle.set_label(cancel_msg)
        else:
            self.progress_ring.set_progress(1.0)
            self._show_success_page(success, elapsed)
        self._finish_translation()

    def _on_translation_error(self, error: Exception):
        """Handle translation pipeline failure."""
        self.progress_title.set_label(_("Error"))
        self.progress_subtitle.set_label(_humanize_error(error))
        self._show_toast(f"{_('Error')}: {_humanize_error(error)}")
        self._finish_translation()

    def _on_cancel_translation(self, button):
        """Signal the translation thread to stop and show cancelled state."""
        self.controller.cancel()
        self.cancel_button.set_sensitive(False)
        self.progress_title.set_label(_("Cancelled"))

        # Count languages already marked as success in the grid
        success_count = 0
        for code, w in self.lang_widgets.items():
            if w.has_css_class("success"):
                success_count += 1

        cancel_msg = _("{} languages translated before cancellation").format(
            success_count
        )

        # Snapshot cost from the live API client
        api = getattr(self.controller, "_api_client", None)
        if api:
            usage = api.get_usage()
            cost = usage.get("cost_usd", 0)
            if cost > 0:
                cancel_msg += f"\n💰 ${cost:.4f}"

        self.progress_subtitle.set_label(cancel_msg)
        self.progress_ring.set_progress(0.0)
        self._finish_translation()

    def _finish_translation(self):
        self.translate_button.set_sensitive(True)
        self.translate_button.set_label(_("Start Translation"))
        self.cancel_button.set_sensitive(False)

    def _on_detail(self, lang: str, pairs: list[tuple[str, str, str]]):
        """Receive translation detail pairs from worker thread (via idle_add)."""
        # Detect language switch
        viewer_lang = getattr(self, "_viewer_current_lang", None)
        if viewer_lang != lang:
            self._viewer_current_lang = lang
            name = SUPPORTED_LANGUAGES.get(lang, lang)
            self._viewer.set_language(lang, name)
        self._viewer.add_batch(pairs)

    def _show_toast(self, msg: str):
        toast = Adw.Toast.new(msg)
        toast.set_timeout(3)
        self.toast_overlay.add_toast(toast)
