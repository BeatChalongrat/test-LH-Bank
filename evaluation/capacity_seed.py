"""Provision mock identities only in the isolated capacity database."""

import hashlib
import hmac
import json
import os
import secrets

from knowledge.db import ROOT, STATE, accounts, engine_for, initialize, insert_for
from knowledge.encoder import Encoder
from knowledge.ingest import ingest


def main():
    engine = engine_for()
    if engine.url.database != "capacity":
        raise SystemExit("Capacity seed requires a database named capacity")
    initialize(engine)
    print(ingest(engine, Encoder(), ROOT / "data/catalog.json"))
    secret_file = STATE / "accounts.secret"
    if not secret_file.exists():
        with os.fdopen(
            os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
        ) as f:
            f.write(secrets.token_bytes(32))
    secret = secret_file.read_bytes()
    rows, identities = [], []
    for i in range(5000):
        name = f"load-{i:04}"
        role = (
            "executive"
            if i % 100 == 99
            else "reviewer"
            if i % 100 >= 97
            else "employee"
        )
        token = "ks_" + hmac.new(secret, name.encode(), "sha256").hexdigest()
        rows.append(
            dict(
                subject=name,
                role=role,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
            )
        )
        identities.append([role, token])
    with engine.begin() as conn:
        insert = insert_for(conn, accounts)
        conn.execute(
            insert.on_conflict_do_update(
                index_elements=["subject"],
                set_={
                    "role": insert.excluded.role,
                    "token_hash": insert.excluded.token_hash,
                },
            ),
            rows,
        )
    path = STATE / "identities.json"
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
    ) as f:
        json.dump(identities, f)
    print("Provisioned 5,000 benchmark identities")


if __name__ == "__main__":
    main()
