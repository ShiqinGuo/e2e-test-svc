"""Prepare local service credentials. Values stay in ignored local files."""

import secrets
from pathlib import Path

root = Path(__file__).resolve().parent.parent
data = root / "data"
data.mkdir(exist_ok=True)
password_file = data / "postgres.password"
if not password_file.exists():
    password_file.write_text(secrets.token_urlsafe(36), encoding="utf8")
    password_file.chmod(0o600)
env = root / ".env"
if not env.exists():
    env.write_text(
        f"E2E_DATABASE_URL=postgresql+asyncpg://e2e:{password_file.read_text().strip()}@127.0.0.1:55432/e2e\n",
        encoding="utf8",
    )
    env.chmod(0o600)
print("Local configuration ready; credentials stored only in ignored files.")
