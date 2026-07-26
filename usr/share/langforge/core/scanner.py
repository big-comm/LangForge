"""Module for scanning projects and detecting gettext usage."""

import ast
import io
import os
import re
import tokenize
from pathlib import Path
from typing import List

# Source file extensions to scan for gettext usage
_SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",  # Python, JavaScript, TypeScript
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",  # C/C++
    ".rs",  # Rust
    ".vala",
    ".ui",
    ".blp",  # Vala, GTK UI files
    ".sh",
    ".bash",  # Shell scripts
}

# Patterns that indicate gettext usage across different languages
_GETTEXT_PATTERNS = [
    # Python
    re.compile(r"import\s+gettext"),
    re.compile(r"from\s+gettext\s+import"),
    # JavaScript / GNOME Shell extensions
    re.compile(r"gettext\s+as\s+_"),
    re.compile(r"imports\.gettext"),
    re.compile(r"Gettext\.gettext"),
    re.compile(r"GLib\.dgettext"),
    # C / Vala
    re.compile(r'#\s*include\s+[<"].*gettext\.h[>"]'),
    re.compile(r"\b[dn]?gettext\s*\("),
    re.compile(r"\bN?_\s*\("),
    # Shell
    re.compile(r"\$\(\s*gettext\b"),
    re.compile(r"eval_gettext"),
    # Generic _() usage (common across all)
    re.compile(r'_\s*\(\s*["\']'),
]

# Patterns for detecting textdomain declarations across languages
_TEXTDOMAIN_PATTERNS = [
    # Python
    re.compile(
        r"gettext\.(?:textdomain|bindtextdomain|translation|install)"
        r'\s*\(\s*["\']([^"\']+)["\']'
    ),
    # C / Vala
    re.compile(r'\b(?:bind)?textdomain\s*\(\s*["\']([^"\']+)["\']'),
    re.compile(r'\bGETTEXT_PACKAGE\s*(?:=\s*)?["\']([^"\']+)["\']'),
    re.compile(r'\bset\s*\(\s*GETTEXT_PACKAGE\s+["\']([^"\']+)["\']'),
    # JavaScript: Extension metadata or GLib.textdomain
    re.compile(r'\btextdomain\s*[=:]\s*["\']([^"\']+)["\']'),
    re.compile(r'["\']gettext-domain["\']\s*:\s*["\']([^"\']+)["\']'),
    # Meson
    re.compile(r'i18n\.gettext\s*\(\s*["\']([^"\']+)["\']'),
    # Shell: TEXTDOMAIN=name
    re.compile(r'\bTEXTDOMAIN\s*=\s*["\']?([^\s"\']+)'),
]

# Generated, vendored, cached, and VCS-owned directories.
_SKIP_DIRS = {
    ".bzr",
    ".eggs",
    ".git",
    ".gradle",
    ".hg",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "_build",
    "_darcs",
    "bower_components",
    "build",
    "deps",
    "dist",
    "node_modules",
    "out",
    "target",
    "third-party",
    "third_party",
    "vendor",
    "venv",
}
_SKIP_DIR_PREFIXES = ("cmake-build-",)

# Build and metadata files that can explicitly declare a gettext domain.
_TEXTDOMAIN_FILENAMES = {
    "CMakeLists.txt",
    "Makefile",
    "Makefile.am",
    "Makefile.in",
    "configure.ac",
    "configure.in",
    "meson.build",
    "metadata.json",
}
_PYTHON_GETTEXT_CALLS = {
    "bindtextdomain",
    "install",
    "textdomain",
    "translation",
}
_PYTHON_DOMAIN_NAMES = {"GETTEXT_PACKAGE", "TEXTDOMAIN"}
_C_LIKE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".js",
    ".jsx",
    ".rs",
    ".ts",
    ".tsx",
    ".vala",
    ".blp",
}
_HASH_COMMENT_SUFFIXES = {".bash", ".desktop", ".sh"}
_HASH_COMMENT_FILENAMES = {
    "CMakeLists.txt",
    "Makefile",
    "Makefile.am",
    "Makefile.in",
    "configure.ac",
    "configure.in",
    "meson.build",
}
_C_LIKE_COMMENTS = re.compile(
    r'(?P<string>"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')'
    r"|(?P<comment>//[^\n]*|/\*.*?\*/)",
    re.DOTALL,
)
_HASH_COMMENTS = re.compile(
    r'(?P<string>"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')'
    r"|(?P<comment>#[^\n]*)",
)
_XML_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)


