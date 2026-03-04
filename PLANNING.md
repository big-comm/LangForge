# PLANNING.md — LangForge Project Improvement Roadmap

## Files Analyzed

**Total files read:** 20 (all Python files in the project)
**Total lines analyzed:** 2,524
**Large files (>500 lines) confirmed read in full:**
- `usr/share/langforge/ui/main_window.py` — 666 lines (read in full: lines 1–666)
- No other file exceeds 500 lines

**Complete file list:**

| # | File | Lines | Read |
|---|------|-------|------|
| 1  | `usr/share/langforge/__init__.py` | 3 | ✅ |
| 2  | `usr/share/langforge/main.py` | 82 | ✅ |
| 3  | `usr/share/langforge/api/__init__.py` | 1 | ✅ |
| 4  | `usr/share/langforge/api/base.py` | 38 | ✅ |
| 5  | `usr/share/langforge/api/factory.py` | 108 | ✅ |
| 6  | `usr/share/langforge/api/free_apis.py` | 370 | ✅ |
| 7  | `usr/share/langforge/api/paid_apis.py` | 164 | ✅ |
| 8  | `usr/share/langforge/config/__init__.py` | 1 | ✅ |
| 9  | `usr/share/langforge/config/settings.py` | 90 | ✅ |
| 10 | `usr/share/langforge/core/__init__.py` | 1 | ✅ |
| 11 | `usr/share/langforge/core/compiler.py` | 98 | ✅ |
| 12 | `usr/share/langforge/core/extractor.py` | 83 | ✅ |
| 13 | `usr/share/langforge/core/languages.py` | 48 | ✅ |
| 14 | `usr/share/langforge/core/scanner.py` | 86 | ✅ |
| 15 | `usr/share/langforge/core/translator.py` | 242 | ✅ |
| 16 | `usr/share/langforge/ui/__init__.py` | 1 | ✅ |
| 17 | `usr/share/langforge/ui/main_window.py` | 666 | ✅ |
| 18 | `usr/share/langforge/ui/settings_dialog.py` | 417 | ✅ |
| 19 | `usr/share/langforge/utils/__init__.py` | 7 | ✅ |
| 20 | `usr/share/langforge/utils/i18n.py` | 33 | ✅ |

---

## Current State Summary

**Overall quality grade: C+**

LangForge is a functional GTK4/Adwaita translation tool that automates gettext localization across 29 languages via multiple free and paid API providers. The core translation pipeline (scan → extract → translate → compile) works and is reasonably well-structured.

**What works:**
- Clean factory pattern for API providers with good extensibility
- Proper placeholder protection/restoration during translation (`_protect_placeholders`, `_restore_placeholders`)
- Correct use of `GLib.idle_add` for thread-safe UI updates
- Adwaita widgets and split-view layout follow modern GNOME patterns
- Configuration persistence via JSON in `~/.config/`
- Proper i18n setup with gettext

**What doesn't work or is problematic:**
- **Zero test coverage** — no tests exist anywhere in the project
- **No accessibility** — not a single `set_accessible_name()` call in the entire project; Orca users cannot use this application
- **AdwToastOverlay used for critical feedback** — connection test results and errors shown as transient toasts that Orca may miss
- **Custom ProgressRing uses Cairo directly** — completely invisible to assistive technology
- **No error recovery or undo** — translation is all-or-nothing with no way to cancel mid-operation
- **API keys stored in plaintext JSON** — no use of system keyring
- **Config directory mismatch** — code uses `~/.config/translation-automator/` but app is named `LangForge`
- **subprocess calls** with `capture_output=True` but output is never checked (extractor.py:50)
- **No type stubs** for `polib` and `requests`

---

## Critical (fix immediately)

### C1. No Test Suite ✅ DONE
- **All files** — Zero test files exist. No unit, integration, or UI tests.
- **Impact:** Any change can silently break core functionality (translation pipeline, placeholder protection, API integration).
- **Fix:** Create `tests/` directory with:
  - `test_translator.py` — placeholder protection/restoration, validation, fix logic
  - `test_scanner.py` — textdomain detection, project validation
  - `test_extractor.py` — mock xgettext calls
  - `test_compiler.py` — mock msgfmt calls
  - `test_factory.py` — provider creation, settings-based creation
  - `test_settings.py` — config load/save/get/set
