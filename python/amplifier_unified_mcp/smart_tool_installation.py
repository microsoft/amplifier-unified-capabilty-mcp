"""Inert package inspection and safe diagnostics for explicit installations."""
import os
import shutil
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


class ManagedInstaller:
    """Trusted launcher configuration; never tool args or catalog metadata.

    Availability checks inspect the selected executable only. They do not run it,
    inspect credentials, download software or claim package/renderer readiness.
    """
    unavailable_message = 'Smart Tool installation is unavailable because its installer executable cannot be found or used. Ask your administrator to configure the installer location.'

    def __init__(self, configuration=None):
        if configuration is not None:
            if not isinstance(configuration, dict) or set(configuration) != {'executable'}:
                raise ValueError('Installer configuration requires only an absolute executable path.')
            value = configuration['executable']
            if (not isinstance(value, str) or not 1 <= len(value) <= 4096
                    or any(ord(c) < 32 for c in value) or not Path(value).is_absolute()):
                raise ValueError('Installer executable must be an absolute path of at most 4096 characters.')
            self.executable = value
        else:
            self.executable = None

    def resolve(self):
        # Explicit configuration is authoritative: never silently use another uv.
        if self.executable is not None:
            path = Path(self.executable)
            try:
                return self.executable if path.is_file() and os.access(path, os.X_OK) else None
            except OSError:
                return None
        return shutil.which('uv')

    def readiness(self):
        available = self.resolve() is not None
        return {'version': 1, 'status': 'available' if available else 'unavailable',
                'source': 'configured' if self.executable is not None else 'path',
                'code': None if available else 'installer_unavailable',
                'message': ('The installer executable is available. Tool prerequisites and installation have not been checked.' if available else
                            self.unavailable_message)}

    def require(self):
        executable = self.resolve()
        if executable is None:
            details = ('The configured uv executable is missing or not executable; ambient PATH was not used.' if self.executable is not None else
                       'uv is not visible on the broker PATH. Configure an absolute installer executable in the broker launcher.')
            raise InstallProblem('installer_unavailable', self.unavailable_message, details)
        return executable