def _python_fallback_tokens(content: str) -> list[tokenize.TokenInfo]:
    """Tokenize Python that the running interpreter cannot parse."""
    ignored = {
        tokenize.COMMENT,
        tokenize.ENDMARKER,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.NL,
        tokenize.NEWLINE,
    }
    try:
        return [
            token
            for token in tokenize.generate_tokens(io.StringIO(content).readline)
            if token.type not in ignored
        ]
    except (IndentationError, tokenize.TokenError):
        return []


def _python_literal(token: tokenize.TokenInfo) -> str | None:
    """Decode one string token without evaluating arbitrary code."""
    if token.type != tokenize.STRING:
        return None
    try:
        value = ast.literal_eval(token.string)
    except (SyntaxError, ValueError):
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeError:
            return None
    return value if isinstance(value, str) else None


def _call_literal_argument(
    tokens: list[tokenize.TokenInfo],
    opening_index: int,
    *,
    positional_limit: int,
) -> str | None:
    """Return a top-level literal argument from one tokenized call."""
    depth = 1
    argument = 0
    index = opening_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token.type == tokenize.OP:
            if token.string in "([{":
                depth += 1
            elif token.string in ")]}":
                depth -= 1
                if depth == 0:
                    return None
            elif token.string == "," and depth == 1:
                argument += 1

        if depth == 1:
            value = _python_literal(token)
            if value is not None and argument < positional_limit:
                return value
            if (
                token.type == tokenize.NAME
                and token.string == "domain"
                and index + 2 < len(tokens)
                and tokens[index + 1].string == "="
            ):
                value = _python_literal(tokens[index + 2])
                if value is not None:
                    return value
        index += 1
    return None


def _python_fallback_imports(
    tokens: list[tokenize.TokenInfo],
) -> tuple[bool, set[str], set[str]]:
    """Collect simple gettext imports from tokenized legacy Python."""
    imported = False
    modules = {"gettext"}
    functions: set[str] = set()

    for index, token in enumerate(tokens):
        if token.type != tokenize.NAME:
            continue
        if (
            token.string == "import"
            and index + 1 < len(tokens)
            and tokens[index + 1].string == "gettext"
        ):
            imported = True
            if (
                index + 3 < len(tokens)
                and tokens[index + 2].string == "as"
                and tokens[index + 3].type == tokenize.NAME
            ):
                modules.add(tokens[index + 3].string)
            continue
        if not (
            token.string == "from"
            and index + 2 < len(tokens)
            and tokens[index + 1].string == "gettext"
            and tokens[index + 2].string == "import"
        ):
            continue

        imported = True
        line = token.start[0]
        cursor = index + 3
        while cursor < len(tokens) and tokens[cursor].start[0] == line:
            imported_name = tokens[cursor].string
            if imported_name == "*":
                functions.update(_PYTHON_GETTEXT_CALLS)
            elif imported_name in _PYTHON_GETTEXT_CALLS:
                alias = imported_name
                if (
                    cursor + 2 < len(tokens)
                    and tokens[cursor + 1].string == "as"
                    and tokens[cursor + 2].type == tokenize.NAME
                ):
                    alias = tokens[cursor + 2].string
                    cursor += 2
                functions.add(alias)
            cursor += 1
    return imported, modules, functions


