"""Fernet key rotation: MultiFernet versioning + credential re-encryption."""
import pytest
from cryptography.fernet import Fernet, MultiFernet
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.core import crypto
from app.core.config import Settings
from app.core.crypto import decrypt, encrypt, rotate_token
from app.models.broker import BrokerCredential
from app.services.key_rotation_service import KeyRotationService


@pytest.fixture
def keys():
    a, b = Fernet.generate_key(), Fernet.generate_key()
    yield a, b
    crypto.reset_cipher()  # restore settings-derived cipher for other tests


def test_multifernet_rotation_roundtrip(keys):
    a, b = keys
    crypto._install_cipher(MultiFernet([Fernet(a)]))
    token = encrypt("secret")

    crypto._install_cipher(MultiFernet([Fernet(b), Fernet(a)]))  # b primary, a retired
    assert decrypt(token) == "secret"  # old token still decrypts
    rotated = rotate_token(token)
    assert rotated != token and decrypt(rotated) == "secret"

    crypto._install_cipher(MultiFernet([Fernet(b)]))  # retire the old key entirely
    assert decrypt(rotated) == "secret"


def test_rotate_credentials_reencrypts_to_new_primary(keys):
    a, b = keys
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    crypto._install_cipher(MultiFernet([Fernet(a)]))
    with Session(engine) as s:
        s.add(BrokerCredential(user_id=1, broker="alpaca",
                               api_key_enc=encrypt("KEY123"), api_secret_enc=encrypt("SEC456")))
        s.commit()
        old_ct = s.exec(select(BrokerCredential)).first().api_key_enc

    crypto._install_cipher(MultiFernet([Fernet(b), Fernet(a)]))  # rotate keys
    with Session(engine) as s:
        assert KeyRotationService().rotate_credentials(s) == 1

    crypto._install_cipher(MultiFernet([Fernet(b)]))  # drop old key -> proves re-encryption
    with Session(engine) as s:
        row = s.exec(select(BrokerCredential)).first()
        assert row.api_key_enc != old_ct
        assert decrypt(row.api_key_enc) == "KEY123"
        assert decrypt(row.api_secret_enc) == "SEC456"


def test_production_accepts_multiple_valid_keys():
    a = Fernet.generate_key().decode("utf-8")
    b = Fernet.generate_key().decode("utf-8")
    errs = Settings(
        environment="production", jwt_secret="x" * 40, anthropic_api_key="sk-real",
        cache_backend="redis", encryption_key=f"{a},{b}",
    ).production_config_errors()
    assert errs == []
