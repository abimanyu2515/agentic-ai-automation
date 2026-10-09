import os
import sys
import base64
import logging
import requests
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from backend.config.settings import get_settings

logger = logging.getLogger("backend.services.jira_service")


def is_running_tests() -> bool:
    return "pytest" in sys.modules or os.getenv("APP_ENV") == "testing"


class JiraService:
    def __init__(self):
        self.settings = get_settings()
        self.config = self.settings.jira
        self.mock_mode = is_running_tests() or not self.config.api_token or not self.config.email
        self._issue_types_cache = {}
        
        if self.mock_mode:
            logger.info("JiraService initialized in MOCK MODE.")
        else:
            logger.info("JiraService initialized in REAL API MODE.")
            self.session = requests.Session()
            # Retry adapter for 429 and 5xx errors with exponential backoff
            retries = Retry(
                total=4,
                backoff_factor=1.5,
                status_forcelist=[429, 500, 502, 503, 504],
                raise_on_status=False
            )
            self.session.mount("https://", HTTPAdapter(max_retries=retries))
            self.session.mount("http://", HTTPAdapter(max_retries=retries))

    def _get_headers(self) -> dict:
        if self.mock_mode:
            return {}
        # Basic auth base64 representation
        auth_str = f"{self.config.email}:{self.config.api_token}"
        auth_b64 = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
        return {
            "Authorization": f"Basic {auth_b64}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

    def search_issues(self, jql: str) -> list[dict]:
        """Search issues using JQL."""
        if self.mock_mode:
            logger.info(f"[Mock JIRA] Searching JQL: '{jql}'")
            # Return empty or simulate finding a match based on labels
            if "label_match_trigger" in jql:
                return [{
                    "key": "MOCK-101",
                    "fields": {
                        "summary": "Mock Issue",
                        "status": {"name": "In Progress"},
                        "labels": ["label_match_trigger"]
                    }
                }]
            return []

        url = f"{self.config.base_url.rstrip('/')}/rest/api/3/search/jql"
        try:
            response = self.session.post(
                url,
                json={"jql": jql, "maxResults": 10},
                headers=self._get_headers(),
                verify=self.config.verify_ssl,
                timeout=15
            )
            if response.status_code == 200:
                return response.json().get("issues", [])
            logger.error(f"Jira search failed ({response.status_code}): {response.text}")
        except Exception as e:
            logger.error(f"Jira search error: {e}")
        return []

    def get_issue(self, issue_key: str) -> dict | None:
        """Fetch details of a single Jira issue by key."""
        if self.mock_mode:
            logger.info(f"[Mock JIRA] Fetching issue: '{issue_key}'")
            return {
                "key": issue_key,
                "fields": {
                    "summary": f"Mock Story for {issue_key}: Book a Hotel room in Adactin",
                    "description": "As a registered user, I want to book a hotel room using Adactin Hotel App so that I can secure accommodation.",
                    "priority": {"name": "High"},
                    "issuetype": {"name": "Story"}
                }
            }

        url = f"{self.config.base_url.rstrip('/')}/rest/api/2/issue/{issue_key}"
        try:
            response = self.session.get(
                url,
                headers=self._get_headers(),
                verify=self.config.verify_ssl,
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
            logger.error(f"Jira get issue failed ({response.status_code}): {response.text}")
        except Exception as e:
            logger.error(f"Jira get issue error: {e}")
        return None

    def get_valid_issue_type(self, project_key: str, preferred_type: str) -> str:
        """Dynamically resolve preferred_type to an available issue type in the project."""
        if self.mock_mode:
            return preferred_type

        if project_key in self._issue_types_cache:
            types = self._issue_types_cache[project_key]
        else:
            url = f"{self.config.base_url.rstrip('/')}/rest/api/2/project/{project_key}"
            types = []
            try:
                response = self.session.get(
                    url,
                    headers=self._get_headers(),
                    verify=self.config.verify_ssl,
                    timeout=10
                )
                if response.status_code == 200:
                    proj_data = response.json()
                    types = [
                        it.get("name")
                        for it in proj_data.get("issueTypes", [])
                        if not it.get("subtask")
                    ]
                    self._issue_types_cache[project_key] = types
                else:
                    logger.error(f"Failed to fetch Jira project issue types ({response.status_code}): {response.text}")
            except Exception as e:
                logger.error(f"Error fetching Jira project issue types: {e}")

        if not types:
            return preferred_type

        preferred_lower = preferred_type.lower()
        for t in types:
            if t.lower() == preferred_lower:
                return t

        for fallback in ["Task", "Bug", "Story"]:
            for t in types:
                if t.lower() == fallback.lower():
                    return t

        return types[0]

    def create_issue(self, summary: str, description: str, issue_type: str, project_key: str | None = None, labels: list[str] | None = None, assignee_email: str | None = None) -> dict | None:
        """Create a new issue (e.g. Story or Bug) in Jira."""
        proj_key = project_key or self.config.project_key or "QA"
        labels = labels or []
        resolved_issue_type = self.get_valid_issue_type(proj_key, issue_type)
        
        if self.mock_mode:
            mock_id = int(datetime.now().timestamp() * 1000) % 10000
            mock_key = f"{proj_key}-{mock_id}"
            logger.info(f"[Mock JIRA] Created {resolved_issue_type} key: {mock_key}")
            return {
                "key": mock_key,
                "self": f"{self.config.base_url.rstrip('/')}/rest/api/2/issue/{mock_key}",
                "url": f"{self.config.base_url.rstrip('/')}/browse/{mock_key}"
            }

        url = f"{self.config.base_url.rstrip('/')}/rest/api/2/issue"
        payload = {
            "fields": {
                "project": {"key": proj_key},
                "summary": summary,
                "description": description,
                "issuetype": {"name": resolved_issue_type},
                "labels": labels
            }
        }
        
        if assignee_email:
            # Map platform assignee email to Jira account
            payload["fields"]["assignee"] = {"name": assignee_email}

        try:
            response = self.session.post(
                url,
                json=payload,
                headers=self._get_headers(),
                verify=self.config.verify_ssl,
                timeout=15
            )
            if response.status_code == 201:
                data = response.json()
                key = data.get("key")
                return {
                    "key": key,
                    "self": data.get("self"),
                    "url": f"{self.config.base_url.rstrip('/')}/browse/{key}"
                }
            logger.error(f"Jira create issue failed ({response.status_code}): {response.text}")
        except Exception as e:
            logger.error(f"Jira create issue error: {e}")
        return None

    def add_comment(self, issue_key: str, comment_text: str) -> bool:
        """Add a comment trace to a Jira issue."""
        if self.mock_mode:
            logger.info(f"[Mock JIRA] Added comment to {issue_key}: '{comment_text}'")
            return True

        url = f"{self.config.base_url.rstrip('/')}/rest/api/2/issue/{issue_key}/comment"
        try:
            response = self.session.post(
                url,
                json={"body": comment_text},
                headers=self._get_headers(),
                verify=self.config.verify_ssl,
                timeout=10
            )
            return response.status_code == 201
        except Exception as e:
            logger.error(f"Jira add comment error: {e}")
        return False

    def transition_issue(self, issue_key: str, transition_name: str) -> bool:
        """Transition issue state (e.g. In Progress, Resolve, Close)."""
        if self.mock_mode:
            logger.info(f"[Mock JIRA] Transitioned {issue_key} -> '{transition_name}'")
            return True

        # First find transition ID matching name
        url_transitions = f"{self.config.base_url.rstrip('/')}/rest/api/2/issue/{issue_key}/transitions"
        try:
            headers = self._get_headers()
            res = self.session.get(url_transitions, headers=headers, verify=self.config.verify_ssl, timeout=10)
            if res.status_code != 200:
                return False
                
            transitions = res.json().get("transitions", [])
            trans_id = None
            for t in transitions:
                if t.get("name", "").lower() == transition_name.lower():
                    trans_id = t.get("id")
                    break
            
            if not trans_id:
                logger.warning(f"Jira transition name '{transition_name}' not found for {issue_key}")
                return False

            response = self.session.post(
                url_transitions,
                json={"transition": {"id": trans_id}},
                headers=headers,
                verify=self.config.verify_ssl,
                timeout=10
            )
            return response.status_code == 204
        except Exception as e:
            logger.error(f"Jira transition error: {e}")
        return False

    def upload_attachment(self, issue_key: str, filename: str, content: bytes, mime_type: str) -> bool:
        """Upload a file attachment to the Jira issue (screenshots, reports, traces, videos)."""
        if self.mock_mode:
            logger.info(f"[Mock JIRA] Uploaded attachment '{filename}' ({len(content)} bytes) to {issue_key}")
            return True

        url = f"{self.config.base_url.rstrip('/')}/rest/api/2/issue/{issue_key}/attachments"
        headers = self._get_headers()
        # strip Content-Type to let requests build multipart borders automatically
        headers.pop("Content-Type", None)
        headers["X-Atlassian-Token"] = "no-check"
        
        try:
            response = self.session.post(
                url,
                headers=headers,
                files={"file": (filename, content, mime_type)},
                verify=self.config.verify_ssl,
                timeout=25
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Jira attachment upload error: {e}")
        return False

    def update_issue(self, issue_key: str, fields: dict) -> bool:
        """Update fields of a Jira issue."""
        if self.mock_mode:
            logger.info(f"[Mock JIRA] Updated issue {issue_key} with fields: {fields}")
            return True

        url = f"{self.config.base_url.rstrip('/')}/rest/api/2/issue/{issue_key}"
        try:
            response = self.session.put(
                url,
                json={"fields": fields},
                headers=self._get_headers(),
                verify=self.config.verify_ssl,
                timeout=15
            )
            if response.status_code == 204:
                return True
            logger.error(f"Jira update issue failed ({response.status_code}): {response.text}")
        except Exception as e:
            logger.error(f"Jira update issue error: {e}")
        return False

    def link_issues(self, inward_key: str, outward_key: str, link_type: str = "Relates") -> bool:
        """Link two Jira issues together (e.g. Bug to Story)."""
        if self.mock_mode:
            logger.info(f"[Mock JIRA] Linked issues: {inward_key} {link_type} {outward_key}")
            return True

        url = f"{self.config.base_url.rstrip('/')}/rest/api/2/issueLink"
        payload = {
            "type": {
                "name": link_type
            },
            "inwardIssue": {
                "key": inward_key
            },
            "outwardIssue": {
                "key": outward_key
            }
        }
        try:
            response = self.session.post(
                url,
                json=payload,
                headers=self._get_headers(),
                verify=self.config.verify_ssl,
                timeout=10
            )
            return response.status_code == 201
        except Exception as e:
            logger.error(f"Jira link issues error: {e}")
        return False

    def find_custom_field_by_name(self, name_query: str) -> str | None:
        """Find custom field ID by its exact or partial name."""
        if self.mock_mode:
            return "customfield_10100"

        url = f"{self.config.base_url.rstrip('/')}/rest/api/2/field"
        try:
            response = self.session.get(
                url,
                headers=self._get_headers(),
                verify=self.config.verify_ssl,
                timeout=10
            )
            if response.status_code == 200:
                fields = response.json()
                for f in fields:
                    if f.get("name", "").lower() == name_query.lower():
                        return f.get("id")
            else:
                logger.error(f"Jira get fields failed ({response.status_code}): {response.text}")
        except Exception as e:
            logger.error(f"Jira get fields error: {e}")
        return None
