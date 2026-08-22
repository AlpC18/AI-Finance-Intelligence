"""Automated envelope-encryption rotation for stored broker credentials.

After prepending a new primary key to ENCRYPTION_KEY (keeping the old key for
decryption), call `rotate_credentials` to re-encrypt every stored secret under
the new primary — no manual re-encryption, no downtime. Idempotent: re-running
simply rotates already-current ciphertext to itself.
"""
from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.core.crypto import rotate_token
from app.models.broker import BrokerCredential

logger = logging.getLogger("key_rotation")


class KeyRotationService:
    def rotate_credentials(self, session: Session) -> int:
        """Re-encrypt all broker credentials to the current primary key.

        Returns the number of credential rows rotated.
        """
        rows = list(session.exec(select(BrokerCredential)).all())
        for row in rows:
            row.api_key_enc = rotate_token(row.api_key_enc)
            row.api_secret_enc = rotate_token(row.api_secret_enc)
            session.add(row)
        session.commit()
        logger.info("Rotated encryption for %s broker credential(s).", len(rows))
        return len(rows)
