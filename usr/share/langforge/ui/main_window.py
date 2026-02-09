"""Main application window - BigLinux Style."""

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
    """Main window - BigLinux Style."""

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
        # Main container
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(main_box)

        # === Header Bar ===
        header = Adw.HeaderBar()

        # Menu (first pack_end = rightmost)
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu = Gio.Menu()
        menu.append(_("API Settings"), "app.settings")
        menu.append(_("About"), "app.about")
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)

        # Main button (second pack_end = before menu)
        self.translate_button = Gtk.Button(label=_("Start Translation"))
        self.translate_button.add_css_class("suggested-action")
        self.translate_button.set_sensitive(False)
        self.translate_button.connect("clicked", self._on_start_translation)
        header.pack_end(self.translate_button)

        main_box.append(header)

        # === Content: Sidebar + Main Area ===
        content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        content_box.set_vexpand(True)
        main_box.append(content_box)

        # --- Left Sidebar ---
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar.set_size_request(280, -1)
        sidebar.add_css_class("sidebar")

        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_vexpand(True)

        sidebar_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar_scroll.set_child(sidebar_content)
        sidebar.append(sidebar_scroll)

        # Group: API
        api_group = self._create_sidebar_group(_("Translation API"))

        self.api_type_row = self._create_combo_row(
            _("Type"), [_("Free"), _("Paid")]
        )
        api_group.append(self.api_type_row)

        self.api_provider_row = self._create_combo_row(
            _("Provider"), ["DeepL Free", "Groq", "Gemini Free", "OpenRouter", "Mistral", "LibreTranslate"]
        )
        api_group.append(self.api_provider_row)

        sidebar_content.append(api_group)

        # Group: Options
        options_group = self._create_sidebar_group(_("Options"))

        compile_row = self._create_switch_row(_("Compile .mo files"))
        self.compile_switch = compile_row.switch
        self.compile_switch.set_active(True)
        options_group.append(compile_row)

        overwrite_row = self._create_switch_row(_("Overwrite existing"))
        self.overwrite_switch = overwrite_row.switch
        self.overwrite_switch.set_active(True)
        options_group.append(overwrite_row)

        sidebar_content.append(options_group)

        # Group: Languages
        langs_group = self._create_sidebar_group(_("Languages"))

        langs_info = Gtk.Label(label=_("{} languages supported").format(len(SUPPORTED_LANGUAGES)))
        langs_info.add_css_class("dim-label")
        langs_info.set_halign(Gtk.Align.START)
        langs_info.set_margin_start(12)
        langs_info.set_margin_bottom(8)
        langs_group.append(langs_info)

        sidebar_content.append(langs_group)

        # Advanced Settings Button
        adv_button = Gtk.Button(label=_("API Settings..."))
        adv_button.set_margin_start(12)
        adv_button.set_margin_end(12)
        adv_button.set_margin_top(12)
        adv_button.set_margin_bottom(12)
        adv_button.connect("clicked", self._on_settings_clicked)
        sidebar.append(adv_button)

        content_box.append(sidebar)

        # Vertical separator
        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(sep)

        # --- Main Area ---
        main_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_area.set_hexpand(True)
        main_area.set_vexpand(True)
        content_box.append(main_area)

        # Stack to switch between drop zone and progress
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        main_area.append(self.stack)

        # --- Page: Drop Zone ---
        drop_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        drop_page.set_valign(Gtk.Align.CENTER)
        drop_page.set_halign(Gtk.Align.CENTER)
        drop_page.set_vexpand(True)
        drop_page.set_hexpand(True)

        # Visual container for drop
        drop_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        drop_frame.add_css_class("drop-zone")
        drop_frame.set_valign(Gtk.Align.CENTER)
        drop_frame.set_halign(Gtk.Align.CENTER)
        drop_frame.set_size_request(400, 250)

        drop_icon = Gtk.Image.new_from_icon_name("folder-open-symbolic")
        drop_icon.set_pixel_size(64)
        drop_icon.set_opacity(0.6)
        drop_frame.append(drop_icon)

        self.drop_title = Gtk.Label(label=_("No project selected"))
        self.drop_title.add_css_class("title-2")
        drop_frame.append(self.drop_title)

        self.drop_subtitle = Gtk.Label(label=_("Drag a folder here or click to select"))
        self.drop_subtitle.add_css_class("dim-label")
        drop_frame.append(self.drop_subtitle)

        select_btn = Gtk.Button(label=_("Select Project"))
        select_btn.add_css_class("suggested-action")
        select_btn.add_css_class("pill")
        select_btn.set_halign(Gtk.Align.CENTER)
        select_btn.connect("clicked", self._on_select_project)
        drop_frame.append(select_btn)

        drop_page.append(drop_frame)
        self.stack.add_named(drop_page, "drop")

        # --- Page: Project Loaded ---
        project_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        project_page.set_valign(Gtk.Align.CENTER)
        project_page.set_halign(Gtk.Align.CENTER)
        project_page.set_spacing(16)

        project_icon = Gtk.Image.new_from_icon_name("folder-symbolic")
        project_icon.set_pixel_size(64)
        project_icon.set_opacity(0.5)
        project_page.append(project_icon)

        self.project_name_label = Gtk.Label()
        self.project_name_label.add_css_class("title-2")
        project_page.append(self.project_name_label)

        self.project_info_label = Gtk.Label()
        self.project_info_label.add_css_class("dim-label")
        project_page.append(self.project_info_label)

        change_btn = Gtk.Button(label=_("Change Project"))
        change_btn.set_halign(Gtk.Align.CENTER)
        change_btn.connect("clicked", self._on_select_project)
        project_page.append(change_btn)

        self.stack.add_named(project_page, "project")

        # --- Page: Progress ---
        progress_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        progress_page.set_valign(Gtk.Align.CENTER)
        progress_page.set_halign(Gtk.Align.CENTER)
        progress_page.set_spacing(20)

        self.progress_ring = ProgressRing()
        progress_page.append(self.progress_ring)

        self.progress_title = Gtk.Label(label=_("Translating..."))
        self.progress_title.add_css_class("title-3")
        progress_page.append(self.progress_title)

        self.progress_subtitle = Gtk.Label(label=_("Preparing..."))
        self.progress_subtitle.add_css_class("dim-label")
        progress_page.append(self.progress_subtitle)

        # Compact language grid
        self.lang_grid = Gtk.FlowBox()
        self.lang_grid.set_max_children_per_line(12)
        self.lang_grid.set_min_children_per_line(6)
        self.lang_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.lang_grid.set_homogeneous(True)
        self.lang_grid.set_margin_top(16)
        self._populate_lang_grid()
        progress_page.append(self.lang_grid)

        self.stack.add_named(progress_page, "progress")

        # Drop target
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_drop)
        main_area.add_controller(drop_target)

        # --- Bottom Bar (Status) ---
        status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        status_bar.add_css_class("statusbar")
        status_bar.set_margin_start(12)
        status_bar.set_margin_end(12)
        status_bar.set_margin_top(6)
        status_bar.set_margin_bottom(6)

        self.status_label = Gtk.Label(label=_("Ready"))
        self.status_label.add_css_class("dim-label")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_hexpand(True)
        status_bar.append(self.status_label)

        main_box.append(status_bar)

        # Start on drop page
        self.stack.set_visible_child_name("drop")

    def _create_sidebar_group(self, title: str) -> Gtk.Box:
        """Create a group in the sidebar."""
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        label = Gtk.Label(label=title)
        label.add_css_class("heading")
        label.set_halign(Gtk.Align.START)
        label.set_margin_start(12)
        label.set_margin_top(16)
        label.set_margin_bottom(8)
        group.append(label)

        return group

    def _create_combo_row(self, title: str, options: list) -> Gtk.Box:
        """Create a row with label and combo."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_start(12)
        row.set_margin_end(12)
        row.set_margin_top(4)
        row.set_margin_bottom(4)

        label = Gtk.Label(label=title)
        label.set_hexpand(True)
        label.set_halign(Gtk.Align.START)
        row.append(label)

        combo = Gtk.DropDown()
        combo.set_model(Gtk.StringList.new(options))
        combo.add_css_class("flat")
        row.append(combo)

        row.combo = combo
        return row

    def _create_switch_row(self, title: str) -> Gtk.Switch:
        """Create a row with label and switch."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_start(12)
        row.set_margin_end(12)
        row.set_margin_top(4)
        row.set_margin_bottom(4)

        label = Gtk.Label(label=title)
        label.set_hexpand(True)
        label.set_halign(Gtk.Align.START)
        row.append(label)

        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        row.append(switch)

        row.switch = switch
        return row

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
        w.status_icon.set_from_icon_name(icons.get(status, 'content-loading-symbolic'))

    def _on_settings_clicked(self, button):
        dialog = SettingsDialog(self, self.settings)
        dialog.present()

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
            self.status_label.set_label(f"{_('Error')}: {e}")

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
            self.project_info_label.set_label(_("{} strings · 29 languages").format(strings))

            self.translate_button.set_sensitive(True)
            self.status_label.set_label(_("Project: {}").format(textdomain))
            self.stack.set_visible_child_name("project")

            self._show_toast(_("Project loaded: {}").format(textdomain))

        except Exception as e:
            self._show_toast(f"{_('Error')}: {e}")

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
            GLib.idle_add(self.status_label.set_label, _("Extracting strings..."))
            GLib.idle_add(self.progress_subtitle.set_label, _("Extracting strings..."))

            scanner = ProjectScanner(str(self.selected_project))
            textdomain = scanner.detect_textdomain()
            files = scanner.find_python_files()

            extractor = GettextExtractor(str(self.selected_project), textdomain)
            extractor.extract_strings(files)
            count = extractor.get_string_count()

            GLib.idle_add(self.status_label.set_label, _("Translating {} strings...").format(count))

            api = APIFactory.create_from_settings(self.settings)
            translator = TranslationEngine(api, textdomain)

            def callback(lang, status, current, total):
                name = SUPPORTED_LANGUAGES.get(lang, lang)
                if "error" in status.lower():
                    GLib.idle_add(self._update_lang_status, lang, 'error')
                else:
                    GLib.idle_add(self._update_lang_status, lang, 'success')

                langs = list(SUPPORTED_LANGUAGES.keys())
                if current < total:
                    next_l = langs[current]
                    GLib.idle_add(self._update_lang_status, next_l, 'translating')
                    GLib.idle_add(self.progress_subtitle.set_label, f"{name}...")

                GLib.idle_add(self.progress_ring.set_progress, current / total)
                GLib.idle_add(self.status_label.set_label, f"[{current}/{total}] {name}")

            first = list(SUPPORTED_LANGUAGES.keys())[0]
            GLib.idle_add(self._update_lang_status, first, 'translating')

            results = translator.translate_project(
                extractor.pot_file, self.selected_project, callback
            )
            success = sum(1 for v in results.values() if v)

            # Compile
            if self.compile_switch.get_active():
                GLib.idle_add(self.progress_subtitle.set_label, _("Compiling..."))
                compiler = MoCompiler(self.selected_project, textdomain)
                compiler.compile_all()

            GLib.idle_add(self.progress_title.set_label, _("Completed!"))
            GLib.idle_add(self.progress_subtitle.set_label, _("{} languages translated").format(success))
            GLib.idle_add(self.progress_ring.set_progress, 1.0)
            GLib.idle_add(self.status_label.set_label, _("Completed: {}/29 languages").format(success))
            GLib.idle_add(self._show_toast, _("Translation complete! {} languages").format(success))

        except Exception as e:
            GLib.idle_add(self.progress_title.set_label, _("Error"))
            GLib.idle_add(self.progress_subtitle.set_label, str(e))
            GLib.idle_add(self.status_label.set_label, f"{_('Error')}: {e}")
            GLib.idle_add(self._show_toast, f"{_('Error')}: {e}")

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
