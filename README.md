<p align="center">
  <img src="usr/share/icons/hicolor/scalable/apps/langforge.svg" width="128" alt="LangForge Icon">
</p>

<h1 align="center">LangForge</h1>

<p align="center">
  <strong>Automatic translator for gettext-based projects using AI APIs</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/GTK-4.0-4A86CF?logo=gnome&logoColor=white" alt="GTK 4.0">
  <img src="https://img.shields.io/badge/Adwaita-1.x-4A86CF?logo=gnome&logoColor=white" alt="Adwaita">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
  <img src="https://img.shields.io/badge/platform-Linux-FCC624?logo=linux&logoColor=black" alt="Linux">
</p>

---

## Overview

**LangForge** is a desktop application that automates the translation of [gettext](https://www.gnu.org/software/gettext/) projects (`.po`/`.pot` files) into 29 languages using AI-powered translation APIs. Built with GTK 4 and libadwaita, it follows modern GNOME Human Interface Guidelines for a native Linux desktop experience.

Point LangForge at any Python project that uses gettext, and it will:

1. **Scan** the project for translatable strings
2. **Extract** strings into a `.pot` template
3. **Translate** into 29 languages via your chosen API
4. **Compile** `.mo` binary files ready for distribution

## Features

- 🌍 **29 Languages** — Translate to Bulgarian, Czech, Danish, German, Greek, Estonian, Finnish, French, Hebrew, Croatian, Hungarian, Icelandic, Italian, Japanese, Korean, Dutch, Norwegian, Polish, Portuguese, Brazilian Portuguese, Romanian, Russian, Slovak, Swedish, Turkish, Ukrainian, Chinese, Spanish, and English
- 🤖 **Multiple AI APIs** — DeepL, Groq, Gemini, OpenRouter, Mistral, and LibreTranslate
- 💸 **Free & Paid Tiers** — Use free API tiers or unlock paid APIs for higher limits
- 📦 **Auto-compile** — Optionally compile `.po` → `.mo` after translation
- 🎯 **Drag & Drop** — Drop a project folder directly onto the window
- 📊 **Real-time Progress** — Circular progress ring with per-language status indicators
- 🎨 **Modern UI** — Native Adwaita split-view layout with card-style controls

## Screenshot

<!-- Add a screenshot of the app here -->
<!-- ![LangForge Screenshot](docs/screenshot.png) -->

## Installation

### Arch Linux / BigLinux (PKGBUILD)

```bash
cd pkgbuild
makepkg -si
```

### Manual Installation

```bash
# Dependencies
sudo pacman -S python gtk4 libadwaita python-gobject

# Install
sudo cp -r usr/share/langforge /usr/share/
sudo cp usr/share/applications/com.biglinux.langforge.desktop /usr/share/applications/
sudo cp usr/share/icons/hicolor/scalable/apps/langforge.svg /usr/share/icons/hicolor/scalable/apps/
sudo cp usr/bin/langforge /usr/bin/
sudo chmod +x /usr/bin/langforge
```

### Running from Source

```bash
python3 usr/share/langforge/main.py
```

## Usage

1. **Launch** LangForge from your application menu or terminal
2. **Configure** the translation API in the sidebar (Type: Free/Paid, Provider)
3. **Select** a project folder via button or drag & drop
4. **Click** "Start Translation" to begin
5. **Monitor** real-time progress for each language

### API Configuration

Click **API Settings** in the sidebar to configure your API keys:

| Provider | Tier | Key Required |
|---|---|---|
| DeepL Free | Free | ✅ |
| LibreTranslate | Free | ❌ |
| Groq | Free | ✅ |
| Gemini Free | Free | ✅ |
| OpenRouter | Paid | ✅ |
| Mistral | Paid | ✅ |

## Project Structure

```
LangForge/
├── usr/
│   ├── bin/langforge                    # Entry point script
│   └── share/
│       ├── langforge/
│       │   ├── main.py                  # Application entry point
│       │   ├── api/                     # API integrations
│       │   │   ├── base.py              # Base API class
│       │   │   ├── factory.py           # API factory
│       │   │   ├── free_apis.py         # Free tier APIs
│       │   │   └── paid_apis.py         # Paid tier APIs
│       │   ├── config/
│       │   │   └── settings.py          # App settings management
│       │   ├── core/
│       │   │   ├── compiler.py          # .mo file compiler
│       │   │   ├── extractor.py         # String extractor
│       │   │   ├── languages.py         # Supported languages
│       │   │   ├── scanner.py           # Project scanner
│       │   │   └── translator.py        # Translation engine
│       │   ├── ui/
│       │   │   ├── main_window.py       # Main window (GTK4/Adwaita)
│       │   │   ├── settings_dialog.py   # Settings dialog
│       │   │   └── style.css            # Custom styles
│       │   └── utils/
│       │       └── i18n.py              # Internationalization
│       ├── applications/
│       │   └── com.biglinux.langforge.desktop
│       └── icons/
│           └── hicolor/scalable/apps/langforge.svg
├── locale/                              # Translation files (29 languages)
├── pkgbuild/                            # Arch Linux packaging
├── LICENSE
└── README.md
```

## Requirements

| Dependency | Version |
|---|---|
| Python | ≥ 3.10 |
| GTK | 4.0 |
| libadwaita | ≥ 1.4 |
| PyGObject | ≥ 3.42 |

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## Credits

Developed by [BigLinux](https://github.com/biglinux) team.

---

<p align="center">
  Made with ❤️ for the open-source community
</p>