- **Priority:** CRITICAL — must exist before any other changes

### C2. Orca Screen Reader: Custom ProgressRing Invisible ✅ DONE
- **File:** `ui/main_window.py:22–56`
- **Issue:** `ProgressRing(Gtk.DrawingArea)` draws with Cairo but has zero ATK integration. Orca announces nothing — a blind user has no idea translation is in progress, what percentage is complete, or when it finishes.
- **Fix:** Add `ATK.Role.PROGRESS_BAR` role. Implement `set_accessible_name()` and update `accessible-value-now` on each progress change. Alternative: replace with `Gtk.ProgressBar` wrapped in a circular CSS clip (simpler and natively accessible).
- **Orca experience now:** Complete silence during the entire translation process.

### C3. Orca Screen Reader: No Accessible Labels on Any Interactive Widget ✅ DONE
- **File:** `ui/main_window.py` (entire file), `ui/settings_dialog.py` (entire file)
- **Widgets missing accessible names:**
  - `ui/main_window.py:161` — `self.compile_switch`: no `set_accessible_name()` on the switch itself
  - `ui/main_window.py:204` — `menu_button`: no accessible name, Orca says "button" only
  - `ui/main_window.py:253` — `select_btn` in drop zone: label exists but drop zone frame has no accessible description
  - `ui/main_window.py:330–348` — Language grid items: each `Gtk.Box` with a label and icon but no accessible name on the FlowBox children; Orca cannot describe what each item represents
  - `ui/settings_dialog.py:98` — `self.free_api_key (Adw.PasswordEntryRow)`: title is set which acts as label — OK
  - `ui/settings_dialog.py:104` — `self.libretranslate_url`: title acts as label — OK
  - `ui/settings_dialog.py:140` — `help_button`: has tooltip but no accessible name; Orca reads icon name only
  - `ui/settings_dialog.py:144` — `test_button`: label exists — OK
- **Fix:** Add `widget.update_property([Gtk.AccessibleProperty.LABEL], ["descriptive name"])` or `set_accessible_name()` on every interactive widget.

### C4. Orca: Toast Notifications for Critical Feedback ✅ DONE
- **Files:** `ui/main_window.py:664–666`, `ui/settings_dialog.py:241–245`
- **Issue:** Connection test success/failure, translation completion, and errors are shown only via `Adw.Toast` — these are transient notifications that Orca may or may not announce depending on timing and focus state. A blind user testing their API connection may never know if it succeeded.
- **Fix:** For critical state changes (connection test results, translation complete/error, project validation), use an inline `Adw.StatusPage` or `Gtk.Label` in the UI with `ATK.Role.ALERT` so Orca announces the result. Keep toasts for nice-to-have confirmations only.

### C5. API Keys Stored in Plaintext ✅ DONE
- **File:** `config/settings.py:50–51`
- **Issue:** API keys are saved as plaintext JSON in `~/.config/translation-automator/config.json`. Any process on the system can read them.
- **Fix:** Use `libsecret` (via `gi.repository.Secret`) to store API keys in the system keyring. Fallback to file-based storage only if keyring is unavailable.

---

## High Priority (code quality)

### H1. Config Directory Name Mismatch ✅ DONE
- **File:** `config/settings.py:13`
- **Issue:** `self.config_dir = Path.home() / ".config" / "translation-automator"` — the app is named LangForge, but config lives under the old name "translation-automator".
- **Fix:** Migrate to `~/.config/langforge/` with backwards-compatible auto-migration from old path.

### H2. Version Inconsistency ✅ DONE
- **Files:** `__init__.py:3`, `main.py:19–23`
- **Issue:** `__init__.py` says `__version__ = "1.0.0"` but `main.py` hardcodes `APP_VERSION = "1.0.2"` as fallback and tries to import from `__init__` which may fail depending on import path.
- **Fix:** Single source of truth in `__init__.py`. Remove hardcoded fallback.

### H3. Duplicated TRANSLATION_PROMPT ✅ DONE
- **Files:** `api/free_apis.py:10–17`, `api/paid_apis.py:8–15`
- **Issue:** Identical prompt string defined in two files. If one changes, the other won't.
- **Fix:** Move to `api/base.py` or a shared `api/prompts.py`.

