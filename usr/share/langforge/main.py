"""Entry point for LangForge."""

import logging
import sys
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
# Show translation progress in terminal
for _logger_name in (
    "core.translator",
    "core.controller",
    "api.paid_apis",
    "api.free_apis",
):
    logging.getLogger(_logger_name).setLevel(logging.INFO)

# Add src directory to path for imports
src_dir = Path(__file__).parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk

from ui.main_window import MainWindow
from utils.i18n import _

try:
    from __init__ import __version__ as APP_VERSION
except ImportError:
    APP_VERSION = "1.1.3"


class LangForgeApp(Adw.Application):
    """Main application."""

    def __init__(self):
        super().__init__(application_id="org.communitybig.langforge")

    def do_activate(self):
        """Called when application is activated."""
        win = self.props.active_window
        if not win:
            win = MainWindow(self)
        win.present()

    def do_startup(self):
        """Configure menu actions and keyboard shortcuts."""
        Adw.Application.do_startup(self)

        # Action: Settings
        settings_action = Gio.SimpleAction.new("settings", None)
        settings_action.connect("activate", self._on_settings)
        self.add_action(settings_action)
        self.set_accels_for_action("app.settings", ["<Control>comma"])

        # Action: About
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

        # Action: Open project
        open_action = Gio.SimpleAction.new("open-project", None)
        open_action.connect("activate", self._on_open_project)
        self.add_action(open_action)
        self.set_accels_for_action("app.open-project", ["<Control>o"])

        # Action: Start translation
        translate_action = Gio.SimpleAction.new("translate", None)
        translate_action.connect("activate", self._on_translate)
        self.add_action(translate_action)
        self.set_accels_for_action("app.translate", ["<Control>Return"])

        # Action: Quit
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def _on_settings(self, action, param):
        """Open settings dialog."""
        win = self.props.active_window
        if win:
            win._on_settings_clicked(None)

    def _on_open_project(self, action, param):
        """Open project via file dialog."""
        win = self.props.active_window
        if win:
            win._on_select_project(None)

    def _on_translate(self, action, param):
        """Start translation."""
        win = self.props.active_window
        if win:
            win._on_start_translation(None)

    def _on_about(self, action, param):
        """Show About dialog."""
        about = Adw.AboutWindow(
            application_name="LangForge",
            application_icon="langforge",
            developer_name="BigLinux",
            version=APP_VERSION,
            comments=_("Automatic translator for gettext projects"),
            website="https://github.com/biglinux/langforge",
            issue_url="https://github.com/biglinux/langforge/issues",
            developers=["BigLinux Team"],
            copyright="© 2024 BigLinux",
            license_type=Gtk.License.GPL_3_0,
            transient_for=self.props.active_window,
        )
        about.present()


def main():
    """Main function."""
    app = LangForgeApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
