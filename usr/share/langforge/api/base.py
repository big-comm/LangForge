"""Interface abstrata para APIs de tradução."""

from abc import ABC, abstractmethod


class TranslationAPI(ABC):
    """Classe base para todas as APIs de tradução."""

    @abstractmethod
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """
        Traduz texto entre idiomas.

        Args:
            text: Texto a ser traduzido
            source_lang: Código do idioma de origem (ex: 'en')
            target_lang: Código do idioma de destino (ex: 'pt-BR')

        Returns:
            Texto traduzido
        """
        pass

    @abstractmethod
    def test_connection(self) -> bool:
        """
        Testa se a API está acessível e funcionando.

        Returns:
            True se conectado com sucesso, False caso contrário
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """Retorna o nome da API."""
        pass
