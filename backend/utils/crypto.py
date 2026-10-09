from cryptography.fernet import Fernet
import base64

# Deterministic key derived from secret key
PLATFORM_SECRET = "supersecretkeyforplatformdevelopmentonly"
key_bytes = PLATFORM_SECRET.encode("utf-8").ljust(32, b'\0')[:32]
FERNET_KEY = base64.urlsafe_b64encode(key_bytes)
cipher = Fernet(FERNET_KEY)

def encrypt_value(value: str | None) -> str | None:
    if not value:
        return None
    return cipher.encrypt(value.encode("utf-8")).decode("utf-8")

def decrypt_value(enc_value: str | None) -> str | None:
    if not enc_value:
        return None
    try:
        return cipher.decrypt(enc_value.encode("utf-8")).decode("utf-8")
    except Exception:
        return None
