from uuid import UUID, uuid4
from datetime import datetime, timezone
from backend.models.project import Project
from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.test_case import TestCase
from backend.models.common import Priority
from backend.models.test_case import TestCaseStatus, EvaluationStatus
from backend.models.document import Document
from backend.models.execution_result import ExecutionResult
from backend.repository.project_repository import get_project_repository



class ProjectService:
    @property
    def repo(self):
        return get_project_repository()



    def list_projects(self) -> list[Project]:
        return self.repo.list_projects()

    def get_project(self, project_id: UUID) -> Project | None:
        return self.repo.get_project(project_id)

    def create_project(self, name: str, description: str, line_of_business: str = "general", framework: str = "playwright", jira_project_key: str | None = None, target_url: str | None = "https://adactinhotelapp.com/", target_username: str | None = None, target_password_enc: str | None = None) -> Project:
        return self.repo.create_project(
            name, description, line_of_business, framework, jira_project_key,
            target_url, target_username, target_password_enc
        )

    def get_requirements(self, project_id: UUID) -> list[Requirement]:
        return self.repo.get_requirements(project_id)

    def create_requirement(
        self,
        project_id: UUID,
        title: str,
        description: str,
        priority: str,
        business_domain: str,
        attachments: list[str] | None = None,
        original_filename: str | None = None,
        requirement_id: str | None = None,
        requirement_title: str | None = None,
        release_id: UUID | None = None
    ) -> Requirement:
        return self.repo.create_requirement(
            project_id, title, description, priority, business_domain, attachments,
            original_filename, requirement_id, requirement_title, release_id
        )

    def save_requirement(
        self,
        requirement: Requirement,
        priority: str | None = None,
        business_domain: str | None = None,
        attachments: list[str] | None = None
    ) -> None:
        self.repo.save_requirement(requirement, priority, business_domain, attachments)

    def get_scenarios_for_requirement(self, requirement_id: UUID) -> list[Scenario]:
        return self.repo.get_scenarios(requirement_id)

    def get_scenarios_for_project(self, project_id: UUID) -> list[Scenario]:
        return self.repo.get_scenarios_for_project(project_id)

    def update_scenario(
        self,
        scenario_id: UUID,
        scenario_name: str | None = None,
        description: str | None = None,
        priority: Priority | None = None,
        approved: bool | None = None,
        rejected: bool | None = None
    ) -> Scenario | None:
        scenario = self.repo.get_scenario(scenario_id)
        if not scenario:
            return None
        if scenario_name is not None:
            scenario.scenario_name = scenario_name
        if description is not None:
            scenario.description = description
        if priority is not None:
            scenario.priority = priority
        if approved is not None:
            scenario.approved = approved
            if approved:
                scenario.rejected = False
                scenario.reviewer = getattr(scenario, "reviewer", None) or "System"
                scenario.approved_at = datetime.now(timezone.utc)
            else:
                scenario.reviewer = None
                scenario.approved_at = None
                self.repo.delete_test_cases_for_scenario(scenario_id)
        if rejected is not None:
            scenario.rejected = rejected
            if rejected:
                scenario.approved = False
                self.repo.delete_test_cases_for_scenario(scenario_id)

        return self.repo.update_scenario(scenario)

    def delete_scenario(self, scenario_id: UUID) -> bool:
        return self.repo.delete_scenario(scenario_id)

    def duplicate_scenario(self, scenario_id: UUID) -> Scenario | None:
        orig = self.repo.get_scenario(scenario_id)
        if not orig:
            return None
        dup = Scenario(
            id=uuid4(),
            requirement_id=orig.requirement_id,
            scenario_name=f"{orig.scenario_name} (Copy)",
            description=orig.description,
            priority=orig.priority,
            confidence=orig.confidence,
            approved=orig.approved,
            generated_at=datetime.now(timezone.utc)
        )
        self.repo.save_scenarios([dup])
        return dup

    def get_test_cases_for_scenario(self, scenario_id: UUID) -> list[TestCase]:
        return self.repo.get_test_cases(scenario_id)

    def get_test_cases_for_project(self, project_id: UUID) -> list[TestCase]:
        return self.repo.get_test_cases_for_project(project_id)

    def get_test_case(self, test_case_id: UUID) -> TestCase | None:
        return self.repo.get_test_case(test_case_id)

    def delete_execution(self, execution_id: UUID, deleted_by: UUID | None = None) -> bool:
        return self.repo.delete_execution(execution_id, deleted_by)

    def delete_failed_executions(self, project_id: UUID, deleted_by: UUID | None = None) -> int:
        return self.repo.delete_failed_executions(project_id, deleted_by)

    def update_test_case(
        self,
        test_case_id: UUID,
        title: str | None = None,
        preconditions: list[str] | None = None,
        steps: list[str] | None = None,
        expected_result: str | None = None,
        priority: Priority | None = None,
        status: TestCaseStatus | None = None,
        confidence: float | None = None,
        evaluation_status: EvaluationStatus | None = None,
        evaluation_reason: str | None = None
    ) -> TestCase | None:
        tc = self.repo.get_test_case(test_case_id)
        if not tc:
            return None
        if title is not None:
            tc.title = title
        if preconditions is not None:
            tc.preconditions = preconditions
        if steps is not None:
            tc.steps = steps
        if expected_result is not None:
            tc.expected_result = expected_result
        if priority is not None:
            tc.priority = priority
        if status is not None:
            tc.status = status
        if confidence is not None:
            tc.confidence = confidence
        if evaluation_status is not None:
            tc.evaluation_status = evaluation_status
        if evaluation_reason is not None:
            tc.evaluation_reason = evaluation_reason

        return self.repo.update_test_case(tc)

    def delete_test_case(self, test_case_id: UUID) -> bool:
        return self.repo.delete_test_case(test_case_id)

    def update_project(self, project_id: UUID, name: str, description: str, line_of_business: str = "general") -> Project | None:
        proj = self.repo.get_project(project_id)
        if not proj:
            return None
        proj.name = name
        proj.description = description
        proj.line_of_business = line_of_business
        return self.repo.update_project(proj)

    def delete_project(self, project_id: UUID) -> bool:
        return self.repo.delete_project(project_id)

    def get_documents(self, project_id: UUID) -> list[Document]:
        return self.repo.get_documents(project_id)

    def create_document(
        self,
        project_id: UUID,
        filename: str,
        original_filename: str,
        mime_type: str,
        size: int,
        storage_path: str,
        metadata: dict | None = None
    ) -> Document:
        doc = Document(
            id=uuid4(),
            project_id=project_id,
            filename=filename,
            original_filename=original_filename,
            mime_type=mime_type,
            size=size,
            storage_path=storage_path,
            embedding_status="pending",
            metadata=metadata or {}
        )
        self.repo.save_document(doc)
        return doc

    def delete_document(self, project_id: UUID, document_id: UUID) -> bool:
        return self.repo.delete_document(project_id, document_id)

    def get_execution_results(self, project_id: UUID) -> list[ExecutionResult]:
        return self.repo.get_execution_results(project_id)

    def save_execution_result(self, project_id: UUID, result: ExecutionResult) -> None:
        self.repo.save_execution_result(project_id, result)

    def get_scenario_notes(self, scenario_id: UUID) -> list[str]:
        return self.repo.get_scenario_notes(scenario_id)

    def add_scenario_note(self, scenario_id: UUID, note: str) -> None:
        self.repo.add_scenario_note(scenario_id, note)

    def get_test_case_notes(self, test_case_id: UUID) -> list[str]:
        return self.repo.get_test_case_notes(test_case_id)

    def add_test_case_note(self, test_case_id: UUID, note: str) -> None:
        self.repo.add_test_case_note(test_case_id, note)

    def update_test_case_script(self, test_case_id: UUID, script: str) -> TestCase | None:
        tc = self.repo.get_test_case(test_case_id)
        if not tc:
            print(f"[DEBUG] test case not found in database: {str(test_case_id)}")
            return None
        tc.playwright_script = script
        return self.repo.update_test_case(tc)

    def get_execution_result(self, project_id: UUID, execution_id: UUID) -> ExecutionResult | None:
        return self.repo.get_execution_result(project_id, execution_id)

    def parse_swagger_spec(self, file_bytes: bytes, filename: str) -> list[dict]:
        import json
        parsed_blocks = []
        try:
            content_str = file_bytes.decode("utf-8", errors="ignore")
            data = None
            if filename.endswith(".json"):
                data = json.loads(content_str)
            elif filename.endswith((".yaml", ".yml")):
                try:
                    import yaml
                    data = yaml.safe_load(content_str)
                except ImportError:
                    pass
            
            if data and isinstance(data, dict) and "paths" in data:
                paths = data.get("paths", {})
                idx = 1
                for path, methods in paths.items():
                    for method, details in methods.items():
                        if method.lower() not in ["get", "post", "put", "delete", "patch", "options", "head"]:
                            continue
                        
                        summary = details.get("summary") or details.get("description") or f"API Endpoint {method.upper()} {path}"
                        description = f"API Endpoint: {method.upper()} {path}\nSummary: {summary}\n"
                        
                        req_body = details.get("requestBody")
                        if req_body:
                            description += f"Request Body: {json.dumps(req_body)}\n"
                        responses = details.get("responses")
                        if responses:
                            description += f"Responses: {json.dumps(responses)}\n"
                            
                        parsed_blocks.append({
                            "requirement_id": f"API-{idx:03d}",
                            "title": f"API: {method.upper()} {path}",
                            "description": description,
                            "priority": "medium",
                            "business_domain": "API Automation",
                            "requirement_title": f"{method.upper()} {path}"
                        })
                        idx += 1
        except Exception as e:
            print(f"[ERROR] Failed to parse Swagger/OpenAPI spec: {e}")
        return parsed_blocks

    def parse_postman_collection(self, file_bytes: bytes, filename: str) -> list[dict]:
        import json
        parsed_blocks = []
        try:
            content_str = file_bytes.decode("utf-8", errors="ignore")
            data = json.loads(content_str)
            
            is_postman = False
            if isinstance(data, dict):
                info = data.get("info", {})
                schema = info.get("schema", "") if isinstance(info, dict) else ""
                if "postman" in schema or "item" in data:
                    is_postman = True
            
            if not is_postman:
                return []
                
            collection_name = data.get("info", {}).get("name", "Postman Collection") if isinstance(data.get("info"), dict) else "Postman Collection"
            
            def extract_requests(items, folder_path=""):
                if not isinstance(items, list):
                    return
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name", "Request")
                    current_path = f"{folder_path}/{name}" if folder_path else name
                    
                    if "item" in item:
                        extract_requests(item["item"], current_path)
                    elif "request" in item:
                        req = item.get("request")
                        if not isinstance(req, dict):
                            if isinstance(req, str):
                                req = {"url": req, "method": "GET"}
                            else:
                                continue
                        
                        method = req.get("method", "GET")
                        url_info = req.get("url", "")
                        url_str = ""
                        if isinstance(url_info, dict):
                            url_str = url_info.get("raw", "")
                        elif isinstance(url_info, str):
                            url_str = url_info
                            
                        desc = req.get("description") or f"Execute API Request: {method} {url_str}"
                        if isinstance(desc, dict):
                            desc = desc.get("content", "")
                        
                        body_str = ""
                        body_info = req.get("body")
                        if isinstance(body_info, dict):
                            mode = body_info.get("mode")
                            if mode == "raw":
                                body_str = body_info.get("raw", "")
                            elif mode in ["formdata", "urlencoded"]:
                                params = body_info.get(mode, [])
                                if isinstance(params, list):
                                    body_str = json.dumps({p.get("key"): p.get("value") for p in params if isinstance(p, dict)})
                                    
                        headers = req.get("header", [])
                        headers_str = ""
                        if isinstance(headers, list):
                            headers_str = json.dumps({h.get("key"): h.get("value") for h in headers if isinstance(h, dict)})
                            
                        full_description = (
                            f"Postman Ingestion Source\n"
                            f"Collection: {collection_name}\n"
                            f"Path: {current_path}\n"
                            f"Method: {method}\n"
                            f"URL: {url_str}\n"
                            f"Headers: {headers_str}\n"
                            f"Body: {body_str}\n\n"
                            f"Description:\n{desc}"
                        )
                        
                        parsed_blocks.append({
                            "requirement_id": f"PM-{len(parsed_blocks) + 1:03d}",
                            "title": f"API Request: {name}",
                            "description": full_description,
                            "priority": "medium",
                            "business_domain": "API Ingestion",
                            "requirement_title": f"{method} {name}"
                        })
            
            extract_requests(data.get("item", []))
        except Exception as e:
            print(f"[ERROR] Failed to parse Postman collection: {e}")
        return parsed_blocks

    def parse_requirements_from_file(self, filename: str, file_bytes: bytes) -> list[dict]:
        import re
        import io
        import zipfile
        import pandas as pd
        import fitz
        from pathlib import Path
        import xml.etree.ElementTree as ET

        ext = Path(filename).suffix.lower()
        parsed_blocks = []

        if ext in [".json", ".yaml", ".yml"]:
            # First try Postman collection format
            parsed_blocks = self.parse_postman_collection(file_bytes, filename)
            if not parsed_blocks:
                # Fallback to Swagger/OpenAPI spec
                parsed_blocks = self.parse_swagger_spec(file_bytes, filename)
            if not parsed_blocks:
                text = file_bytes.decode("utf-8", errors="ignore")
                parsed_blocks = self._parse_requirements_from_text(text, filename)
        elif ext in [".xlsx", ".xls"]:
            df = pd.read_excel(io.BytesIO(file_bytes))
            cols = {col.lower().replace(" ", "").replace("_", ""): col for col in df.columns}
            
            id_col = cols.get("requirementid") or cols.get("id")
            desc_col = cols.get("requirement") or cols.get("description") or cols.get("title")
            priority_col = cols.get("priority")
            module_col = cols.get("module") or cols.get("businessdomain") or cols.get("domain")

            if not desc_col:
                desc_col = df.columns[0]

            for idx, row in df.iterrows():
                req_id_val = str(row[id_col]) if (id_col and id_col in df.columns) else f"REQ-{idx+1:03d}"
                desc_val = str(row[desc_col]) if (desc_col and desc_col in df.columns) else ""
                priority_val = str(row[priority_col]).lower() if (priority_col and priority_col in df.columns) else "medium"
                module_val = str(row[module_col]) if (module_col and module_col in df.columns) else "general"

                if not desc_val.strip() or pd.isna(row[desc_col]):
                    continue

                if priority_val not in ["low", "medium", "high"]:
                    priority_val = "medium"

                parsed_blocks.append({
                    "requirement_id": req_id_val,
                    "title": f"{req_id_val}: {desc_val[:50]}...",
                    "description": desc_val,
                    "priority": priority_val,
                    "business_domain": module_val,
                    "requirement_title": desc_val[:50]
                })

        elif ext == ".pdf":
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            text = ""
            for page in doc:
                text += page.get_text()
            parsed_blocks = self._parse_requirements_from_text(text, filename)

        elif ext in [".docx", ".doc"]:
            try:
                with zipfile.ZipFile(io.BytesIO(file_bytes)) as docx_zip:
                    xml_content = docx_zip.read('word/document.xml')
                    root = ET.fromstring(xml_content)
                    
                    namespaces = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                    paragraphs = []
                    for para in root.findall('.//w:p', namespaces):
                        text_elems = para.findall('.//w:t', namespaces)
                        text = "".join([t.text for t in text_elems if t.text])
                        if text.strip():
                            paragraphs.append(text)
                    full_text = "\n\n".join(paragraphs)
            except Exception:
                full_text = "Failed to parse Word document XML structure."
            parsed_blocks = self._parse_requirements_from_text(full_text, filename)

        else:
            text = file_bytes.decode("utf-8", errors="ignore")
            parsed_blocks = self._parse_requirements_from_text(text, filename)

        return parsed_blocks

    def _parse_requirements_from_text(self, text: str, filename: str) -> list[dict]:
        import re
        pattern = r'(REQ-\d+|Requirement\s+\d+|[A-Z]+-\d+):'
        parts = re.split(pattern, text)
        
        parsed = []
        if len(parts) > 1:
            i = 1
            while i < len(parts):
                req_id = parts[i].strip()
                content = parts[i+1].strip() if i+1 < len(parts) else ""
                description = content
                
                # Guess priority
                priority = "medium"
                if any(k in description.lower() for k in ["high", "critical", "urgent", "must"]):
                    priority = "high"
                elif any(k in description.lower() for k in ["low", "minor", "nice to have"]):
                    priority = "low"
                    
                # Guess line of business / domain
                business_domain = "general"
                for k in ["banking", "finance", "auth", "login", "profile", "billing", "payment", "checkout", "search", "upload", "insurance", "retail", "healthcare"]:
                    if k in description.lower():
                        business_domain = k.capitalize()
                        break
                        
                # Try to extract a clean requirement title from description
                req_title = "Requirement Block"
                title_match = re.search(r'Title\s*\n\s*([^\n]+)', description, re.IGNORECASE)
                if title_match:
                    req_title = title_match.group(1).strip()
                else:
                    first_line = description.split('\n')[0].strip()
                    if first_line:
                        req_title = first_line[:50].strip()
                        
                title = f"{req_id}: {req_title}"
                parsed.append({
                    "requirement_id": req_id,
                    "title": title,
                    "description": description,
                    "priority": priority,
                    "business_domain": business_domain,
                    "requirement_title": req_title
                })
                i += 2
        else:
            # Split by double newline (paragraph blocks)
            blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
            for idx, block in enumerate(blocks):
                if len(block) < 25:
                    continue
                    
                req_id = f"REQ-{idx+1:03d}"
                priority = "medium"
                if any(k in block.lower() for k in ["high", "critical", "urgent"]):
                    priority = "high"
                elif any(k in block.lower() for k in ["low", "minor"]):
                    priority = "low"
                    
                business_domain = "general"
                for k in ["banking", "finance", "auth", "login", "profile", "billing", "payment", "checkout", "search", "upload", "insurance", "retail", "healthcare"]:
                    if k in block.lower():
                        business_domain = k.capitalize()
                        break
                        
                req_title = block[:50].strip() + "..."
                title = f"{req_id}: {req_title}"
                parsed.append({
                    "requirement_id": req_id,
                    "title": title,
                    "description": block,
                    "priority": priority,
                    "business_domain": business_domain,
                    "requirement_title": req_title
                })
                
        if not parsed:
            parsed.append({
                "requirement_id": "REQ-001",
                "title": filename,
                "description": text or "Empty file content",
                "priority": "medium",
                "business_domain": "general",
                "requirement_title": filename
            })
            
        return parsed