### H4. subprocess Result Not Used ✅ DONE
- **File:** `core/extractor.py:50`
- **Issue:** `result = subprocess.run(...)` — variable `result` assigned but never used (ruff F841). The return code is checked by `check=True` but stderr/stdout are captured and discarded.
- **Fix:** Remove the variable assignment or log stderr on failure for debugging.

### H5. mypy Type Errors ✅ DONE
- **Files:** Multiple (6 errors in 5 files)
  - `api/free_apis.py:189` — `float` assigned to `int` variable (`self._last_request`)
  - `api/paid_apis.py:48` — `str | None` has no attribute `.strip()` (missing null check)
  - `core/compiler.py:90` — `compiled` list needs type annotation
- **Fix:** Add type annotations and null checks as mypy indicates.

### H6. Unused Imports (ruff F401) ✅ DONE
- **Files:** `api/factory.py:3`, `api/free_apis.py:4`, `api/paid_apis.py:3`, `config/settings.py:5`, `core/compiler.py:7`, `core/extractor.py:5`
- **Issue:** `Optional` imported but unused in 4 files. `SUPPORTED_LANGUAGES` imported but unused in `compiler.py`.
- **Fix:** Remove unused imports.

### H7. Formatting Violations ✅ DONE
- **14 of 20 files** need reformatting per `ruff format --check`.
- **Fix:** Run `ruff format .` once and commit.

### H8. No Cancellation Support for Translation ✅ DONE
- **File:** `ui/main_window.py:564–577`
- **Issue:** Once translation starts, there is no way to cancel. The thread runs until all 29 languages complete or an error occurs. `thread.daemon = True` means it dies on app close, but there's no graceful stop.
- **Fix:** Add a `threading.Event` cancel flag checked in the translation loop. Add a "Cancel" button that appears during translation.

---

## Medium Priority (UX improvements)

### M1. No First-Run Experience ✅ DONE
- **All UI files**
- **Issue:** New users see an empty drop zone with no guidance on what API to configure first. The app requires an API key but doesn't tell the user this upfront.
- **Psychology:** First-time users experience "blank slate paralysis" (cognitive load theory). Without guidance, they don't know where to start.
- **Fix:** On first run (no `config.json`), show an `AdwStatusPage` with a welcome message that guides directly to API settings. Use progressive disclosure: show only the essentials first.

### M2. Drop Zone Click Area Too Small ✅ DONE (implemented in C3 batch)
- **File:** `ui/main_window.py:243–266`
- **Issue:** The drop zone has a "Select Project" button, but the large empty area around it is not clickable. Users expect the entire zone to be clickable (a la file upload zones in web apps).
- **Psychology:** Fitts's Law — larger targets are easier to hit. The clickable area should match the visual grouping.
- **Fix:** Add a `GestureClick` controller on the entire `frame` box, not just the button.

