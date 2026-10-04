"""Real install/broker fixture; substitutes only network checkout with inert source."""
import json
import uuid
import subprocess
from amplifier_unified_mcp.smart_tools import SmartToolsManager
from amplifier_unified_mcp.server import main

BACKEND = '''import zipfile
from pathlib import Path
def get_requires_for_build_wheel(config_settings=None): return []
def build_wheel(wheel_directory,config_settings=None,metadata_directory=None):
 name='boundary_fixture-1.0-py3-none-any.whl'
 with zipfile.ZipFile(Path(wheel_directory)/name,'w') as z:
  z.writestr('boundary_fixture.py',"VALUE='installed'\\n")
  z.writestr('boundary_fixture-1.0.dist-info/METADATA','Metadata-Version: 2.1\\nName: boundary-fixture\\nVersion: 1.0\\n')
  z.writestr('boundary_fixture-1.0.dist-info/WHEEL','Wheel-Version: 1.0\\nGenerator: inert-fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')
  z.writestr('boundary_fixture-1.0.dist-info/RECORD','')
 return name
'''

async def checkout(self, repository, ref=None):
    assert repository == 'https://example.invalid/boundary-fixture'
    assert ref == 'a' * 40
    folder = self.root / 'sources' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    (folder / 'smart-tool.json').write_text(json.dumps({'manifest': 'SMART_TOOL.md', 'cli_argv': ['boundary-fixture']}))
    (folder / 'SMART_TOOL.md').write_text("---\nsmart_tool_format: 1\nname: boundary-fixture\nversion: '1.0'\ndescription: Inert package boundary fixture\n---\n# Retained setup\nCLI help does not need a model. Optional MCP support is tool-owned.\n")
    (folder / 'pyproject.toml').write_text("[project]\nname='boundary-fixture'\nversion='1.0'\nrequires-python='>=3.13'\n[build-system]\nrequires=[]\nbuild-backend='backend'\nbackend-path=['.']\n")
    (folder / 'backend.py').write_text(BACKEND)
    def git(*args):
        return subprocess.run(['git', '-C', str(folder), *args], check=True, capture_output=True).stdout.decode().strip()
    git('init', '-q')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    git('add', '.')
    git('commit', '-qm', 'Exact inert source')
    counter = self.root / 'fixture-checkouts'
    counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))
    return folder, git('rev-parse', 'HEAD')

SmartToolsManager._checkout = checkout
main()
