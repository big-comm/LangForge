"""Tests for controller lifecycle and compilation result handling."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import core.controller as controller_module
from core.controller import TranslationController


def _callbacks():
    return {
        "on_phase": lambda *_args: None,
        "on_lang_progress": lambda *_args: None,
        "on_complete": lambda *_args: None,
        "on_error": lambda *_args: None,
    }


class BlockingController(TranslationController):
    """Controller whose workers remain active until released by the test."""

    def __init__(self):
        super().__init__(settings=None)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.worker_count = 0
        self.worker_count_lock = threading.Lock()

    def _block_worker(self):
        with self.worker_count_lock:
            self.worker_count += 1
        self.entered.set()
        try:
            self.release.wait(timeout=5)
        finally:
            self._finish_run()

    def _run(self, *_args):
        self._block_worker()

    def _run_file(self, *_args):
        self._block_worker()


class UsageAPI:
    def get_usage(self):
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0,
            "api_calls": 0,
        }


class FakeScanner:
    def __init__(self, project_path):
        self.project_path = project_path

    def detect_textdomain(self):
        return "app"

    def find_source_files(self):
        return []


class FakeExtractor:
    def __init__(self, project_path, textdomain):
        self.pot_file = Path(project_path) / "locale" / f"{textdomain}.pot"

    def extract_strings(self, files):
        return True


class FakeTranslationEngine:
    results = {
        "fr": True,
        "de": True,
        "pt-BR": True,
        "es": False,
    }

    def __init__(self, api_client, textdomain):
        pass

    def translate_project(self, *_args, **_kwargs):
        return dict(self.results)


class FakeCompiler:
    def __init__(self, project_path, textdomain):
        pass

    def compile_all(self, progress_callback=None):
        statuses = [
            ("fr", "compiled", True),
            ("de", "error: invalid catalog", False),
            ("pt_BR", "error: plural mismatch", False),
            ("es", "compiled", True),
        ]
        for current, (lang, status, _success) in enumerate(statuses, start=1):
            progress_callback(lang, status, current, len(statuses))
        return {lang: success for lang, _status, success in statuses}


def test_start_and_start_file_are_mutually_exclusive_and_keep_cancelled_state():
    controller = BlockingController()
    barrier = threading.Barrier(3)

    def start_project():
        barrier.wait()
        try:
            controller.start(Path("/tmp/project"), **_callbacks())
            return "started"
        except RuntimeError:
            return "busy"

    def start_file():
        barrier.wait()
        try:
            controller.start_file(
                Path("/tmp/file.json"),
                languages=["fr"],
                **_callbacks(),
            )
            return "started"
        except RuntimeError:
            return "busy"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(start_project),
            executor.submit(start_file),
        ]
        barrier.wait()
        outcomes = [future.result(timeout=2) for future in futures]

    assert sorted(outcomes) == ["busy", "started"]
    assert controller.entered.wait(timeout=1)
    assert controller.worker_count == 1
    assert controller.is_translating is True

    controller.cancel()
    with pytest.raises(RuntimeError, match="already in progress"):
        controller.start(Path("/tmp/other"), **_callbacks())
    assert controller.cancelled is True
    assert controller.worker_count == 1

    controller.release.set()
    deadline = time.monotonic() + 2
    while controller.is_translating and time.monotonic() < deadline:
        time.sleep(0.01)
    assert controller.is_translating is False


def test_thread_start_failure_releases_controller(monkeypatch):
    controller = TranslationController(settings=None)

    class BrokenThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise OSError("cannot start worker")

    monkeypatch.setattr(controller_module.threading, "Thread", BrokenThread)

    with pytest.raises(OSError, match="cannot start worker"):
        controller.start(Path("/tmp/project"), **_callbacks())

    assert controller.is_translating is False


def test_compile_failures_override_translation_success(monkeypatch, tmp_path):
    monkeypatch.setattr(controller_module, "ProjectScanner", FakeScanner)
    monkeypatch.setattr(controller_module, "GettextExtractor", FakeExtractor)
    monkeypatch.setattr(controller_module, "TranslationEngine", FakeTranslationEngine)
    monkeypatch.setattr(controller_module, "MoCompiler", FakeCompiler)
    controller = TranslationController(settings=None)
    controller._api_client = UsageAPI()
    phases = []
    progress = []
    completed = []
    errors = []

    controller._run(
        tmp_path,
        ["fr", "de", "pt-BR", "es"],
        phases.append,
        lambda *args: progress.append(args),
        lambda results, elapsed, cancelled: completed.append(
            (dict(results), elapsed, cancelled)
        ),
        errors.append,
        True,
        False,
    )

    assert errors == []
    assert phases == ["extracting", "translating", "compiling"]
    assert len(completed) == 1
    results, elapsed, cancelled = completed[0]
    assert results == {
        "fr": True,
        "de": False,
        "pt-BR": False,
        "es": False,
    }
    assert elapsed >= 0
    assert cancelled is False
    assert progress == [
        ("de", "error: invalid catalog", 2, 4),
        ("pt-BR", "error: plural mismatch", 3, 4),
    ]


def test_fix_context_rejects_provider_without_context_before_extraction(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        controller_module,
        "ProjectScanner",
        lambda _path: pytest.fail("project must not be scanned"),
    )
    controller = TranslationController(settings=None)
    controller._api_client = UsageAPI()
    phases = []
    completed = []
    errors = []

    controller._run(
        tmp_path,
        ["fr"],
        phases.append,
        lambda *_args: None,
        lambda *args: completed.append(args),
        errors.append,
        False,
        True,
    )

    assert phases == []
    assert completed == []
    assert len(errors) == 1
    assert "requires an LLM provider" in str(errors[0])


def test_usage_failure_does_not_mask_success(monkeypatch, tmp_path):
    class BrokenUsageAPI:
        def get_usage(self):
            raise RuntimeError("usage endpoint unavailable")

    monkeypatch.setattr(controller_module, "ProjectScanner", FakeScanner)
    monkeypatch.setattr(controller_module, "GettextExtractor", FakeExtractor)
    monkeypatch.setattr(
        controller_module,
        "TranslationEngine",
        FakeTranslationEngine,
    )
    controller = TranslationController(settings=None)
    controller._api_client = BrokenUsageAPI()
    completed = []
    errors = []

    controller._run(
        tmp_path,
        ["fr"],
        lambda *_args: None,
        lambda *_args: None,
        lambda results, elapsed, cancelled: completed.append(
            (results, elapsed, cancelled)
        ),
        errors.append,
        False,
        False,
    )

    assert errors == []
    assert len(completed) == 1


def test_validate_nested_json_counts_every_string(tmp_path):
    source = tmp_path / "labels.json"
    source.write_text(
        json.dumps(
            {
                "title": "Title",
                "menu": {"open": "Open"},
                "items": ["First", 2, None, ""],
            }
        ),
        encoding="utf-8",
    )

    filename, count = TranslationController.validate_file(str(source))

    assert filename == "labels.json"
    assert count == 3


def test_validate_json_rejects_scalar_root(tmp_path):
    source = tmp_path / "labels.json"
    source.write_text('"single string"', encoding="utf-8")

    with pytest.raises(ValueError, match="object or array"):
        TranslationController.validate_file(str(source))
