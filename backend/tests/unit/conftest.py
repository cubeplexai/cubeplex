import pytest
from cryptography.fernet import Fernet

from cubebox.credentials.encryption import FernetBackend


@pytest.fixture
def mock_encryption_backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key()])
