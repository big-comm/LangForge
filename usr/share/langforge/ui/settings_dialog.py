"""Application settings dialog."""

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from config.settings import Settings
from api.factory import APIFactory
from utils.i18n import _

log = logging.getLogger(__name__)

# Available models per free provider
_FREE_MODELS: dict[str, list[str]] = {
    "openrouter": [
        "meta-llama/llama-3.1-8b-instruct:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "google/gemma-2-9b-it:free",
        "google/gemma-3-4b-it:free",
        "google/gemma-3-12b-it:free",
        "google/gemma-3-27b-it:free",
        "mistralai/mistral-7b-instruct:free",
        "microsoft/phi-3-mini-128k-instruct:free",
        "microsoft/phi-3-medium-128k-instruct:free",
        "qwen/qwen-2-7b-instruct:free",
        "qwen/qwen-2.5-7b-instruct:free",
        "qwen/qwen-2.5-72b-instruct:free",
        "deepseek/deepseek-r1-distill-llama-70b:free",
        "nousresearch/hermes-3-llama-3.1-405b:free",
        "huggingfaceh4/zephyr-7b-beta:free",
        "openchat/openchat-7b:free",
        "undi95/toppy-m-7b:free",
        "gryphe/mythomist-7b:free",
    ],
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "gemma2-9b-it",
        "mixtral-8x7b-32768",
    ],
    "gemini-free": [
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ],
    "mistral-free": [
        "mistral-small-latest",
        "open-mistral-7b",
        "open-mixtral-8x7b",
    ],
}

# Available models per paid provider
_PAID_MODELS: dict[str, list[str]] = {
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4.1-mini",
        "gpt-4.1",
        "gpt-4.1-nano",
        "o4-mini",
    ],
    "gemini": [
        "gemini-2.0-flash",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-1.5-pro",
    ],
    "grok": [
        "grok-4-fast",
        "grok-3-fast",
        "grok-3-mini-fast",
        "grok-2",
    ],
}


