"""OfficeCLI Service - Document automation via OfficeCLI binary

Provides template-based DOCX generation using OfficeCLI.
https://github.com/iOfficeAI/OfficeCLI
"""

import os
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, List
import structlog

logger = structlog.get_logger()

from app.config import get_settings

settings = get_settings()


class OfficeCLIService:
    """Wrapper for OfficeCLI binary operations"""

    _instance: Optional["OfficeCLIService"] = None
    _binary_path: Optional[str] = None

    def __init__(self):
        self._binary_path = self._find_binary()

    @classmethod
    def get_instance(cls) -> "OfficeCLIService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _find_binary(self) -> Optional[str]:
        """Find OfficeCLI binary in configured locations"""
        bin_name = "officecli" if os.name != "nt" else "officecli.exe"

        search_paths = [
            os.environ.get("OFFICECLI_PATH"),
            os.path.join(settings.DATA_DIR, "officecli", bin_name),
            "/usr/local/bin/officecli",
            "/usr/bin/officecli",
            str(Path(__file__).parent.parent.parent.parent / "officecli" / bin_name),
        ]

        for path in search_paths:
            if path and os.path.isfile(path):
                logger.info("officecli_binary_found", path=path)
                return path

        found = self._try_download()
        if found:
            return found

        logger.warning("officecli_binary_not_found")
        return None

    def _try_download(self) -> Optional[str]:
        """Download OfficeCLI binary if not found"""
        import platform

        system = platform.system().lower()
        arch = "arm64" if platform.machine().startswith(("arm", "aarch64")) else "x64"

        if system == "windows":
            fname = f"officecli-win-{arch}.exe"
        elif system == "darwin":
            fname = f"officecli-mac-{arch}"
        else:
            fname = f"officecli-linux-{arch}"

        download_dir = Path(settings.DATA_DIR) / "officecli"
        download_dir.mkdir(parents=True, exist_ok=True)

        target_path = download_dir / fname
        if target_path.exists():
            self._binary_path = str(target_path)
            return str(target_path)

        try:
            import urllib.request

            url = f"https://github.com/iOfficeAI/OfficeCLI/releases/latest/download/{fname}"
            logger.info("officecli_downloading", url=url)
            urllib.request.urlretrieve(url, target_path)
            os.chmod(target_path, 0o755)
            self._binary_path = str(target_path)
            logger.info("officecli_downloaded", path=str(target_path))
            return str(target_path)
        except Exception as e:
            logger.warning("officecli_download_failed", error=str(e))
            return None

    def is_available(self) -> bool:
        """Check if OfficeCLI is available"""
        if not self._binary_path:
            return False
        try:
            result = subprocess.run(
                [self._binary_path, "--version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _run_command(
        self, *args: str, timeout: int = 30, input_data: Optional[str] = None
    ) -> subprocess.CompletedProcess:
        """Run OfficeCLI command"""
        if not self._binary_path:
            raise RuntimeError("OfficeCLI not available")

        cmd = [self._binary_path] + list(args)
        logger.debug("officecli_command", cmd=cmd)

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_data,
        )

    async def validate_document(self, doc_path: str) -> Dict[str, Any]:
        """Validate document structure and return issues"""
        import asyncio

        if not self.is_available():
            return {"success": False, "error": "OfficeCLI not available"}

        result = await asyncio.to_thread(self._run_command, "validate", doc_path, timeout=15)
        output = result.stdout.strip()

        if result.returncode != 0:
            return {"success": False, "error": output}

        issues = await asyncio.to_thread(
            self._run_command, "view", doc_path, "issues", "--json", timeout=15
        )
        try:
            issues_data = json.loads(issues.stdout) if issues.stdout else []
        except json.JSONDecodeError:
            issues_data = []

        return {"success": True, "issues": self._normalize_issues(issues_data)}

    @staticmethod
    def _normalize_issues(issues_data: Any) -> list:
        """Flatten OfficeCLI's issue report to a list of blocking issues.

        OfficeCLI wraps results as {"success": true, "data": {"count": 0,
        "issues": []}}. Callers tested ``if result["issues"]``, and a non-empty
        wrapper dict is truthy, so every clean DOCX/PPTX/XLSX was rejected
        whenever OfficeCLI was installed. Only error-level items block.
        """
        items = issues_data
        if isinstance(items, dict):
            items = (items.get("data") or {}).get("issues") if isinstance(items.get("data"), dict) else items.get("issues")
        if not isinstance(items, list):
            return []
        blocking = []
        for item in items:
            level = str((item or {}).get("severity") or (item or {}).get("level") or "error").lower() if isinstance(item, dict) else "error"
            if level in ("error", "critical", "fatal"):
                blocking.append(item)
        return blocking

    async def merge_template(
        self,
        template_path: str,
        output_path: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merge JSON data into template"""
        if not self.is_available():
            return {"success": False, "error": "OfficeCLI not available"}

        if not os.path.exists(template_path):
            return {"success": False, "error": f"Template not found: {template_path}"}

        data_json = json.dumps(data)

        result = self._run_command(
            "merge",
            template_path,
            output_path,
            data_json,
            timeout=30,
        )

        if result.returncode != 0:
            logger.error("officecli_merge_failed", error=result.stderr)
            return {"success": False, "error": result.stderr}

        logger.info("officecli_merge_success", output=output_path)
        return {"success": True, "output": output_path}

    async def merge_template_file(
        self,
        template_path: str,
        output_path: str,
        data_file: str,
    ) -> Dict[str, Any]:
        """Merge JSON file data into template"""
        if not self.is_available():
            return {"success": False, "error": "OfficeCLI not available"}

        if not os.path.exists(template_path):
            return {"success": False, "error": f"Template not found: {template_path}"}
        if not os.path.exists(data_file):
            return {"success": False, "error": f"Data file not found: {data_file}"}

        result = self._run_command(
            "merge",
            template_path,
            output_path,
            data_file,
            timeout=30,
        )

        if result.returncode != 0:
            logger.error("officecli_merge_failed", error=result.stderr)
            return {"success": False, "error": result.stderr}

        logger.info("officecli_merge_success", output=output_path)
        return {"success": True, "output": output_path}

    async def create_document(
        self,
        output_path: str,
        type: str = "docx",
    ) -> Dict[str, Any]:
        """Create blank document"""
        if not self.is_available():
            return {"success": False, "error": "OfficeCLI not available"}

        result = self._run_command("create", output_path, timeout=10)

        if result.returncode != 0:
            return {"success": False, "error": result.stderr}

        return {"success": True, "output": output_path}

    async def batch_operations(
        self,
        doc_path: str,
        operations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Execute batch operations"""
        if not self.is_available():
            return {"success": False, "error": "OfficeCLI not available"}

        ops_json = json.dumps(operations)
        result = self._run_command(
            "batch",
            doc_path,
            "--commands",
            ops_json,
            timeout=60,
        )

        if result.returncode != 0:
            return {"success": False, "error": result.stderr}

        return {"success": True}

    def get_template_variables(self, template_path: str) -> List[str]:
        """Extract {{variable}} names from template"""
        if not os.path.exists(template_path):
            return []

        try:
            result = self._run_command("view", template_path, "text", timeout=10)
            if result.returncode != 0:
                return []

            import re

            variables = re.findall(r"\{\{(\w+)\}\}", result.stdout)
            return list(set(variables))
        except Exception:
            return []


def get_officecli_service() -> OfficeCLIService:
    """Get OfficeCLI service singleton"""
    return OfficeCLIService.get_instance()