def _python_fallback_gettext_usage(content: str) -> tuple[bool, int]:
    """Detect gettext calls in tokenizable legacy Python syntax."""
    tokens = _python_fallback_tokens(content)
    imported, _modules, _functions = _python_fallback_imports(tokens)
    function_names = {"_", "N_", "gettext", "ngettext", "pgettext"}
    attribute_names = {"dgettext", "gettext", "ngettext", "pgettext"}
    count = 0

    for index, token in enumerate(tokens):
        if token.type != tokenize.NAME:
            continue
        direct_call = (
            token.string in function_names
            and index + 1 < len(tokens)
            and tokens[index + 1].string == "("
        )
        attribute_call = (
            token.string in attribute_names
            and index > 0
            and tokens[index - 1].string == "."
            and index + 1 < len(tokens)
            and tokens[index + 1].string == "("
        )
        if (direct_call or attribute_call) and _call_literal_argument(
            tokens,
            index + 1,
            positional_limit=2,
        ) is not None:
            count += 1
    return imported or count > 0, count


def _python_fallback_textdomains(content: str) -> set[str]:
    """Extract declared domains from tokenizable legacy Python syntax."""
    tokens = _python_fallback_tokens(content)
    _imported, modules, functions = _python_fallback_imports(tokens)
    textdomains: set[str] = set()
    depth = 0

    for index, token in enumerate(tokens):
        at_top_level = depth == 0
        if (
            at_top_level
            and token.type == tokenize.NAME
            and token.string in _PYTHON_DOMAIN_NAMES
            and index + 2 < len(tokens)
            and tokens[index + 1].string == "="
        ):
            value = _python_literal(tokens[index + 2])
            if value is not None:
                textdomains.add(value)

        opening_index: int | None = None
        if (
            token.type == tokenize.NAME
            and token.string in functions
            and index + 1 < len(tokens)
            and tokens[index + 1].string == "("
        ):
            opening_index = index + 1
        elif (
            token.type == tokenize.NAME
            and token.string in _PYTHON_GETTEXT_CALLS
            and index >= 2
            and tokens[index - 1].string == "."
            and tokens[index - 2].string in modules
            and index + 1 < len(tokens)
            and tokens[index + 1].string == "("
        ):
            opening_index = index + 1
        if opening_index is not None:
            value = _call_literal_argument(
                tokens,
                opening_index,
                positional_limit=1,
            )
            if value is not None:
                textdomains.add(value)

        if token.type == tokenize.OP:
            if token.string in "([{":
                depth += 1
            elif token.string in ")]}":
                depth = max(0, depth - 1)
    return textdomains


