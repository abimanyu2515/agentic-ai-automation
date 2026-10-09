from typing import Any, Optional

class ArtifactManager:
    def __init__(self):
        self.screenshots = []
        self.video = None
        self.trace = None
        self.logs = []
        self.console = []
        self.timeline = []

    def collect_screenshot(self, name: str, path: str) -> None:
        self.screenshots.append({"name": name, "path": path})

    def collect_video(self, path: str) -> None:
        self.video = path

    def collect_trace(self, path: str) -> None:
        self.trace = path

    def collect_console(self, message: str) -> None:
        self.console.append(message)

    def collect_logs(self, message: str) -> None:
        self.logs.append(message)

    def collect_timeline(self, event: str, event_type: str = "info", details: Optional[str] = None) -> None:
        from datetime import datetime
        self.timeline.append({
            "timestamp": datetime.now().isoformat(),
            "event": event,
            "type": event_type,
            "details": details
        })

    def package(self) -> dict[str, Any]:
        return {
            "screenshots": self.screenshots,
            "video": self.video,
            "trace": self.trace,
            "logs": self.logs,
            "console": self.console,
            "timeline": self.timeline
        }
