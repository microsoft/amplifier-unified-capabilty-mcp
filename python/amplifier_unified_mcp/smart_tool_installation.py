"""Inert package inspection and safe diagnostics for explicit installations."""
from pathlib import Path
import tomllib

from packaging.specifiers import InvalidSpecifier, SpecifierSet


class InstallProblem(ValueError):
    def __init__(self, code, message, details=""):
        super().__init__(details or message)
        self.code, self.message = code, message

    def record(self, redact):
        return {"code": self.code, "message": self.message,
                "details": redact(str(self))[:1500]}


def python_project(root: Path):
    """Read only bounded static metadata; never execute a build backend."""
    project_file = root / "pyproject.toml"
    if not project_file.exists():
        return {"pythonSupported": False, "pythonRequires": None}
    try:
        if not project_file.resolve().is_relative_to(root.resolve()):
            raise ValueError("pyproject.toml points outside the selected package.")
        if not project_file.is_file() or project_file.stat().st_size > 256_000:
            raise ValueError("pyproject.toml must be a file smaller than 256 KB.")
        document = tomllib.loads(project_file.read_text())
        project = document.get("project")
        # A uv workspace root can have pyproject.toml without being a package.
        # Legacy build-backend metadata remains supported but has no static
        # Python constraint; its backend validates compatibility on installation.
        if project is None and not isinstance(document.get("build-system"), dict):
            raise ValueError("The selected folder is not an installable Python project. Select its package subdirectory.")
        if project is not None and not isinstance(project, dict):
            raise ValueError("The project table is invalid.")
        requirement = (project or {}).get("requires-python")
        if requirement is not None:
            if not isinstance(requirement, str) or not requirement.strip() or len(requirement) > 200:
                raise ValueError("project.requires-python must be a nonempty version constraint of at most 200 characters.")
            # This excludes executable paths and other uv Python request forms.
            SpecifierSet(requirement)
        return {"pythonSupported": True, "pythonRequires": requirement}
    except (OSError, UnicodeError, ValueError, InvalidSpecifier) as exc:
        raise InstallProblem("invalid_package", "This tool's package needs an update before it can be installed.", str(exc)) from exc
