import json
import re
from pathlib import Path

from cryptography.fernet import Fernet


class Vault:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        filename = data_dir / "encryption.key"
        if not filename.exists():
            with filename.open("xb") as stream:
                stream.write(Fernet.generate_key())
            filename.chmod(0o600)
        self.cipher = Fernet(filename.read_bytes())

    def seal(self, value):
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()

    def open(self, value):
        return json.loads(self.cipher.decrypt(value.encode()))


def public_environment(value):
    safe = {k: v for k, v in value.items() if k not in {"secretVariables", "roles"}}
    safe["secretVariableKeys"] = list(value.get("secretVariables", {}))
    safe["roles"] = [
        {"name": r["name"], "hasStorageState": bool(r.get("storageState")), "hasHeaders": bool(r.get("headers"))}
        for r in value.get("roles", [])
    ]
    return safe


def secrets_of(value):
    values = list(value.get("secretVariables", {}).values())
    for role in value.get("roles", []):
        values.extend((role.get("headers") or {}).values())
        values.extend(cookie.get("value") for cookie in (role.get("storageState") or {}).get("cookies", []))
        for origin in (role.get("storageState") or {}).get("origins", []):
            values.extend(item.get("value") for item in origin.get("localStorage", []))
    return sorted({v for v in values if isinstance(v, str) and v}, key=len, reverse=True)


def redact(value, secrets=()):
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
        return re.sub(r"(authorization|cookie|set-cookie)\s*[:=]\s*[^\r\n]+", r"\1: [REDACTED]", value, flags=re.I)
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if k.lower() in {"authorization", "cookie", "set-cookie", "password", "token", "secret"}
            else redact(v, secrets)
            for k, v in value.items()
        }
    return value
