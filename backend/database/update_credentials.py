import json
import sqlite3
import os
import sys

# Add backend directory to path to import crypto helper
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.utils.crypto import encrypt_value

target_url = "https://adactinhotelapp.com/"
target_username = "ADACTINFORQA"
password_enc = encrypt_value("hanish13")

# 1. Update project_store.json
json_path = os.path.join(os.path.dirname(__file__), "project_store.json")
if os.path.exists(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        store = json.load(f)

    for project in store.get("projects", []):
        project["target_url"] = target_url
        project["target_username"] = target_username
        project["target_password_enc"] = password_enc

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)
    print("Successfully updated project_store.json with credentials.")
else:
    print("project_store.json not found.")

# 2. Update local_fallback.db
db_path = os.path.join(os.path.dirname(__file__), "local_fallback.db")
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("UPDATE projects SET target_url = ?, target_username = ?, target_password_enc = ?", 
              (target_url, target_username, password_enc))
    conn.commit()
    conn.close()
    print("Successfully updated local_fallback.db SQL tables with credentials.")
else:
    print("local_fallback.db not found.")
