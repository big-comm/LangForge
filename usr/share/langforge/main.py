"""Entry point for LangForge."""

import sys
from pathlib import Path

# Add src directory to path for imports
src_dir = Path(__file__).parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, Gtk

from ui.main_window import MainWindow
from utils.i18n import _

APP_VERSION = "1.0.2"
try:
    from __init__ import __version__
    APP_VERSION = __version__
except ImportError:
    pass


class LangForgeApp(Adw.Application):
    """Main application."""

    def __init__(self):
        super().__init__(application_id='com.biglinux.langforge')

    def do_activate(self):
        """Called when application is activated."""
        win = self.props.active_window
        if not win:
            win = MainWindow(self)
        win.present()

    def do_startup(self):
        """Configure menu actions."""
        Adw.Application.do_startup(self)

        # Action: Settings
        settings_action = Gio.SimpleAction.new("settings", None)
        settings_action.connect("activate", self._on_settings)
        self.add_action(settings_action)

        # Action: About
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

    def _on_settings(self, action, param):
        """Open settings dialog."""
        win = self.props.active_window
        if win:
            win._on_settings_clicked(None)

    def _on_about(self, action, param):
        """Show About dialog."""
        about = Adw.AboutWindow(
            application_name="LangForge",
            application_icon="langforge",
            developer_name="BigLinux",
            version=APP_VERSION,
            comments=_("Automatic translator for gettext projects"),
            website="https://github.com/biglinux/langforge",
            license_type=Gtk.License.GPL_3_0,
            transient_for=self.props.active_window
        )
        about.present()


def main():
    """Main function."""
    app = LangForgeApp()
    return app.run(sys.argv)


if __name__ == '__main__':
    sys.exit(main())
