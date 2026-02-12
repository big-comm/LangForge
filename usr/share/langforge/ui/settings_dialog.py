"""Application settings dialog."""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from config.settings import Settings
from api.factory import APIFactory
from utils.i18n import _


class SettingsDialog(Adw.PreferencesWindow):
    """Preferences/settings window."""

    def __init__(self, parent, settings: Settings):
        super().__init__()
        self.settings = settings
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_default_size(720, 500)

        self._build_ui()
        self._load_settings()

        # Auto-save on close
        self.connect("close-request", self._on_close)

    def _on_close(self, window):
        """Auto-save when closing window."""
        self._save_settings()
        return False

    def _save_settings(self):
        """Save all settings."""
        api_type = "free" if self.api_type_row.get_selected() == 0 else "paid"
        self.settings.set_api_type(api_type)

        # Free API
        provider_idx = self.free_provider_row.get_selected()
        free_providers = ["deepl-free", "groq", "gemini-free", "openrouter", "mistral-free", "libretranslate"]
        self.settings.set("free_api.provider", free_providers[provider_idx])
        self.settings.set("free_api.api_key", self.free_api_key.get_text())
        self.settings.set("free_api.libretranslate_url", self.libretranslate_url.get_text())

        # Paid API
        provider_idx = self.paid_provider_row.get_selected()
        paid_providers = ["openai", "gemini", "grok"]
        self.settings.set("paid_api.provider", paid_providers[provider_idx])
        self.settings.set("paid_api.api_key", self.api_key.get_text())
        self.settings.set("paid_api.model", self.model_name.get_text())

        self.settings.save()

    def _build_ui(self):
        """Build settings interface."""
        # API Page
        api_page = Adw.PreferencesPage()
        api_page.set_title(_("Translation API"))
        api_page.set_icon_name("network-wireless-symbolic")

        # Group: API Type
        type_group = Adw.PreferencesGroup()
        type_group.set_title(_("API Type"))

        self.api_type_row = Adw.ComboRow()
        self.api_type_row.set_title(_("Select type"))
        model = Gtk.StringList.new([_("Free API"), _("Paid API")])
        self.api_type_row.set_model(model)
        self.api_type_row.connect("notify::selected", self._on_api_type_changed)
        type_group.add(self.api_type_row)

        api_page.add(type_group)

        # Group: Free API
        self.free_group = Adw.PreferencesGroup()
        self.free_group.set_title(_("Free Tier APIs"))
        self.free_group.set_description(_("Recommended: DeepL (best quality) or Groq (best value)"))

        self.free_provider_row = Adw.ComboRow()
        self.free_provider_row.set_title(_("Provider"))
        self.free_provider_row.set_subtitle(_("DeepL and Groq are the best options for i18n translation"))
        free_model = Gtk.StringList.new([
            _("DeepL Free (500k chars/month - Best quality)"),
            _("Groq (14.4k req/day - Fastest)"),
            _("Gemini Free (1k req/day)"),
            _("OpenRouter (18 free models)"),
            _("Mistral Free"),
            _("LibreTranslate (Open Source)")
        ])
        self.free_provider_row.set_model(free_model)
        self.free_provider_row.connect("notify::selected", self._on_free_provider_changed)
        self.free_group.add(self.free_provider_row)

        # API Key field
        self.free_api_key = Adw.PasswordEntryRow()
        self.free_api_key.set_title(_("API Key"))
        self.free_api_key.set_visible(True)
        self.free_group.add(self.free_api_key)

        # LibreTranslate URL field
        self.libretranslate_url = Adw.EntryRow()
        self.libretranslate_url.set_title(_("LibreTranslate URL"))
        self.libretranslate_url.set_text("https://libretranslate.com")
        self.libretranslate_url.set_visible(False)
        self.free_group.add(self.libretranslate_url)

        api_page.add(self.free_group)

        # Group: Paid API
        self.paid_group = Adw.PreferencesGroup()
        self.paid_group.set_title(_("Paid API Settings"))

        self.paid_provider_row = Adw.ComboRow()
        self.paid_provider_row.set_title(_("Provider"))
        paid_model = Gtk.StringList.new(["OpenAI", "Gemini", "Grok (xAI)"])
        self.paid_provider_row.set_model(paid_model)
        self.paid_group.add(self.paid_provider_row)

        self.api_key = Adw.PasswordEntryRow()
        self.api_key.set_title(_("API Key"))
        self.paid_group.add(self.api_key)

        self.model_name = Adw.EntryRow()
        self.model_name.set_title(_("Model"))
        self.paid_group.add(self.model_name)

        api_page.add(self.paid_group)

        # Action buttons (centered)
        action_group = Adw.PreferencesGroup()

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        button_box.set_halign(Gtk.Align.CENTER)
        button_box.set_margin_top(8)
        button_box.set_margin_bottom(8)

        help_button = Gtk.Button()
        help_button.set_icon_name("help-browser-symbolic")
        help_button.set_tooltip_text(_("How to get API Keys"))
        help_button.connect("clicked", self._on_show_api_help)
        button_box.append(help_button)

        test_button = Gtk.Button(label=_("Test Connection"))
        test_button.add_css_class("suggested-action")
        test_button.connect("clicked", self._on_test_connection)
        button_box.append(test_button)

        action_row = Adw.ActionRow()
        action_row.set_child(button_box)
        action_group.add(action_row)

        api_page.add(action_group)

        self.add(api_page)

    def _load_settings(self):
        """Load saved settings."""
        api_type = self.settings.get_api_type()
        self.api_type_row.set_selected(0 if api_type == "free" else 1)

        # Free API
        free_provider = self.settings.get("free_api.provider", "deepl-free")
        provider_map = {"deepl-free": 0, "groq": 1, "gemini-free": 2, "openrouter": 3, "mistral-free": 4, "libretranslate": 5}
        self.free_provider_row.set_selected(provider_map.get(free_provider, 0))

        self.free_api_key.set_text(self.settings.get("free_api.api_key", ""))
        self.libretranslate_url.set_text(
            self.settings.get("free_api.libretranslate_url", "https://libretranslate.com")
        )

        # Paid API
        paid_provider = self.settings.get("paid_api.provider", "openai")
        paid_map = {"openai": 0, "gemini": 1, "grok": 2}
        self.paid_provider_row.set_selected(paid_map.get(paid_provider, 0))

        self.api_key.set_text(self.settings.get("paid_api.api_key", ""))
        self.model_name.set_text(self.settings.get("paid_api.model", "gpt-4o-mini"))

        self._update_visibility()
        self._update_free_api_fields()

    def _on_api_type_changed(self, combo, _):
        """Called when API type changes."""
        self._update_visibility()

    def _on_free_provider_changed(self, combo, _):
        """Called when free provider changes."""
        self._update_free_api_fields()

    def _update_visibility(self):
        """Update group visibility based on type."""
        is_free = self.api_type_row.get_selected() == 0
        self.free_group.set_visible(is_free)
        self.paid_group.set_visible(not is_free)

    def _update_free_api_fields(self):
        """Update free API fields visibility."""
        provider_idx = self.free_provider_row.get_selected()
        needs_api_key = provider_idx in [0, 1, 2, 3, 4]
        self.free_api_key.set_visible(needs_api_key)
        self.libretranslate_url.set_visible(provider_idx == 5)

        titles = {
            0: "DeepL API Key",
            1: "Groq API Key",
            2: "Gemini API Key",
            3: "OpenRouter API Key",
            4: "Mistral API Key"
        }
        if needs_api_key:
            self.free_api_key.set_title(titles.get(provider_idx, _("API Key")))

    def _on_test_connection(self, button):
        """Test API connection."""
        import sys
        try:
            api_type = "free" if self.api_type_row.get_selected() == 0 else "paid"

            if api_type == "free":
                provider_idx = self.free_provider_row.get_selected()
                free_providers = ["deepl-free", "groq", "gemini-free", "openrouter", "mistral-free", "libretranslate"]
                provider = free_providers[provider_idx]
                api_key = self.free_api_key.get_text()

                if provider == "libretranslate":
                    api = APIFactory.create(provider, url=self.libretranslate_url.get_text())
                else:
                    api = APIFactory.create(provider, api_key)
            else:
                provider_idx = self.paid_provider_row.get_selected()
                paid_providers = ["openai", "gemini", "grok"]
                provider = paid_providers[provider_idx]
                api_key = self.api_key.get_text()
                model = self.model_name.get_text()
                api = APIFactory.create(provider, api_key, model=model)

            if api.test_connection():
                self._show_toast(_("✅ Connection OK!"), Adw.ToastPriority.HIGH)
            else:
                self._show_toast(_("❌ Connection failed"), Adw.ToastPriority.HIGH)

        except Exception as e:
            error_msg = str(e)
            # Truncar mensagem longa para caber no toast
            if len(error_msg) > 100:
                error_msg = error_msg[:100] + "..."
            print(f"[LangForge] Connection test error: {e}", file=sys.stderr)
            self._show_toast(f"❌ {error_msg}", Adw.ToastPriority.HIGH)

    def _on_save(self, button):
        """Save settings."""
        self._save_settings()
        self._show_toast(_("✓ Settings saved!"), Adw.ToastPriority.HIGH)

    def _on_show_api_help(self, button):
        """Show dialog with instructions for getting free API keys."""
        dialog = Adw.Window()
        dialog.set_transient_for(self)
        dialog.set_modal(True)
        dialog.set_default_size(550, 520)

        toolbar = Adw.ToolbarView()
        dialog.set_content(toolbar)

        # Header bar without title
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Box())
        toolbar.add_top_bar(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        toolbar.set_content(scroll)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(24)
        content.set_margin_end(24)
        scroll.set_child(content)

        title = Gtk.Label(label=_("Available Free APIs"))
        title.add_css_class("title-1")
        content.append(title)

        subtitle = Gtk.Label(label=_("Click the links to create your account and get your key"))
        subtitle.add_css_class("dim-label")
        subtitle.set_margin_bottom(12)
        content.append(subtitle)

        apis = [
            {
                "name": "DeepL Free",
                "icon": "starred-symbolic",
                "badge": _("Recommended"),
                "limit": _("500k characters/month"),
                "quality": _("Best translation quality"),
                "url": "https://www.deepl.com/pro-api",
                "steps": _("Create account → Free Plan → API Keys")
            },
            {
                "name": "Groq",
                "icon": "media-playback-start-symbolic",
                "badge": _("Fastest"),
                "limit": _("14,400 requests/day"),
                "quality": _("LLaMA 3 - Excellent value"),
                "url": "https://console.groq.com",
                "steps": _("Create account → API Keys → Create")
            },
            {
                "name": "Gemini Free",
                "icon": "applications-science-symbolic",
                "badge": None,
                "limit": _("1,000 requests/day"),
                "quality": _("Google AI - Good quality"),
                "url": "https://aistudio.google.com/apikey",
                "steps": _("Google Login → Get API Key")
            },
            {
                "name": "OpenRouter",
                "icon": "network-server-symbolic",
                "badge": None,
                "limit": _("18 free models"),
                "quality": _("Multiple models available"),
                "url": "https://openrouter.ai",
                "steps": _("Create account → Keys → Create Key")
            },
            {
                "name": "Mistral Free",
                "icon": "weather-windy-symbolic",
                "badge": None,
                "limit": _("Free tier available"),
                "quality": _("Quality Mistral models"),
                "url": "https://console.mistral.ai",
                "steps": _("Create account → API Keys → Generate")
            },
            {
                "name": "LibreTranslate",
                "icon": "emblem-documents-symbolic",
                "badge": _("No API Key"),
                "limit": _("Unlimited (self-hosted)"),
                "quality": _("Open source - Basic quality"),
                "url": "https://libretranslate.com",
                "steps": _("Use default URL or your own server")
            },
        ]

        for api in apis:
            card = self._create_api_help_card(api)
            content.append(card)

        dialog.present()

    def _create_api_help_card(self, api: dict) -> Gtk.Box:
        """Create help card for an API."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("card")
        card.set_margin_top(4)
        card.set_margin_bottom(4)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_margin_start(16)
        header.set_margin_end(16)
        header.set_margin_top(12)

        icon = Gtk.Image.new_from_icon_name(api["icon"])
        icon.set_pixel_size(24)
        header.append(icon)

        name = Gtk.Label(label=api["name"])
        name.add_css_class("title-4")
        name.set_hexpand(True)
        name.set_halign(Gtk.Align.START)
        header.append(name)

        if api.get("badge"):
            badge = Gtk.Label(label=api["badge"])
            badge.add_css_class("caption")
            badge.add_css_class("success")
            header.append(badge)

        card.append(header)

        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        info_box.set_margin_start(52)
        info_box.set_margin_end(16)
        info_box.set_margin_bottom(12)

        limit_label = Gtk.Label(label=f"• {api['limit']}")
        limit_label.set_halign(Gtk.Align.START)
        limit_label.add_css_class("dim-label")
        info_box.append(limit_label)

        quality_label = Gtk.Label(label=f"• {api['quality']}")
        quality_label.set_halign(Gtk.Align.START)
        quality_label.add_css_class("dim-label")
        info_box.append(quality_label)

        steps_label = Gtk.Label(label=f"• {api['steps']}")
        steps_label.set_halign(Gtk.Align.START)
        steps_label.add_css_class("dim-label")
        info_box.append(steps_label)

        link_button = Gtk.LinkButton.new_with_label(api["url"], f"{_('Open')} {api['url']}")
        link_button.set_halign(Gtk.Align.START)
        info_box.append(link_button)

        card.append(info_box)

        return card

    def _show_toast(self, message: str, priority):
        """Show toast notification in this window."""
        toast = Adw.Toast.new(message)
        toast.set_priority(priority)
        toast.set_timeout(3)
        self.add_toast(toast)
