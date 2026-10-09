import json
from pathlib import Path
from typing import Optional

class ApplicationStateResolver:
    def __init__(self, config_path: Optional[str] = None):
        if not config_path:
            config_path = str(Path(__file__).parent.parent / "config" / "app_states.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                self.config = json.load(f)
        except Exception as e:
            self.config = {"application_states": []}

    def resolve_state(self, url: str, title: str, visible_elements: list[str]) -> str:
        for state in self.config.get("application_states", []):
            name = state.get("name")
            url_contains = state.get("url_contains")
            title_contains = state.get("title_contains")
            required_elements = state.get("required_elements", [])

            # Check URL match
            if url_contains and url_contains not in url:
                continue

            # Check Title match
            if title_contains and title_contains.lower() not in title.lower():
                continue

            # Check Required Elements match
            if required_elements:
                # visible_elements must contain all required elements
                if not all(el in visible_elements for el in required_elements):
                    continue

            return name
        return "UNKNOWN"