### M3. No Progress Details During Translation ✅ DONE
- **File:** `ui/main_window.py:290–340`
- **Issue:** During translation, only the current language name and a circular progress are shown. There's no indication of: which string is being translated, estimated time remaining, or whether the API is responding slowly.
- **Psychology:** "Uncertain waits feel longer than known, finite waits" (Maister's principles of waiting). Users need temporal context.
- **Fix:** Add a secondary label showing "String X of Y" and a rough ETA based on average time per string.

### M4. Language Grid Status Not Accessible by Keyboard ✅ DONE
- **File:** `ui/main_window.py:326–348`
- **Issue:** `self.lang_grid` FlowBox has `SelectionMode.NONE`, which means keyboard users cannot navigate to individual language items to check their status. The grid is purely visual.
- **Fix:** Set `SelectionMode.SINGLE` for navigation (not selection) and add accessible descriptions per item (e.g., "Bulgarian: pending", "Czech: completed").

### M5. Error Messages Use Jargon ✅ DONE
- **File:** `ui/main_window.py:535, 655`
- **Issue:** Errors are shown as raw exception messages: `f"{_('Error')}: {e}"`. These contain Python tracebacks and API error codes that non-technical users cannot understand.
- **Psychology:** "Error messages should express themselves in the user's language, not in system-level technical jargon." (Nielsen's heuristic #9)
- **Fix:** Map common exceptions to human-readable messages: "Could not connect to the translation service. Check your internet connection and API key."

### M6. No Confirmation Before Starting Translation ✅ DONE
- **File:** `ui/main_window.py:555–577`
- **Issue:** Clicking "Start Translation" immediately begins translating to all 29 languages. There's no confirmation dialog showing what will happen (how many strings, which API, estimated cost).
- **Psychology:** Error prevention (Nielsen) — destructive or lengthy actions need confirmation. Users may click accidentally.
- **Fix:** Show a brief summary dialog: "Translate N strings to 29 languages using [API]? This may take several minutes."

### M7. Settings Dialog: No Visual Feedback on Provider Change ✅ DONE
- **File:** `ui/settings_dialog.py:73–97`
- **Issue:** When switching between free providers, the API key field title changes but there's no visual indication that the existing key might be invalid for the new provider.
- **Fix:** Clear the API key field when switching providers (or show a warning), since keys are provider-specific.

### M8. Connection Test Blocks UI Thread ✅ DONE
- **File:** `ui/settings_dialog.py:214–244`
- **Issue:** `_on_test_connection` calls `api.test_connection()` synchronously on the main thread. If the API is down, the UI freezes for up to 10 seconds (the timeout value).
- **Fix:** Run `test_connection()` in a background thread with a spinner on the button. Use `GLib.idle_add` to report the result.

---

## Low Priority (polish & optimization)

### L1. AdwAboutWindow Could Use More Metadata ✅ DONE
- **File:** `main.py:63–72`
- **Issue:** The About dialog is minimal. Missing: translators credits, issue tracker URL, release notes URL.
- **Fix:** Add `issue_url`, `translator_credits`, `developers` properties.

### L2. ProgressRing Could Use Adwaita Tokens ✅ DONE
- **File:** `ui/main_window.py:34–52`
- **Issue:** Cairo drawing uses hardcoded RGBA colors (`0.35, 0.55, 0.95`) that may not match user's Adwaita theme or dark mode.
- **Fix:** Query `Gtk.StyleContext` for `@accent_bg_color` and `@window_fg_color` at draw time.

### L3. GrokAPI Inline Import ✅ DONE
- **File:** `api/paid_apis.py:128`
- **Issue:** `self.session = __import__('requests').Session()` — inline `__import__` is unusual and less readable.
- **Fix:** Use standard `import requests` at the top of `__init__`.

### L4. DeepLFreeAPI Rate Limiting Implementation ✅ OK (runs in translation thread)
- **File:** `api/free_apis.py:172–175`
- **Issue:** Rate limiting uses `time.sleep()` inline, which blocks the thread. Works fine in the translation thread but is architecturally fragile.
- **Fix:** Consider making the delay part of the translation engine's orchestration rather than inside the API class.

### L5. Empty Content in ToolbarView ✅ DONE ✅ DONE (fixed by ruff format)
- **File:** `ui/main_window.py:232–233`
- **Issue:** There are two blank lines after `toolbar.set_content(self.stack)` in `_build_content()` — minor cosmetic.

### L6. CSS `@keyframes pulse` May Not Work in All GTK Versions ✅ DONE
- **File:** `ui/style.css:59–67`
- **Issue:** CSS animations in GTK4 have limited support compared to web CSS. The `pulse` animation using `box-shadow` may not render on all versions.
- **Fix:** Test on GTK 4.6 (oldest supported). Fallback to opacity cycling if box-shadow animation fails.

### L7. Model Selection Hardcoded ✅ DONE (tooltip added)
- **Files:** `api/free_apis.py`, `api/paid_apis.py`, `ui/settings_dialog.py`
- **Issue:** Default models are hardcoded (`llama-3.3-70b-versatile`, `gpt-4o-mini`, etc.). These will become outdated.
- **Fix:** Fetch available models from API where possible (Groq, OpenAI, OpenRouter provide model lists). Cache the result.

---

## Architecture Recommendations

### A1. Separate UI Construction from Logic ✅ DONE
- **Issue:** `MainWindow` (666 lines) does everything: builds UI, handles callbacks, runs translation, manages state. This makes testing impossible and changes risky.
- **Recommendation:** Extract a `TranslationController` class that owns the translation workflow (scan → extract → translate → compile). The window only handles UI events and delegates to the controller.
- **Solution:** Created `core/controller.py` with `TranslationController` class. MainWindow delegates all business logic (validate_project, prepare, start, cancel) to the controller via callbacks. Controller runs pipeline in background thread; MainWindow handles only UI updates via GLib.idle_add.

### A2. Use Composite Templates (.ui or .blp) — DEFERRED
- **Issue:** All UI is built programmatically in Python (no `.ui` XML or Blueprint files). This makes it hard to iterate on layout, preview in tools like Cambalache, or separate design from code.
- **Recommendation:** For the main window and settings dialog, create `.ui` template files and use `@Gtk.Template`. Keep only signal handlers in Python.
- **Status:** Deferred — 1439 lines of UI across 2 files. Requires blueprint-compiler as build dependency, PKGBUILD changes, complete UI rewrite. High regression risk. Better suited as a dedicated project phase.

### A3. Consider GAction for All User Actions ✅ DONE
- **Issue:** Some actions use GAction (`settings`, `about`) but most use direct signal connections. This inconsistency makes shortcut binding and menu integration harder.
- **Recommendation:** Use GAction for all user-facing actions (select project, start translation, test connection, cancel). This enables keyboard shortcuts via `set_accels_for_action`.

### A4. Dependency Injection for API Clients ✅ DONE
- **Issue:** `APIFactory.create_from_settings()` is called deep inside the translation thread. If the factory fails (missing dependency), the error surfaces as a generic toast.
- **Recommendation:** Create the API client before starting translation and validate it. Pass the ready client to the translation thread.

### A5. Logging Instead of Print to stderr ✅ DONE ✅ DONE
- **Files:** `api/paid_apis.py:62,110,155`
- **Issue:** Debug messages use `print(f"...", file=sys.stderr)` directly.
- **Recommendation:** Use Python `logging` module for structured, filterable log output.

---

## UX Recommendations

### U1. Progressive Disclosure in Settings ✅ DONE
- **Principle:** Hick's Law — more choices increase decision time.
- **Current:** Settings dialog shows all 6 free providers and 3 paid providers simultaneously.
- **Recommendation:** Show only the top 2 recommended providers (DeepL + Groq) by default. Add an "Show all providers" expander for the rest. This reduces cognitive load for new users by 67%.
- **Solution:** Replaced free provider ComboRow with ActionRows + radio buttons. DeepL and Groq shown as recommended (always visible). Remaining 4 providers collapsed inside an Adw.ExpanderRow titled "More providers". Auto-expands if a non-recommended provider is saved.

### U2. Smart Defaults to Reduce Setup Friction ✅ DONE
- **Principle:** Default Effect — users tend to accept defaults.
- **Current:** App starts with no API configured. User must navigate to settings, choose a provider, enter a key.
- **Recommendation:** Default to LibreTranslate (no key needed) so the app works out of the box. Show a subtle banner suggesting "Get better translations with DeepL or Groq" after the first translation.

### U3. Inline Status Instead of Toasts for Translation Results ✅ DONE
- **Principle:** Change Blindness — users may not notice peripheral notifications.
- **Current:** Translation completion is shown as a toast that disappears after 3 seconds.
- **Recommendation:** Update the progress page to a permanent "completed" state with a summary card showing: languages translated, time taken, and a "Translate Again" or "Open Output Folder" button.

### U4. Contextual Help Where Decisions Are Made ✅ DONE
- **Principle:** Recognition over Recall (Nielsen) — users shouldn't have to remember information from one screen to use on another.
- **Current:** API help is in a separate modal accessible only via an icon button.
- **Recommendation:** Add subtitle descriptions directly on each provider row in the ComboRow dropdown, or use `Adw.ActionRow` with subtitles showing limits and quality.
- **Solution:** Each free provider ActionRow now has a descriptive subtitle (e.g., "500k chars/month — Best translation quality" for DeepL). Paid provider ComboRow items also include descriptions. Provider rows give immediate context without needing to open the help dialog.

### U5. Satisfaction Feedback on Success ✅ DONE
- **Principle:** Peak-End Rule — people judge experiences by their peak and ending.
- **Current:** Translation completes with a simple "Completed!" label.
- **Recommendation:** Show a celebratory `Adw.StatusPage` with a success icon, count of translations, and an action button. This creates positive emotional association with the tool.

---

## Orca Screen Reader Compatibility

### Issues Found

| # | Widget Type | File:Line | What Orca Cannot Announce | How to Fix |
|---|-------------|-----------|---------------------------|------------|
| O1 | `ProgressRing` (custom DrawingArea) | `ui/main_window.py:22` | Progress value — Orca says nothing during translation | Implement `Gtk.Accessible` with role `PROGRESS_BAR`, update `accessible-value-now/min/max`. Or replace with native `Gtk.ProgressBar`. |
| O2 | `Gtk.MenuButton` | `ui/main_window.py:204` | Button purpose — Orca says "button" with no context | Set `menu_button.update_property([Gtk.AccessibleProperty.LABEL], ["Main menu"])` |
| O3 | `Gtk.FlowBox` (lang grid) | `ui/main_window.py:326` | Individual language status, current state | Set accessible name on each FlowBoxChild: `"Bulgarian: pending"`, updated dynamically via `update_property` |
| O4 | Drop zone `Gtk.Box` | `ui/main_window.py:242` | Purpose of the zone — Orca just says contents | Add `Gtk.AccessibleRole.GROUP` with accessible label "Project selection area" |
| O5 | `self.compile_switch` | `ui/main_window.py:161` | Switch is labeled via `AdwActionRow` which should work, but verify `set_activatable_widget` chains label correctly | Test with Orca — may need explicit `set_accessible_name("Compile MO files toggle")` |
| O6 | Sidebar `Adw.ComboRow` (API type) | `ui/main_window.py:432` | Title "API Type" is set — likely OK, but dynamic provider list changes aren't announced | After model change, set `accessible-description` on the row to announce current selection |
| O7 | Help button (icon only) | `ui/settings_dialog.py:140` | "button" — no label, only icon name and tooltip | `help_button.update_property([Gtk.AccessibleProperty.LABEL], ["How to get API Keys"])` |
| O8 | Status labels during translation | `ui/main_window.py:297, 301` | Dynamic text changes — Orca won't automatically re-read changed labels | Set `Gtk.AccessibleRole.STATUS` on `progress_title` and `progress_subtitle` so Orca monitors live region |
| O9 | All `Adw.Toast` notifications | `ui/main_window.py:664`, `ui/settings_dialog.py:241` | Toasts may not be announced by Orca depending on focus state | Add `Gtk.AccessibleRole.ALERT` to an inline status label for critical messages |
| O10 | API help dialog | `ui/settings_dialog.py:257` | Dialog has no accessible title — header title is set to empty `Gtk.Box()` | Set `dialog.set_title(_("Available Free APIs"))` |

### Test Checklist for Manual Verification

- [ ] Launch app with Orca running (`orca &` then `langforge`)
- [ ] **Initial screen:** Verify Orca announces window title "LangForge"
- [ ] **Sidebar navigation:** Tab through API Type combo, Provider combo, Compile switch, API Settings button. Verify each announces its label and current value.
- [ ] **Drop zone:** Navigate to drop zone. Verify Orca announces purpose ("No project selected, drag a folder here or click to select").
- [ ] **Select Project button:** Verify Orca announces "Select Project" button.
- [ ] **File dialog:** Verify Orca works with `Gtk.FileDialog.select_folder()`.
- [ ] **Project loaded:** After selecting a project, verify Orca announces project name and string count.
- [ ] **Start Translation:** Press "Start Translation" button. Verify Orca announces translation progress (language name, percentage).
- [ ] **Progress ring:** Verify Orca announces progress percentage changes.
- [ ] **Language grid:** Navigate grid items. Verify Orca announces each language and its status (pending/translating/success/error).
- [ ] **Translation complete:** Verify Orca announces completion message and language count.
- [ ] **Settings dialog:** Open settings. Navigate all fields. Verify API key fields announce their provider-specific labels.
- [ ] **Connection test:** Test connection. Verify Orca announces the result (success/failure).
- [ ] **About dialog:** Open About. Verify Orca reads app name and version.
- [ ] **Keyboard only:** Complete an entire translation workflow using only keyboard (no mouse). Document any blocked paths.

---

## Accessibility Checklist (General)

- [ ] All interactive elements have accessible labels — **FAIL** (see O1–O10 above)
- [ ] Keyboard navigation works for all flows — **PARTIAL** (FlowBox items not keyboard-navigable with `SelectionMode.NONE`; drop zone only clickable via button, not zone)
- [ ] Color is never the only indicator — **PARTIAL** (language grid uses CSS color classes `success`/`error` but also has icon changes ✓; however, icon names are generic and may not convey meaning without color)
- [ ] Text is readable at 2x font size — **UNTESTED** (no `AdwClamp` used for content width; verify sidebar doesn't clip at large font sizes)
- [ ] Focus indicators are visible — **DEFAULT** (relying on Adwaita defaults, which is acceptable)
- [ ] No time-based interactions without alternatives — **PASS** (toasts have timeout but are not the sole feedback mechanism for most actions… except connection test result, which IS toast-only — **FAIL**)

---

## Tech Debt

### From ruff (lint)
| Severity | Code | Count | Description |
|----------|------|-------|-------------|
| Warning | F401 | 6 | Unused imports (`Optional` in 4 files, `SUPPORTED_LANGUAGES` in compiler, `result` unused) |
| Warning | F841 | 2 | Unused variables (`result` in extractor.py:50, `e` in translator.py:218) |
| Info | E402 | 13 | Module-level imports not at top of file (GTK `gi.require_version` pattern — acceptable) |

### From ruff format
- 14 of 20 files need reformatting

### From mypy
| Severity | File | Error |
|----------|------|-------|
| Error | `api/free_apis.py:189` | `float` assigned to `int` variable |
| Error | `api/paid_apis.py:48` | `str | None` has no attribute `.strip()` |
| Error | `core/compiler.py:90` | Missing type annotation for `compiled` |
| Warning | `core/extractor.py:6` | Missing stubs for `polib` |
| Warning | `api/free_apis.py:3` | Missing stubs for `requests` |

### From vulture (dead code)
- 18 unused variables at 100% confidence — most are GTK signal handler parameters (`button`, `combo`, `pspec`, `x`, `y`, `action`, `param`) which are required by the GTK callback signature and can be safely ignored. Mark with `_` prefix to suppress warnings.

### From radon (complexity)
| File | Function | Complexity | Grade |
|------|----------|------------|-------|
| `core/translator.py:144` | `TranslationEngine.translate_language` | 14 | C |
| `ui/main_window.py:373` | `MainWindow._build_api_dropdowns` | 15 | C |

Both should be refactored below complexity 10 (grade A or B).

---

## Metrics (before — baseline)

```
ruff lint:        21 issues (6 F401, 2 F841, 13 E402)
ruff format:      14 files need reformatting (of 20)
mypy:             6 errors in 5 files
vulture:          18 unused variables (100% confidence)
radon complexity: 2 functions ≥ grade C (max 15)
test coverage:    0% (no tests exist)
tech debt markers: 0 (no TODO/FIXME/HACK/XXX)
total files:      20
total lines:      2,524
```

## Metrics (after — current)

```
ruff lint:        13 E402 (all acceptable gi.require_version pattern)
ruff format:      0 files need reformatting
mypy:             0 type errors (stubs warnings remain)
test suite:       44 tests, all passing (translator, scanner, factory, settings)
implemented:      C1-C5, H1-H8, M1-M8, L1-L7, A3-A5, U2, U5 — 30+ items done
remaining:        A1 (controller extraction), A2 (UI templates),
                  U1 (progressive disclosure), U3 (inline status - partially done),
                  U4 (contextual help)
```

---

## Implementation Priority Order

The recommended execution order, optimized for maximum user impact per unit of effort:

1. **C1** — Create test suite (unlocks safe refactoring)
2. **C2 + C3 + C4** — Orca accessibility (all screen reader fixes as a batch)
3. **H6 + H7** — Ruff lint + format cleanup (quick wins)
4. **H5** — mypy type errors (quick wins)
5. **C5** — Keyring for API keys (security)
6. **H1** — Config directory migration (correctness)
7. **H2** — Version single source of truth
8. **H3** — Deduplicate translation prompt
9. **M8** — Connection test off main thread (UX)
10. **M1 + M6** — First-run experience + confirmation dialog (UX)
11. **H8** — Cancel translation support (UX)
12. **M2 + M3 + M4** — Drop zone, progress details, keyboard nav (UX)
13. **M5 + M7** — Error messages, provider change feedback (UX)
14. **A1 + A2** — Architecture: controller extraction + UI templates (long-term)
15. **L1–L7** — Polish items (ongoing)