def _model_display_name(model_id: str) -> str:
    """Extract a clean display name from a full model ID.

    'meta-llama/llama-3.1-8b-instruct:free' → 'llama-3.1-8b-instruct'
    'gpt-4o-mini' → 'gpt-4o-mini'
    """
    name = model_id
    if "/" in name:
        name = name.split("/", 1)[1]
    if name.endswith(":free"):
        name = name[:-5]
    return name


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
        free_provider = self._get_selected_free_provider()
        self.settings.set("free_api.provider", free_provider)
        self.settings.set("free_api.api_key", self.free_api_key.get_text())
        self.settings.set(
            "free_api.libretranslate_url", self.libretranslate_url.get_text()
        )
        # Save selected model for providers with model selection
        models = _FREE_MODELS.get(free_provider, [])
        if models:
            idx = self.free_model_row.get_selected()
            if 0 <= idx < len(models):
                self.settings.set("free_api.model", models[idx])

        # Paid API
        paid_provider = self._get_selected_paid_provider()
        self.settings.set("paid_api.provider", paid_provider)
        self.settings.set("paid_api.api_key", self.api_key.get_text())
        # Save selected paid model
        for model_id, check in self._paid_model_checks.items():
            if check.get_active():
                self.settings.set("paid_api.model", model_id)
                break

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

        # Group: Free API — Progressive Disclosure (U1)
        self.free_group = Adw.PreferencesGroup()
        self.free_group.set_title(_("Free Tier APIs"))
        self.free_group.set_description(_("Select your preferred translation provider"))

        # Provider definitions with contextual help (U4)
        _free_defs = [
            {
                "id": "deepl-free",
                "name": "DeepL Free",
                "subtitle": _("500k chars/month — Best translation quality"),
                "recommended": True,
            },
            {
                "id": "groq",
                "name": "Groq",
                "subtitle": _("14.4k req/day — Fastest, excellent quality"),
                "recommended": True,
            },
            {
                "id": "gemini-free",
                "name": "Gemini Free",
                "subtitle": _("1,000 req/day — Google AI"),
                "recommended": False,
            },
            {
                "id": "openrouter",
                "name": "OpenRouter",
                "subtitle": _("18 free models available"),
                "recommended": False,
            },
            {
                "id": "mistral-free",
                "name": "Mistral Free",
                "subtitle": _("Free tier — Quality models"),
                "recommended": False,
            },
            {
                "id": "libretranslate",
                "name": "LibreTranslate",
                "subtitle": _("Open source — No API key needed"),
                "recommended": False,
            },
        ]

        self._provider_checks: dict[str, Gtk.CheckButton] = {}
        first_check: Gtk.CheckButton | None = None

        # Recommended providers (always visible)
        for pdef in _free_defs:
            if not pdef["recommended"]:
                continue
            row = Adw.ActionRow()
            row.set_title(pdef["name"])
            row.set_subtitle(pdef["subtitle"])
            check = Gtk.CheckButton()
            if first_check is None:
                first_check = check
            else:
                check.set_group(first_check)
            check.connect("toggled", self._on_free_provider_toggled, pdef["id"])
            row.add_suffix(check)
            row.set_activatable_widget(check)
            self._provider_checks[pdef["id"]] = check
            self.free_group.add(row)

        # Expander for additional providers (progressive disclosure)
        self._more_providers_row = Adw.ExpanderRow()
        self._more_providers_row.set_title(_("More providers"))
        self._more_providers_row.set_subtitle(_("4 additional free options"))

        for pdef in _free_defs:
            if pdef["recommended"]:
                continue
            row = Adw.ActionRow()
            row.set_title(pdef["name"])
            row.set_subtitle(pdef["subtitle"])
            check = Gtk.CheckButton()
            check.set_group(first_check)
            check.connect("toggled", self._on_free_provider_toggled, pdef["id"])
            row.add_suffix(check)
            row.set_activatable_widget(check)
            self._provider_checks[pdef["id"]] = check
            self._more_providers_row.add_row(row)

        self.free_group.add(self._more_providers_row)

        # API Key field
        self.free_api_key = Adw.PasswordEntryRow()
        self.free_api_key.set_title(_("API Key"))
        self.free_api_key.set_visible(True)
        self.free_group.add(self.free_api_key)

        # DeepL usage info row (shown only when DeepL is selected and has key)
        self._deepl_usage_row = Adw.ActionRow()
        self._deepl_usage_row.set_title(_("Usage"))
        self._deepl_usage_row.set_subtitle(_("Click 'Test Connection' to check"))
        self._deepl_usage_row.set_icon_name("dialog-information-symbolic")
        self._deepl_usage_row.set_visible(False)
        self.free_group.add(self._deepl_usage_row)

        # Model selector for providers with multiple models
        self.free_model_row = Adw.ComboRow()
        self.free_model_row.set_title(_("Model"))
        self.free_model_row.set_visible(False)
        self.free_group.add(self.free_model_row)

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

        # Provider selection as ExpanderRow with radio buttons
        _paid_defs = [
            {
                "id": "openai",
                "name": "OpenAI",
                "subtitle": _("GPT-4o, GPT-4.1 — Industry standard"),
            },
            {
                "id": "gemini",
                "name": "Gemini (Google AI)",
                "subtitle": _("Gemini 2.5 Flash/Pro — Fast and capable"),
            },
            {
                "id": "grok",
                "name": "Grok (xAI)",
                "subtitle": _("$25 free credits — 2M context window"),
            },
        ]

        self._paid_provider_expander = Adw.ExpanderRow()
        self._paid_provider_expander.set_title(_("Provider"))
        self._paid_provider_expander.set_icon_name("network-server-symbolic")

        self._paid_provider_checks: dict[str, Gtk.CheckButton] = {}
        first_paid_check: Gtk.CheckButton | None = None

        for pdef in _paid_defs:
            row = Adw.ActionRow()
            row.set_title(pdef["name"])
            row.set_subtitle(pdef["subtitle"])
            check = Gtk.CheckButton()
            if first_paid_check is None:
                first_paid_check = check
            else:
                check.set_group(first_paid_check)
            check.connect("toggled", self._on_paid_provider_toggled, pdef["id"])
            row.add_suffix(check)
            row.set_activatable_widget(check)
            self._paid_provider_checks[pdef["id"]] = check
            self._paid_provider_expander.add_row(row)

        self.paid_group.add(self._paid_provider_expander)

        self.api_key = Adw.PasswordEntryRow()
        self.api_key.set_title(_("API Key"))
        self.paid_group.add(self.api_key)

        # Model selector as ExpanderRow with radio buttons
        self._paid_model_expander = Adw.ExpanderRow()
        self._paid_model_expander.set_title(_("Model"))
        self._paid_model_expander.set_icon_name("view-list-symbolic")
        self._paid_model_checks: dict[str, Gtk.CheckButton] = {}
        self.paid_group.add(self._paid_model_expander)

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
        help_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("How to get API Keys")],
        )
        help_button.connect("clicked", self._on_show_api_help)
        button_box.append(help_button)

        test_button = Gtk.Button(label=_("Test Connection"))
        test_button.add_css_class("suggested-action")
        test_button.connect("clicked", self._on_test_connection)
        button_box.append(test_button)

        action_row = Adw.ActionRow()
        action_row.set_child(button_box)
        action_group.add(action_row)

        # Inline status label for connection test results (accessible to Orca)
        self.connection_status_label = Gtk.Label()
        self.connection_status_label.set_halign(Gtk.Align.CENTER)
        self.connection_status_label.set_visible(False)
        status_row = Adw.ActionRow()
        status_row.set_child(self.connection_status_label)
        action_group.add(status_row)

        api_page.add(action_group)

        self.add(api_page)

    def _load_settings(self):
        """Load saved settings."""
        api_type = self.settings.get_api_type()
        self.api_type_row.set_selected(0 if api_type == "free" else 1)

        # Free API
        free_provider = self.settings.get("free_api.provider", "deepl-free")
        if free_provider in self._provider_checks:
            self._provider_checks[free_provider].set_active(True)
            # Expand "more" section if non-recommended provider is selected
            if free_provider not in ("deepl-free", "groq"):
                self._more_providers_row.set_expanded(True)

        self.free_api_key.set_text(self.settings.get("free_api.api_key", ""))
        self.libretranslate_url.set_text(
            self.settings.get(
                "free_api.libretranslate_url", "https://libretranslate.com"
            )
        )

        # Paid API
        paid_provider = self.settings.get("paid_api.provider", "openai")
        if paid_provider in self._paid_provider_checks:
            self._paid_provider_checks[paid_provider].set_active(True)

        self.api_key.set_text(self.settings.get("paid_api.api_key", ""))
        self._update_paid_provider_subtitle()
        self._update_paid_model_list()

        self._update_visibility()
        self._update_free_api_fields()

    def _on_api_type_changed(self, combo, _):
        """Called when API type changes."""
        self._update_visibility()

    def _on_free_provider_toggled(self, check: Gtk.CheckButton, provider_id: str):
        """Called when a free provider radio button is toggled."""
        if not check.get_active():
            return
        self._update_free_api_fields()
        # M7: Warn user that existing key may not work with new provider
        if self.free_api_key.get_text():
            self._set_connection_status(
                _("Provider changed — verify your API key is correct"),
                "warning",
            )

    def _update_visibility(self):
        """Update group visibility based on type."""
        is_free = self.api_type_row.get_selected() == 0
        self.free_group.set_visible(is_free)
        self.paid_group.set_visible(not is_free)

    def _on_paid_provider_changed(self, combo, _pspec):
        """Update model list when paid provider changes."""
        self._update_paid_model_list()

    def _on_paid_provider_toggled(self, check: Gtk.CheckButton, provider_id: str):
        """Called when a paid provider radio button is toggled."""
        if not check.get_active():
            return
        self._update_paid_provider_subtitle()
        self._update_paid_model_list()

    def _get_selected_paid_provider(self) -> str:
        """Return the currently selected paid provider ID."""
        for pid, check in self._paid_provider_checks.items():
            if check.get_active():
                return pid
        return "openai"

    def _update_paid_provider_subtitle(self):
        """Update the paid provider expander subtitle."""
        for pid, check in self._paid_provider_checks.items():
            if check.get_active():
                names = {"openai": "OpenAI", "gemini": "Gemini", "grok": "Grok"}
                self._paid_provider_expander.set_subtitle(names.get(pid, pid))
                return

    def _update_paid_model_list(self):
        """Populate paid model ExpanderRow with radio buttons."""
        # Remove old rows
        for child_check in self._paid_model_checks.values():
            row = child_check.get_ancestor(Adw.ActionRow)
            if row:
                self._paid_model_expander.remove(row)
        self._paid_model_checks.clear()

        provider = self._get_selected_paid_provider()
        models = _PAID_MODELS.get(provider, [])
        saved_model = self.settings.get("paid_api.model", "")

        first_check: Gtk.CheckButton | None = None
        for model_id in models:
            display = _model_display_name(model_id)
            row = Adw.ActionRow()
            row.set_title(display)
            check = Gtk.CheckButton()
            if first_check is None:
                first_check = check
            else:
                check.set_group(first_check)
            if model_id == saved_model:
                check.set_active(True)
            row.add_suffix(check)
            row.set_activatable_widget(check)
            self._paid_model_checks[model_id] = check
            self._paid_model_expander.add_row(row)

        # Default to first model if none saved matches
        if first_check and not any(
            c.get_active() for c in self._paid_model_checks.values()
        ):
            first_check.set_active(True)

        # Update expander subtitle
        self._update_paid_model_subtitle()
        for check in self._paid_model_checks.values():
            check.connect("toggled", lambda _c: self._update_paid_model_subtitle())

    def _update_paid_model_subtitle(self):
        """Update the paid model expander subtitle with selected model."""
        for model_id, check in self._paid_model_checks.items():
            if check.get_active():
                self._paid_model_expander.set_subtitle(_model_display_name(model_id))
                return

    def _get_selected_free_provider(self) -> str:
        """Return the currently selected free provider ID."""
        for pid, check in self._provider_checks.items():
            if check.get_active():
                return pid
        return "deepl-free"

    def _update_free_api_fields(self):
        """Update free API fields visibility based on selected provider."""
        provider = self._get_selected_free_provider()
        needs_key = provider != "libretranslate"
        self.free_api_key.set_visible(needs_key)
        self.libretranslate_url.set_visible(provider == "libretranslate")

        # DeepL usage row only visible when DeepL is selected
        self._deepl_usage_row.set_visible(provider == "deepl-free")

        # Model selector
        models = _FREE_MODELS.get(provider, [])
        if models:
            display_names = [_model_display_name(m) for m in models]
            self.free_model_row.set_model(Gtk.StringList.new(display_names))
            # Restore saved model
            saved_model = self.settings.get("free_api.model", "")
            for i, m in enumerate(models):
                if m == saved_model:
                    self.free_model_row.set_selected(i)
                    break
            self.free_model_row.set_visible(True)
        else:
            self.free_model_row.set_visible(False)

        key_titles = {
            "deepl-free": "DeepL API Key",
            "groq": "Groq API Key",
            "gemini-free": "Gemini API Key",
            "openrouter": "OpenRouter API Key",
            "mistral-free": "Mistral API Key",
        }
        if needs_key:
            self.free_api_key.set_title(key_titles.get(provider, _("API Key")))

    def _on_test_connection(self, button):
        """Test API connection in background thread."""
        import threading

        # Show testing state
        button.set_sensitive(False)
        button.set_label(_("Testing..."))
        self._set_connection_status(_("Testing connection..."), "dim-label")

        def _test():
            try:
                api_type = "free" if self.api_type_row.get_selected() == 0 else "paid"

                if api_type == "free":
                    provider = self._get_selected_free_provider()
                    api_key = self.free_api_key.get_text()

                    if provider == "libretranslate":
                        api = APIFactory.create(
                            provider, url=self.libretranslate_url.get_text()
                        )
                    else:
                        models = _FREE_MODELS.get(provider, [])
                        model = None
                        if models:
                            idx = self.free_model_row.get_selected()
                            if 0 <= idx < len(models):
                                model = models[idx]
                        if model:
                            api = APIFactory.create(provider, api_key, model=model)
                        else:
                            api = APIFactory.create(provider, api_key)
                else:
                    provider = self._get_selected_paid_provider()
                    api_key = self.api_key.get_text()
                    model = ""
                    for mid, chk in self._paid_model_checks.items():
                        if chk.get_active():
                            model = mid
                            break
                    api = APIFactory.create(provider, api_key, model=model)

                if api.test_connection():
                    GLib.idle_add(
                        self._set_connection_status,
                        _("Connection successful"),
                        "success",
                    )
                    # Fetch DeepL usage when connected
                    if api_type == "free" and provider == "deepl-free":
                        try:
                            usage = api.get_usage()
                            used = usage.get("character_count", 0)
                            limit = usage.get("character_limit", 0)
                            pct = (used / limit * 100) if limit else 0
                            text = f"{used:,} / {limit:,} ({pct:.1f}%)"
                            GLib.idle_add(self._deepl_usage_row.set_subtitle, text)
                        except Exception:
                            pass
                else:
                    GLib.idle_add(
                        self._set_connection_status,
                        _("Connection failed"),
                        "error",
                    )

            except Exception as e:
                error_msg = str(e)
                if len(error_msg) > 100:
                    error_msg = error_msg[:100] + "..."
                log.debug("Connection test error: %s", e)
                GLib.idle_add(self._set_connection_status, f"{error_msg}", "error")

            finally:
                GLib.idle_add(self._reset_test_button, button)

        thread = threading.Thread(target=_test, daemon=True)
        thread.start()

    def _set_connection_status(self, message: str, css_class: str):
        """Update inline connection status label."""
        self.connection_status_label.set_label(message)
        for cls in ["success", "error", "dim-label"]:
            self.connection_status_label.remove_css_class(cls)
        self.connection_status_label.add_css_class(css_class)
        self.connection_status_label.set_visible(True)

    def _reset_test_button(self, button):
        """Reset test button to default state."""
        button.set_sensitive(True)
        button.set_label(_("Test Connection"))

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
        dialog.set_title(_("Available Free APIs"))

        toolbar = Adw.ToolbarView()
        dialog.set_content(toolbar)

        # Header bar
        header = Adw.HeaderBar()
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

        subtitle = Gtk.Label(
            label=_("Click the links to create your account and get your key")
        )
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
                "steps": _("Create account → Free Plan → API Keys"),
            },
            {
                "name": "Groq",
                "icon": "media-playback-start-symbolic",
                "badge": _("Fastest"),
                "limit": _("14,400 requests/day"),
                "quality": _("LLaMA 3 - Excellent value"),
                "url": "https://console.groq.com",
                "steps": _("Create account → API Keys → Create"),
            },
            {
                "name": "Gemini Free",
                "icon": "applications-science-symbolic",
                "badge": None,
                "limit": _("1,000 requests/day"),
                "quality": _("Google AI - Good quality"),
                "url": "https://aistudio.google.com/apikey",
                "steps": _("Google Login → Get API Key"),
            },
            {
                "name": "OpenRouter",
                "icon": "network-server-symbolic",
                "badge": None,
                "limit": _("18 free models"),
                "quality": _("Multiple models available"),
                "url": "https://openrouter.ai",
                "steps": _("Create account → Keys → Create Key"),
            },
            {
                "name": "Mistral Free",
                "icon": "weather-windy-symbolic",
                "badge": None,
                "limit": _("Free tier available"),
                "quality": _("Quality Mistral models"),
                "url": "https://console.mistral.ai",
                "steps": _("Create account → API Keys → Generate"),
            },
            {
                "name": "LibreTranslate",
                "icon": "emblem-documents-symbolic",
                "badge": _("No API Key"),
                "limit": _("Unlimited (self-hosted)"),
                "quality": _("Open source - Basic quality"),
                "url": "https://libretranslate.com",
                "steps": _("Use default URL or your own server"),
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

        link_button = Gtk.LinkButton.new_with_label(
            api["url"], f"{_('Open')} {api['url']}"
        )
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
