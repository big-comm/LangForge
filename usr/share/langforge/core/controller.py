"""Translation controller — orchestrates scan → extract → translate → compile."""

import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from api.base import TranslationAPI
from api.factory import APIFactory
from config.settings import Settings
from core.compiler import MoCompiler
from core.extractor import GettextExtractor
from core.file_translator import FileTranslator, is_supported_file
from core.scanner import ProjectScanner
from core.translator import TranslationEngine

log = logging.getLogger(__name__)


class TranslationController:
    """Coordinates the full translation pipeline.

    Separates business logic from UI so the window only handles
    display and user interaction.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.is_translating: bool = False
        self._cancel_event = threading.Event()
        self._api_client: Optional[TranslationAPI] = None
        self._last_usage: dict = {}

    def _capture_usage(self) -> None:
        """Snapshot API usage stats into _last_usage and log if paid."""
        if self._api_client:
            self._last_usage = self._api_client.get_usage()
            u = self._last_usage
            if u.get("cost_usd", 0) > 0:
                log.info(
                    "API usage: $%.4f | %d tokens (%d in + %d out) | %d calls",
                    u["cost_usd"], u["total_tokens"],
                    u["input_tokens"], u["output_tokens"], u["api_calls"],
                )

    # ── Project validation ──────────────────────────────────────

    def validate_project(self, path: str) -> tuple[str, int]:
        """Validate a project directory and return (textdomain, string_count).

        Raises ValueError if the project does not use gettext.
        """
        scanner = ProjectScanner(path)
        if not scanner.validate_project():
            raise ValueError("Project does not use gettext")
        textdomain = scanner.detect_textdomain()
        strings = scanner.count_translatable_strings()
        return textdomain, strings

    # ── File validation ─────────────────────────────────────────

    @staticmethod
    def validate_file(path: str) -> tuple[str, int]:
        """Validate a single file for translation.

        Returns (filename, item_count) where item_count is a rough
        count of translatable items (entries, keys, paragraphs).
        Raises ValueError if the file type is unsupported.
        """
        p = Path(path)
        if not p.is_file():
            raise ValueError(f"Not a file: {p.name}")
        if not is_supported_file(p):
            raise ValueError(
                f"Unsupported file type: {p.suffix}. "
                "Supported: .po, .pot, .json, .txt, .md"
            )

        # Rough item count for the confirmation dialog
        import json
        import polib as _polib

        ext = p.suffix.lower()
        if ext in (".po", ".pot"):
            pot = _polib.pofile(str(p))
            count = len([e for e in pot if e.msgid])
        elif ext == ".json":
            with open(p, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            count = sum(1 for v in data.values() if isinstance(v, str) and v.strip())
        else:
            with open(p, "r", encoding="utf-8") as fh:
                content = fh.read()
            import re
            count = len([pg for pg in re.split(r"\n{2,}", content) if pg.strip()])
        return p.name, count

    # ── Translation lifecycle ───────────────────────────────────

    def prepare(self) -> None:
        """Create and validate the API client from current settings.

        Must be called (on the main thread) before ``start()``.
        Raises on failure so the UI can show a message *before*
        spawning the background thread.
        """
        self._api_client = APIFactory.create_from_settings(self.settings)

    def start(
        self,
        project_path: Path,
        *,
        languages: Optional[list[str]] = None,
        on_phase: Callable[[str], None],
        on_lang_progress: Callable[[str, str, int, int], None],
        on_complete: Callable[[dict[str, bool], float, bool], None],
        on_error: Callable[[Exception], None],
        compile_mo: bool = True,
        force_retranslate: bool = False,
    ) -> None:
        """Run the full project pipeline in a background thread.

        Callbacks are invoked **from the worker thread** — the caller
        is responsible for marshalling to the UI thread (GLib.idle_add).
        """
        self.is_translating = True
        self._cancel_event.clear()

        thread = threading.Thread(
            target=self._run,
            args=(
                project_path,
                languages,
                on_phase,
                on_lang_progress,
                on_complete,
                on_error,
                compile_mo,
                force_retranslate,
            ),
            daemon=True,
        )
        thread.start()

    def start_file(
        self,
        file_path: Path,
        *,
        languages: list[str],
        on_phase: Callable[[str], None],
        on_lang_progress: Callable[[str, str, int, int], None],
        on_complete: Callable[[dict[str, bool], float, bool], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        """Run file translation in a background thread."""
        self.is_translating = True
        self._cancel_event.clear()

        thread = threading.Thread(
            target=self._run_file,
            args=(file_path, languages, on_phase, on_lang_progress, on_complete, on_error),
            daemon=True,
        )
        thread.start()

    def cancel(self) -> None:
        """Signal the worker thread to stop at the next safe point."""
        self._cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # ── Internal workers ────────────────────────────────────────

    def _run(
        self,
        project_path: Path,
        languages: Optional[list[str]],
        on_phase: Callable[[str], None],
        on_lang_progress: Callable[[str, str, int, int], None],
        on_complete: Callable[[dict[str, bool], float, bool], None],
        on_error: Callable[[Exception], None],
        compile_mo: bool,
        force_retranslate: bool,
    ) -> None:
        try:
            on_phase("extracting")

            scanner = ProjectScanner(str(project_path))
            textdomain = scanner.detect_textdomain()
            files = scanner.find_python_files()

            extractor = GettextExtractor(str(project_path), textdomain)
            extractor.extract_strings(files)

            on_phase("translating")

            translator = TranslationEngine(self._api_client, textdomain)
            start_time = time.monotonic()

            if force_retranslate:
                # Smart context fix: detect changed translations in system
                # language, then fix only those entries in all languages
                import locale as _locale

                sys_lang = _locale.getlocale()[0] or "en"
                # Normalise: pt_BR → pt-BR
                sys_lang = sys_lang.replace("_", "-")
                log.info("Fix context: reference language = %s", sys_lang)
                on_phase("checking context")
                results = translator.fix_context(
                    extractor.pot_file,
                    project_path,
                    reference_lang=sys_lang,
                    progress_callback=on_lang_progress,
                    cancel_event=self._cancel_event,
                    languages=languages,
                )
            else:
                results = translator.translate_project(
                    extractor.pot_file,
                    project_path,
                    on_lang_progress,
                    cancel_event=self._cancel_event,
                    languages=languages,
                )

            elapsed = time.monotonic() - start_time

            if compile_mo and not self._cancel_event.is_set():
                on_phase("compiling")
                compiler = MoCompiler(project_path, textdomain)
                compiler.compile_all()

            # Capture usage BEFORE on_complete so the UI callback can read it
            self._capture_usage()

            on_complete(results, elapsed, self._cancel_event.is_set())

        except Exception as e:
            log.exception("Translation pipeline failed")
            self._capture_usage()
            on_error(e)

        finally:
            self.is_translating = False

    def _run_file(
        self,
        file_path: Path,
        languages: list[str],
        on_phase: Callable[[str], None],
        on_lang_progress: Callable[[str, str, int, int], None],
        on_complete: Callable[[dict[str, bool], float, bool], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        try:
            on_phase("translating")
            start_time = time.monotonic()

            ft = FileTranslator(self._api_client, file_path)
            results = ft.translate_all(
                target_langs=languages,
                progress_callback=on_lang_progress,
                cancel_event=self._cancel_event,
            )

            elapsed = time.monotonic() - start_time
            self._capture_usage()
            on_complete(results, elapsed, self._cancel_event.is_set())

        except Exception as e:
            log.exception("File translation failed")
            self._capture_usage()
            on_error(e)

        finally:
            self.is_translating = False
