"""Módulo para extração de strings traduzíveis usando xgettext."""

import subprocess
from pathlib import Path
from typing import List, Optional
import polib


class GettextExtractor:
    """Wrapper para o comando xgettext do GNU gettext."""

    def __init__(self, project_path: str, textdomain: str):
        self.project_path = Path(project_path)
        self.textdomain = textdomain
        self.locale_dir = self.project_path / "locale"
        self.pot_file = self.locale_dir / f"{textdomain}.pot"

    def extract_strings(self, python_files: List[Path]) -> bool:
        """
        Executa xgettext para gerar arquivo .pot.

        Args:
            python_files: Lista de arquivos Python para extrair strings

        Returns:
            True se extração foi bem-sucedida
        """
        # Cria diretório locale se não existir
        self.locale_dir.mkdir(parents=True, exist_ok=True)

        if not python_files:
            raise ValueError("Nenhum arquivo Python fornecido")

        # Converte paths para strings
        file_paths = [str(f) for f in python_files]

        # Comando xgettext
        cmd = [
            "xgettext",
            "--language=Python",
            "--keyword=_",
            "--from-code=UTF-8",
            "--add-comments",
            f"--output={self.pot_file}",
            "--package-name=" + self.textdomain,
            "--msgid-bugs-address=",
        ] + file_paths

        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True
            )
            return self.pot_file.exists()
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Erro ao executar xgettext: {e.stderr}")
        except FileNotFoundError:
            raise RuntimeError("xgettext não encontrado. Instale o pacote gettext.")

    def get_extracted_strings(self) -> List[str]:
        """
        Lê o arquivo .pot e retorna lista de msgids.

        Returns:
            Lista de strings extraídas
        """
        if not self.pot_file.exists():
            return []

        try:
            pot = polib.pofile(str(self.pot_file))
            return [entry.msgid for entry in pot if entry.msgid]
        except Exception as e:
            raise RuntimeError(f"Erro ao ler .pot: {e}")

    def get_string_count(self) -> int:
        """Retorna número de strings extraídas."""
        return len(self.get_extracted_strings())
