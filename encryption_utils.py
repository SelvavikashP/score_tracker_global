import os
import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from sqlalchemy.types import TypeDecorator, String
from config import Config

_fernet_instance = None

def get_master_secret():
    """Returns a stable master secret key from env or persistent file."""
    env_secret = os.environ.get('DATA_ENCRYPTION_KEY') or os.environ.get('SECRET_KEY')
    if env_secret:
        return env_secret.encode('utf-8')
    
    key_file = os.path.join(Config.BASE_DIR, 'User_data', '.secret_key')
    if os.path.exists(key_file):
        try:
            with open(key_file, 'rb') as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception:
            pass
            
    # Generate and persist stable secret key
    new_secret = Fernet.generate_key()
    try:
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        with open(key_file, 'wb') as f:
            f.write(new_secret)
    except Exception:
        pass
    return new_secret

def get_fernet_cipher():
    """
    Initializes a deterministic 256-bit Fernet cipher derived from the persistent master key.
    """
    global _fernet_instance
    if _fernet_instance is None:
        secret = get_master_secret()
        salt = b"score_tracker_deterministic_salt_v1"
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
        )
        derived_key = base64.urlsafe_b64encode(kdf.derive(secret))
        _fernet_instance = Fernet(derived_key)
    return _fernet_instance

def encrypt_field(plaintext: str) -> str:
    """Encrypts plaintext string into an AES-128-CBC + HMAC-SHA256 authenticated Fernet token."""
    if plaintext is None:
        return None
    if not isinstance(plaintext, str):
        plaintext = str(plaintext)
    if plaintext == "":
        return ""
    # Avoid re-encrypting if already a valid fernet token
    if plaintext.startswith("enc::"):
        return plaintext
    
    cipher = get_fernet_cipher()
    encrypted_bytes = cipher.encrypt(plaintext.encode('utf-8'))
    return f"enc::{encrypted_bytes.decode('utf-8')}"

def decrypt_field(ciphertext: str) -> str:
    """Decrypts ciphertext string. If plaintext/legacy data is encountered, returns it safely."""
    if ciphertext is None:
        return None
    if not isinstance(ciphertext, str):
        return str(ciphertext)
    if not ciphertext.startswith("enc::"):
        # Legacy or unencrypted string
        return ciphertext
    
    token = ciphertext[5:]
    try:
        cipher = get_fernet_cipher()
        decrypted_bytes = cipher.decrypt(token.encode('utf-8'))
        return decrypted_bytes.decode('utf-8')
    except (InvalidToken, Exception):
        # Fallback if decryption fails (e.g. key mismatch or corrupted token)
        return ciphertext

class EncryptedString(TypeDecorator):
    """
    SQLAlchemy TypeDecorator that transparently encrypts data on write
    and decrypts data on read.
    """
    impl = String
    cache_ok = True

    def __init__(self, length=255, **kwargs):
        super().__init__(length=length, **kwargs)

    def process_bind_param(self, value, dialect):
        if value is not None:
            return encrypt_field(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return decrypt_field(value)
        return value
