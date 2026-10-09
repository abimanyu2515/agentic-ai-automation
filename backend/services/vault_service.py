import os
import json
from pathlib import Path
from backend.utils.crypto import encrypt_value, decrypt_value

VAULT_PATH = Path(__file__).parent.parent / "database" / "vault.json"

class VaultService:
    def __init__(self, vault_path: Path = VAULT_PATH):
        self.vault_path = vault_path
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.vault_path.exists():
            self._save_vault({})

    def _read_vault(self) -> dict[str, dict[str, str]]:
        try:
            if self.vault_path.exists():
                with open(self.vault_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_vault(self, data: dict[str, dict[str, str]]) -> None:
        with open(self.vault_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def set_credentials(self, project_id: str, username: str | None, password: str | None) -> None:
        vault = self._read_vault()
        
        username_enc = encrypt_value(username) if username else None
        password_enc = encrypt_value(password) if password else None
        
        vault[project_id] = {
            "username_enc": username_enc,
            "password_enc": password_enc
        }
        self._save_vault(vault)

    def get_credentials(self, project_id: str) -> tuple[str | None, str | None]:
        vault = self._read_vault()
        creds = vault.get(project_id)
        if not creds:
            return None, None
        
        username = decrypt_value(creds.get("username_enc"))
        password = decrypt_value(creds.get("password_enc"))
        return username, password