class ProjectScanner:
    """Project scanner with multi-language gettext detection."""

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)

    def _is_excluded(self, path: Path) -> bool:
        """Check if path is inside a build/cache directory."""
        for part in path.parts:
            normalized = part.casefold()
            if normalized in _SKIP_DIRS or normalized.startswith(_SKIP_DIR_PREFIXES):
                return True
        return False

    def _project_files(self) -> List[Path]:
        """Return safe, deterministic project files without following directory links."""
        if not self.project_path.exists():
            raise FileNotFoundError(f"Directory not found: {self.project_path}")

        project_root = self.project_path.resolve()
        files = []
        seen_targets = set()

        for dirpath, dirnames, filenames in os.walk(
            self.project_path, topdown=True, followlinks=False
        ):
            directory = Path(dirpath)
            try:
                resolved_directory = directory.resolve(strict=True)
                resolved_directory.relative_to(project_root)
            except (OSError, RuntimeError, ValueError):
                dirnames[:] = []
                continue

            relative_directory = directory.relative_to(self.project_path)
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if not self._is_excluded(relative_directory / name)
                and not (directory / name).is_symlink()
            ]

            for name in sorted(filenames):
                candidate = directory / name
                if self._is_excluded(relative_directory / name):
                    continue
                try:
                    target = candidate.resolve(strict=True)
                    target_relative = target.relative_to(project_root)
                except (OSError, RuntimeError, ValueError):
                    continue
                if (
                    self._is_excluded(target_relative)
                    or not target.is_file()
                    or target in seen_targets
                ):
                    continue
                seen_targets.add(target)
                files.append(candidate)

        return sorted(
            files,
            key=lambda path: path.relative_to(self.project_path).as_posix(),
        )

    def find_source_files(self) -> List[Path]:
        """Find all source files that could use gettext."""
        return [
            path for path in self._project_files() if path.suffix in _SOURCE_EXTENSIONS
        ]

    def find_python_files(self) -> List[Path]:
        """Find all .py files recursively (kept for compatibility)."""
        return [path for path in self._project_files() if path.suffix == ".py"]

    def _find_pot_files(self) -> List[Path]:
        """Find valid .pot template files in the project."""
        return [
            path
            for path in self._project_files()
            if path.suffix == ".pot"
            and path.stem
            and path.stem != "."
            and not path.stem.startswith(".")
        ]

    def _find_po_files(self) -> List[Path]:
        """Find .po translation files in the project."""
        return [path for path in self._project_files() if path.suffix == ".po"]

    @staticmethod
    def _valid_textdomain(textdomain: str) -> bool:
        """Return whether a detected domain is safe to use as a filename."""
        return bool(
            textdomain
            and not textdomain.startswith(".")
            and "/" not in textdomain
            and "\\" not in textdomain
            and "\0" not in textdomain
        )

    @staticmethod
    def _python_declared_textdomains(content: str) -> set[str]:
        """Extract real Python declarations without matching comments or strings."""
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError):
            return _python_fallback_textdomains(content)

        gettext_modules = {"gettext"}
        gettext_functions = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    if name.name == "gettext":
                        gettext_modules.add(name.asname or name.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "gettext":
                for name in node.names:
                    if name.name == "*":
                        gettext_functions.update(_PYTHON_GETTEXT_CALLS)
                    elif name.name in _PYTHON_GETTEXT_CALLS:
                        gettext_functions.add(name.asname or name.name)

        textdomains = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if any(
                    isinstance(target, ast.Name) and target.id in _PYTHON_DOMAIN_NAMES
                    for target in targets
                ):
                    value = node.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        textdomains.add(value.value)
                continue

            if not isinstance(node, ast.Call):
                continue
            function = node.func
            is_gettext_call = (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id in gettext_modules
                and function.attr in _PYTHON_GETTEXT_CALLS
            ) or (isinstance(function, ast.Name) and function.id in gettext_functions)
            if not is_gettext_call:
                continue

            domain_arg = node.args[0] if node.args else None
            if domain_arg is None:
                domain_arg = next(
                    (
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg == "domain"
                    ),
                    None,
                )
            if isinstance(domain_arg, ast.Constant) and isinstance(
                domain_arg.value, str
            ):
                textdomains.add(domain_arg.value)

        return textdomains

    @staticmethod
    def _python_gettext_usage(content: str) -> tuple[bool, int]:
        """Return whether Python uses gettext and its approximate call count."""
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError):
            return _python_fallback_gettext_usage(content)

        imported = False
        count = 0
        function_names = {"_", "N_", "gettext", "ngettext", "pgettext"}
        attribute_names = {
            "dgettext",
            "gettext",
            "ngettext",
            "pgettext",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = imported or any(
                    name.name == "gettext" for name in node.names
                )
                continue
            if isinstance(node, ast.ImportFrom) and node.module == "gettext":
                imported = True
                continue
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            recognized = (
                isinstance(function, ast.Name) and function.id in function_names
            ) or (
                isinstance(function, ast.Attribute)
                and function.attr in attribute_names
            )
            if recognized and any(
                isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
                for argument in node.args[:2]
            ):
                count += 1
        return imported or count > 0, count

    @staticmethod
    def _without_comments(path: Path, content: str) -> str:
        """Remove language comments while retaining quoted declaration values."""

        def replace_comment(match: re.Match) -> str:
            if match.group("string") is not None:
                return match.group()
            return "".join("\n" if char == "\n" else " " for char in match.group())

        suffix = path.suffix.lower()
        if suffix in _C_LIKE_SUFFIXES:
            return _C_LIKE_COMMENTS.sub(replace_comment, content)
        if suffix == ".ui":
            return _XML_COMMENTS.sub(
                lambda match: "\n" * match.group().count("\n"),
                content,
            )
        if suffix in _HASH_COMMENT_SUFFIXES or path.name in _HASH_COMMENT_FILENAMES:
            content = _HASH_COMMENTS.sub(replace_comment, content)
            if path.name in {"configure.ac", "configure.in"}:
                content = re.sub(r"(?m)^\s*dnl\b.*$", "", content)
        return content

    def _declared_textdomains(self) -> List[str]:
        """Collect explicit gettext declarations in deterministic order."""
        textdomains = set()
        for path in self._project_files():
            if not (
                path.suffix in _SOURCE_EXTENSIONS
                or path.name in _TEXTDOMAIN_FILENAMES
                or path.suffix == ".desktop"
                or path.name.endswith(".desktop.in")
            ):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if path.suffix == ".py":
                for textdomain in self._python_declared_textdomains(content):
                    if self._valid_textdomain(textdomain):
                        textdomains.add(textdomain)
                continue
            content = self._without_comments(path, content)
            for pattern in _TEXTDOMAIN_PATTERNS:
                for match in pattern.finditer(content):
                    textdomain = match.group(1).strip()
                    if self._valid_textdomain(textdomain):
                        textdomains.add(textdomain)
        return sorted(textdomains)

    def detect_textdomain(self) -> str:
        """Detect the project's textdomain name.

        Checks (in priority order):
        1. Explicit textdomain declarations
        2. An unambiguous .pot filename
        3. Directory name as fallback
        """
        declared = self._declared_textdomains()
        if len(declared) == 1:
            return declared[0]
        if len(declared) > 1:
            choices = ", ".join(declared)
            raise ValueError(f"Conflicting gettext textdomains: {choices}")

        pot_textdomains = sorted({path.stem for path in self._find_pot_files()})
        if len(pot_textdomains) == 1:
            return pot_textdomains[0]
        if len(pot_textdomains) > 1:
            choices = ", ".join(pot_textdomains)
            raise ValueError(
                f"Multiple gettext templates found ({choices}); "
                "declare the project textdomain explicitly"
            )

        # Fallback: directory name
        return self.project_path.name

    def validate_project(self) -> bool:
        """Check if this is a valid project with gettext.

        Detection strategy (any match is sufficient):
        1. .pot file exists (strongest signal)
        2. .po files exist
        3. Source files contain gettext patterns
        """
        # Check for .pot or .po files first (strongest indicator)
        if self._find_pot_files() or self._find_po_files():
            return True

        # Check source files for gettext patterns
        for src_file in self.find_source_files():
            try:
                content = src_file.read_text(encoding="utf-8")
                if src_file.suffix == ".py":
                    uses_gettext, _count = self._python_gettext_usage(content)
                    if uses_gettext:
                        return True
                    continue
                content = self._without_comments(src_file, content)
                if any(p.search(content) for p in _GETTEXT_PATTERNS):
                    return True
            except Exception:
                continue

        return False

    def count_translatable_strings(self) -> int:
        """Count approximately how many strings are translatable.

        If a .pot file exists, count its entries for accuracy.
        Otherwise, scan source files for _() patterns.
        """
        # Prefer .pot count (most accurate)
        pot_files = self._find_pot_files()
        if pot_files:
            textdomain = self.detect_textdomain()
            for pot_file in (path for path in pot_files if path.stem == textdomain):
                try:
                    import polib

                    po = polib.pofile(str(pot_file))
                    return len([e for e in po if not e.obsolete])
                except Exception:
                    continue

        # Fallback: regex count across source files
        pattern = re.compile(r'_\s*\(\s*["\']([^"\']+)["\']\s*\)')
        count = 0
        for src_file in self.find_source_files():
            try:
                content = src_file.read_text(encoding="utf-8")
                if src_file.suffix == ".py":
                    _uses_gettext, python_count = self._python_gettext_usage(content)
                    count += python_count
                    continue
                content = self._without_comments(src_file, content)
                count += len(pattern.findall(content))
            except Exception:
                continue
        return count
