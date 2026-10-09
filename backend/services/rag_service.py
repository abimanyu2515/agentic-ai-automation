import os
import requests
from pathlib import Path
from uuid import UUID

from backend.config.settings import get_settings

class RAGFlowService:
    def __init__(self):
        self.settings = get_settings()
        self.enabled = getattr(self.settings.rag, "enabled", False)
        self.provider = getattr(self.settings.rag, "vector_db_provider", "chroma")
        self.api_base = getattr(self.settings.rag, "ragflow_api_base", "http://localhost:9380")
        self.api_key = getattr(self.settings.rag, "ragflow_api_key", None)
        self.dataset_id = getattr(self.settings.rag, "ragflow_dataset_id", None)

    def is_active(self) -> bool:
        return self.enabled and self.provider == "ragflow"

    def upload_document(self, file_path: Path, filename: str) -> dict | None:
        """
        Uploads a local file to the configured RAGFlow dataset/knowledge base.
        """
        if not self.is_active():
            return None
        if not self.api_key or not self.dataset_id:
            print("[RAGFlow] Error: Missing API key or Dataset ID in configuration.")
            return None

        url = f"{self.api_base}/api/v1/datasets/{self.dataset_id}/documents"
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

        try:
            with open(file_path, "rb") as f:
                files = {
                    "file": (filename, f)
                }
                # Do NOT set Content-Type header manually; requests handles boundary automatically
                response = requests.post(url, headers=headers, files=files, timeout=15)
                if response.status_code == 200:
                    res_data = response.json()
                    print(f"[RAGFlow] Document '{filename}' uploaded successfully: {res_data}")
                    return res_data
                else:
                    print(f"[RAGFlow] Failed to upload document '{filename}' (Status {response.status_code}): {response.text}")
                    return None
        except Exception as e:
            print(f"[RAGFlow] Exception uploading document '{filename}': {e}")
            return None

    def delete_document(self, document_id_or_name: str) -> bool:
        """
        Deletes a document from the RAGFlow dataset/knowledge base.
        """
        if not self.is_active() or not self.api_key or not self.dataset_id:
            return False

        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        try:
            # Try to resolve name to ID
            url = f"{self.api_base}/api/v1/datasets/{self.dataset_id}/documents"
            response = requests.get(url, headers=headers, timeout=10)
            doc_id = None
            if response.status_code == 200:
                docs = response.json().get("data", {}).get("documents", [])
                for d in docs:
                    if d.get("name") == document_id_or_name or d.get("id") == document_id_or_name:
                        doc_id = d.get("id")
                        break
            
            if not doc_id:
                doc_id = document_id_or_name

            del_url = f"{self.api_base}/api/v1/datasets/{self.dataset_id}/documents/{doc_id}"
            response = requests.delete(del_url, headers=headers, timeout=10)
            if response.status_code == 200:
                print(f"[RAGFlow] Deleted document {doc_id} successfully.")
                return True
        except Exception as e:
            print(f"[RAGFlow] Exception deleting document: {e}")
        return False

    def retrieve_context(self, query: str, top_k: int = 3) -> str:
        """
        Retrieves matching chunks from RAGFlow knowledge base and returns a consolidated string.
        """
        if not self.is_active():
            return ""
        if not self.api_key or not self.dataset_id:
            return ""

        url = f"{self.api_base}/api/v1/retrieval"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "question": query,
            "dataset_ids": [self.dataset_id],
            "top_k": top_k
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                res_data = response.json()
                data_field = res_data.get("data", {})
                chunks = []
                if isinstance(data_field, dict):
                    chunks = data_field.get("chunks", [])
                elif isinstance(data_field, list):
                    chunks = data_field

                texts = []
                for chunk in chunks:
                    content = chunk.get("content_with_weight") or chunk.get("content") or ""
                    if content:
                        texts.append(content)
                return "\n\n".join(texts)
            else:
                print(f"[RAGFlow] Retrieval failed (Status {response.status_code}): {response.text}")
        except Exception as e:
            print(f"[RAGFlow] Exception during retrieval: {e}")
        return ""
