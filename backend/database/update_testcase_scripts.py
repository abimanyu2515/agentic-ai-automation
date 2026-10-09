import json
import sqlite3
import os

# 1. Update project_store.json
json_path = os.path.join(os.path.dirname(__file__), "project_store.json")
if os.path.exists(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        store = json.load(f)

    # Replace in root level test cases
    if "test_cases" in store:
        for tc in store["test_cases"]:
            if "playwright_script" in tc and tc["playwright_script"]:
                script = tc["playwright_script"]
                script = script.replace("standard_user", "ADACTINFORQA")
                script = script.replace("secret_sauce", "hanish13")
                tc["playwright_script"] = script

    # Replace in project level test cases
    for project in store.get("projects", []):
        if "test_cases" in project:
            for tc in project["test_cases"]:
                if "playwright_script" in tc and tc["playwright_script"]:
                    script = tc["playwright_script"]
                    script = script.replace("standard_user", "ADACTINFORQA")
                    script = script.replace("secret_sauce", "hanish13")
                    tc["playwright_script"] = script

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)
    print("Updated playwright scripts inside project_store.json.")
else:
    print("project_store.json not found.")

# 2. Update local_fallback.db
db_path = os.path.join(os.path.dirname(__file__), "local_fallback.db")
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT id, playwright_script FROM test_cases")
    rows = c.fetchall()
    updated_count = 0
    for row_id, script in rows:
        if script:
            new_script = script.replace("standard_user", "ADACTINFORQA").replace("secret_sauce", "hanish13")
            if new_script != script:
                c.execute("UPDATE test_cases SET playwright_script = ? WHERE id = ?", (new_script, row_id))
                updated_count += 1
    conn.commit()
    conn.close()
    print(f"Updated {updated_count} playwright scripts inside local_fallback.db.")
else:
    print("local_fallback.db not found.")
