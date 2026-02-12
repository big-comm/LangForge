"""Main application window - Modern Adwaita Style."""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gdk, Gio
import threading
import math
from pathlib import Path

from config.settings import Settings
from core.scanner import ProjectScanner
from core.extractor import GettextExtractor
from core.translator import TranslationEngine
from core.compiler import MoCompiler
from core.languages import SUPPORTED_LANGUAGES
from api.factory import APIFactory
from ui.settings_dialog import SettingsDialog
from utils.i18n import _


class ProgressRing(Gtk.DrawingArea):
    """Circular progress widget."""

    def __init__(self):
        super().__init__()
        self._progress = 0.0
        self.set_size_request(80, 80)
        self.set_draw_func(self._draw)

    def _draw(self, area, cr, width, height):
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 6
        line_width = 6

        cr.set_line_width(line_width)
        cr.set_source_rgba(0.3, 0.3, 0.3, 0.5)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        if self._progress > 0:
            cr.set_line_cap(1)
            cr.set_source_rgba(0.35, 0.55, 0.95, 1.0)
            start = -math.pi / 2
            end = start + (2 * math.pi * self._progress)
            cr.arc(cx, cy, radius, start, end)
            cr.stroke()

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


class MainWindow(Adw.ApplicationWindow):
    """Main window - Modern Adwaita Split View."""

    def __init__(self, app):
        super().__init__(application=app)
        self.settings = Settings()
        self.selected_project = None
        self.is_translating = False

        self.set_title("LangForge")
        self.set_default_size(900, 600)

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
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
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

        # Start on drop page
        self.stack.set_visible_child_name("drop")

    # ── Sidebar ─────────────────────────────────────────────────

    def _build_sidebar(self):
        """Build the sidebar pane with header and option cards."""
        toolbar = Adw.ToolbarView()

        # Sidebar header — icon + title centered, no window buttons on this side
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)

        # App icon on the left
        app_icon = Gtk.Image.new_from_icon_name("langforge")
        app_icon.set_pixel_size(20)
        header.pack_start(app_icon)

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

        content.append(options_group)

        # ── Languages card ──
        langs_group = Adw.PreferencesGroup()

        langs_row = Adw.ActionRow(
            title=_("{} languages supported").format(len(SUPPORTED_LANGUAGES)),
            icon_name="preferences-desktop-locale-symbolic"
        )
        langs_group.add(langs_row)

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
        menu = Gio.Menu()
        menu.append(_("About"), "app.about")
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

        icon = Gtk.Image.new_from_icon_name("folder-open-symbolic")
        icon.set_pixel_size(64)
        icon.set_opacity(0.6)
        frame.append(icon)

        self.drop_title = Gtk.Label(label=_("No project selected"))
        self.drop_title.add_css_class("title-2")
        frame.append(self.drop_title)

        self.drop_subtitle = Gtk.Label(
            label=_("Drag a folder here or click to select")
        )
        self.drop_subtitle.add_css_class("dim-label")
        frame.append(self.drop_subtitle)

        select_btn = Gtk.Button(label=_("Select Project"))
        select_btn.add_css_class("suggested-action")
        select_btn.add_css_class("pill")
        select_btn.set_halign(Gtk.Align.CENTER)
        select_btn.connect("clicked", self._on_select_project)
        frame.append(select_btn)

        page.append(frame)
        self.stack.add_named(page, "drop")

    def _build_project_page(self):
        """Build the project-loaded info page."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_valign(Gtk.Align.CENTER)
        page.set_halign(Gtk.Align.CENTER)
        page.set_spacing(16)

        icon = Gtk.Image.new_from_icon_name("folder-symbolic")
        icon.set_pixel_size(64)
        icon.set_opacity(0.5)
        page.append(icon)

        self.project_name_label = Gtk.Label()
        self.project_name_label.add_css_class("title-2")
        page.append(self.project_name_label)

        self.project_info_label = Gtk.Label()
        self.project_info_label.add_css_class("dim-label")
        page.append(self.project_info_label)

        change_btn = Gtk.Button(label=_("Change Project"))
        change_btn.set_halign(Gtk.Align.CENTER)
        change_btn.connect("clicked", self._on_select_project)
        page.append(change_btn)

        self.stack.add_named(page, "project")

    def _build_progress_page(self):
        """Build the translation progress page."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_valign(Gtk.Align.CENTER)
        page.set_halign(Gtk.Align.CENTER)
        page.set_spacing(20)

        self.progress_ring = ProgressRing()
        page.append(self.progress_ring)

        self.progress_title = Gtk.Label(label=_("Translating..."))
        self.progress_title.add_css_class("title-3")
        page.append(self.progress_title)

        self.progress_subtitle = Gtk.Label(label=_("Preparing..."))
        self.progress_subtitle.add_css_class("dim-label")
        page.append(self.progress_subtitle)

        # Language grid
        self.lang_grid = Gtk.FlowBox()
        self.lang_grid.set_max_children_per_line(12)
        self.lang_grid.set_min_children_per_line(6)
        self.lang_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.lang_grid.set_homogeneous(True)
        self.lang_grid.set_margin_top(16)
        self._populate_lang_grid()
        page.append(self.lang_grid)

        self.stack.add_named(page, "progress")

    # ── Language Grid ───────────────────────────────────────────

    def _populate_lang_grid(self):
        """Populate language grid."""
        self.lang_widgets = {}
        for code in SUPPORTED_LANGUAGES:
            item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            item.add_css_class("lang-item")
            item.add_css_class("pending")

            lbl = Gtk.Label(label=code.upper()[:2])
            lbl.add_css_class("caption")
            item.append(lbl)

            icon = Gtk.Image.new_from_icon_name("content-loading-symbolic")
            icon.set_pixel_size(12)
            item.append(icon)

            item.status_icon = icon
            self.lang_grid.append(item)
            self.lang_widgets[code] = item

    def _update_lang_status(self, code: str, status: str):
        if code not in self.lang_widgets:
            return
        w = self.lang_widgets[code]
        for c in ['pending', 'translating', 'success', 'error']:
            w.remove_css_class(c)
        w.add_css_class(status)
        icons = {
            'pending': 'content-loading-symbolic',
            'translating': 'emblem-synchronizing-symbolic',
            'success': 'emblem-ok-symbolic',
            'error': 'dialog-error-symbolic'
        }
        w.status_icon.set_from_icon_name(
            icons.get(status, 'content-loading-symbolic')
        )

    # ── API Dropdowns ───────────────────────────────────────────

    def _build_api_dropdowns(self, api_group):
        """Build API type and provider dropdowns showing only configured options."""
        # Determine which API types have configured providers
        configured_types = []  # list of (label, type_key)
        self._configured_free_providers = []  # list of (label, provider_key)
        self._configured_paid_providers = []  # list of (label, provider_key)

        # Free providers: check which have API keys or are LibreTranslate
        free_provider_labels = {
            "deepl-free": "DeepL Free",
            "groq": "Groq",
            "gemini-free": "Gemini Free",
            "openrouter": "OpenRouter",
            "mistral-free": "Mistral",
            "libretranslate": "LibreTranslate",
        }
        free_api_key = self.settings.get("free_api.api_key", "")
        free_provider = self.settings.get("free_api.provider", "")

        for key, label in free_provider_labels.items():
            if key == "libretranslate":
                # LibreTranslate doesn't need API key — always available
                self._configured_free_providers.append((label, key))
            elif key == free_provider and free_api_key:
                # Only show the provider that is actually configured with a key
                self._configured_free_providers.append((label, key))

        # Paid providers: check if API key is set
        paid_provider_labels = {
            "openai": "OpenAI",
            "gemini": "Gemini",
            "grok": "Grok (xAI)",
        }
        paid_api_key = self.settings.get("paid_api.api_key", "")
        paid_provider = self.settings.get("paid_api.provider", "")

        for key, label in paid_provider_labels.items():
            if key == paid_provider and paid_api_key:
                self._configured_paid_providers.append((label, key))

        # Build API type dropdown with only configured types
        if self._configured_free_providers:
            configured_types.append((_("Free"), "free"))
        if self._configured_paid_providers:
            configured_types.append((_("Paid"), "paid"))

        # Fallback: if nothing configured, show free with LibreTranslate
        if not configured_types:
            configured_types.append((_("Free"), "free"))
            if not self._configured_free_providers:
                self._configured_free_providers.append(("LibreTranslate", "libretranslate"))

        self._configured_types = configured_types

        # API Type row
        self.api_type_row = Adw.ComboRow(title=_("API Type"))
        type_labels = [t[0] for t in configured_types]
        self.api_type_row.set_model(Gtk.StringList.new(type_labels))
        self.api_type_row.connect("notify::selected", self._on_sidebar_api_type_changed)
        api_group.add(self.api_type_row)

        # Provider row
        self.api_provider_row = Adw.ComboRow(title=_("Provider"))
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

        labels = [p[0] for p in providers]
        self.api_provider_row.set_model(Gtk.StringList.new(labels))

        # Select the saved provider
        if type_key == "free":
            saved = self.settings.get("free_api.provider", "")
        else:
            saved = self.settings.get("paid_api.provider", "")

        for i, (_lbl, key) in enumerate(providers):
            if key == saved:
                self.api_provider_row.set_selected(i)
                break

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
        if hasattr(self, 'api_type_row'):
            self.api_group.remove(self.api_type_row)
        if hasattr(self, 'api_provider_row'):
            self.api_group.remove(self.api_provider_row)
        # Rebuild
        self._build_api_dropdowns(self.api_group)

    def _on_select_project(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_modal(True)
        dialog.select_folder(self, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                self._validate_and_set_project(folder.get_path())
        except Exception as e:
            self._show_toast(f"{_('Error')}: {e}")

    def _on_drop(self, target, value, x, y):
        if isinstance(value, Gio.File):
            path = value.get_path()
            if path:
                self._validate_and_set_project(path)
                return True
        return False

    def _validate_and_set_project(self, path: str):
        try:
            scanner = ProjectScanner(path)
            if not scanner.validate_project():
                self._show_toast(_("Project does not use gettext"))
                return

            self.selected_project = Path(path)
            textdomain = scanner.detect_textdomain()
            strings = scanner.count_translatable_strings()

            self.project_name_label.set_label(textdomain)
            self.project_info_label.set_label(
                _("{} strings · 29 languages").format(strings)
            )

            self.translate_button.set_sensitive(True)
            self.stack.set_visible_child_name("project")

            self._show_toast(
                _("Project loaded: {}").format(textdomain)
            )

        except Exception as e:
            self._show_toast(f"{_('Error')}: {str(e)}")

    def _on_start_translation(self, button):
        if not self.selected_project or self.is_translating:
            return

        self.is_translating = True
        self.translate_button.set_sensitive(False)
        self.translate_button.set_label(_("Translating..."))

        self.stack.set_visible_child_name("progress")
        self.progress_ring.set_progress(0)

        for code in SUPPORTED_LANGUAGES:
            self._update_lang_status(code, 'pending')

        thread = threading.Thread(target=self._run_translation)
        thread.daemon = True
        thread.start()

    def _run_translation(self):
        try:

            GLib.idle_add(
                self.progress_subtitle.set_label, _("Extracting strings...")
            )

            scanner = ProjectScanner(str(self.selected_project))
            textdomain = scanner.detect_textdomain()
            files = scanner.find_python_files()

            extractor = GettextExtractor(
                str(self.selected_project), textdomain
            )
            extractor.extract_strings(files)
            count = extractor.get_string_count()



            api = APIFactory.create_from_settings(self.settings)
            translator = TranslationEngine(api, textdomain)

            def callback(lang, status, current, total):
                name = SUPPORTED_LANGUAGES.get(lang, lang)
                if "error" in status.lower():
                    GLib.idle_add(
                        self._update_lang_status, lang, 'error'
                    )
                else:
                    GLib.idle_add(
                        self._update_lang_status, lang, 'success'
                    )

                langs = list(SUPPORTED_LANGUAGES.keys())
                if current < total:
                    next_l = langs[current]
                    GLib.idle_add(
                        self._update_lang_status, next_l, 'translating'
                    )
                    GLib.idle_add(
                        self.progress_subtitle.set_label, f"{name}..."
                    )

                GLib.idle_add(
                    self.progress_ring.set_progress, current / total
                )

            first = list(SUPPORTED_LANGUAGES.keys())[0]
            GLib.idle_add(self._update_lang_status, first, 'translating')

            results = translator.translate_project(
                extractor.pot_file, self.selected_project, callback
            )
            success = sum(1 for v in results.values() if v)

            # Compile .mo files if enabled
            if self.compile_switch.get_active():
                GLib.idle_add(
                    self.progress_subtitle.set_label, _("Compiling...")
                )
                compiler = MoCompiler(self.selected_project, textdomain)
                compiler.compile_all()

            GLib.idle_add(
                self.progress_title.set_label, _("Completed!")
            )
            GLib.idle_add(
                self.progress_subtitle.set_label,
                _("{} languages translated").format(success)
            )
            GLib.idle_add(self.progress_ring.set_progress, 1.0)
            GLib.idle_add(
                self._show_toast,
                _("Translation complete! {} languages").format(success)
            )

        except Exception as e:
            GLib.idle_add(
                self.progress_title.set_label, _("Error")
            )
            GLib.idle_add(
                self.progress_subtitle.set_label, str(e)
            )

            GLib.idle_add(
                self._show_toast, f"{_('Error')}: {e}"
            )

        finally:
            GLib.idle_add(self._finish_translation)

    def _finish_translation(self):
        self.is_translating = False
        self.translate_button.set_sensitive(True)
        self.translate_button.set_label(_("Start Translation"))

    def _show_toast(self, msg: str):
        toast = Adw.Toast.new(msg)
        toast.set_timeout(3)
        self.toast_overlay.add_toast(toast)
