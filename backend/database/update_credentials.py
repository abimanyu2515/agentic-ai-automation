"""Set per-project target credentials (target-agnostic utility).

Previously this script hardcoded a single demo application's URL and
credentials across every project. It now requires explicit values so no
project is silently pointed at a demo site.

Usage (PowerShell):
    $env:TARGET_URL="https://your-app.example.com"
    $env:TARGET_USERNAME="your-user"
    $env:TARGET_PASSWORD="your-password"
    python backend/database/update_credentials.py
"""

import json
import sqlite3
import os
import sys

# Add backend directory to path to import crypto helper
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.utils.crypto import encrypt_value

target_url = os.environ.get("TARGET_URL")
target_username = os.environ.get("TARGET_USERNAME")
target_password = os.environ.get("TARGET_PASSWORD")

if not target_url or not target_username or not target_password:
    raise SystemExit(
        "Refusing to run: set TARGET_URL, TARGET_USERNAME and TARGET_PASSWORD "
        "environment variables. No default target application is assumed."
    )

password_enc = encrypt_value(target_password)

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
