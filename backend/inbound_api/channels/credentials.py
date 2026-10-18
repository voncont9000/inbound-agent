from cryptography.fernet import Fernet, InvalidToken

from inbound_api.settings import get_settings


def _fernet() -> Fernet:
    key = get_settings().credentials_encryption_key
    if not key:
        raise ValueError("CREDENTIALS_ENCRYPTION_KEY is not configured")
    return Fernet(key.encode())


def encrypt_credential(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_credential(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as error:
        raise ValueError("Stored credential could not be decrypted") from error
