import os
import re
import zipfile
import shutil

CLASS_PATTERN = re.compile(r"class\s+(\w+)")
METHOD_PATTERN = re.compile(r"(?:async\s+)?(\w+)\s*\(([^)]*)\)")

def parse_code_content(content: str) -> list[dict]:
    """Simple parser using regex to extract classes and methods/parameters from source files."""
    results = []
    current_class = None
    class_methods = []

    for line in content.splitlines():
        line = line.strip()
        
        # Match class definition
        class_match = CLASS_PATTERN.search(line)
        if class_match:
            if current_class:
                results.append({
                    "class_name": current_class,
                    "methods": class_methods
                })
            current_class = class_match.group(1)
            class_methods = []
            continue

        # Match method definitions (excluding constructors/dunder methods)
        method_match = METHOD_PATTERN.search(line)
        if method_match and current_class:
            name = method_match.group(1)
            params = [p.strip() for p in method_match.group(2).split(",") if p.strip()]
            if name not in ["constructor", "__init__"] and not name.startswith("_"):
                class_methods.append({
                    "method_name": name,
                    "parameters": params
                })

    if current_class:
        results.append({
            "class_name": current_class,
            "methods": class_methods
        })

    return results

def index_zip_file(zip_path: str, extract_to: str) -> dict:
    """Extracts zip framework and indexes POM source files."""
    if os.path.exists(extract_to):
        shutil.rmtree(extract_to)
    os.makedirs(extract_to, exist_ok=True)

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_to)

    pom_index = {}
    for root, _, files in os.walk(extract_to):
        for file in files:
            if file.endswith((".ts", ".js", ".py", ".java")):
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    parsed = parse_code_content(content)
                    if parsed:
                        rel_path = os.path.relpath(filepath, extract_to).replace("\\", "/")
                        pom_index[rel_path] = parsed
                except Exception:
                    continue
    return pom_index
