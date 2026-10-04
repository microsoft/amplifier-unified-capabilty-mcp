"""Pointer-bound catalog snapshots. Inspection is static and never fetches tools."""
from datetime import datetime
import json
from pathlib import Path, PurePosixPath
import re


def relative(value, *, root=False):
    if root and value == '.':
        return value
    if not isinstance(value, str) or not value or '\\' in value or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid repository-relative catalog path.')
    if PurePosixPath(value).is_absolute() or any(part in ('', '.', '..') for part in value.split('/')):
        raise ValueError('Invalid repository-relative catalog path.')
    return value


def read(path, checkout, limit):
    path.relative_to(checkout)
    current = path
    while current != checkout:
        if current.is_symlink():
            raise ValueError('Catalog snapshot files cannot be symlinks.')
        current = current.parent
    if not path.resolve().is_relative_to(checkout.resolve()) or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Catalog snapshot file is unavailable or exceeds its limit.')
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Catalog snapshot file exceeds its limit.')
    return data.decode('utf-8')


def unique(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('Duplicate catalog metadata key.')
        value[key] = item
    return value


def entry(source_path: Path, checkout: Path):
    from .smart_tools import _manifest, _repository
    source = json.loads(read(source_path, checkout, 32_000), object_pairs_hook=unique)
    if not isinstance(source, dict):
        raise ValueError('Invalid source pointer.')
    _repository(source.get('repository'))
    # Preserve pointer spelling: normalization must not manufacture agreement.
    repository = source['repository']
    ref, path = source.get('ref', 'main'), source.get('path', '.')
    if not isinstance(ref, str) or not ref or len(ref) > 200 or ref.startswith('-') or any(c.isspace() for c in ref):
        raise ValueError('Invalid catalog source ref.')
    path = relative(path, root=True)
    provenance = json.loads(read(source_path.with_name('provenance.json'), checkout, 32_000), object_pairs_hook=unique)
    original = provenance.get('source') if isinstance(provenance, dict) else None
    if not isinstance(original, dict) or any(original.get(k) != v for k, v in {'repository':repository,'ref':ref,'path':path}.items()):
        raise ValueError('Snapshot provenance does not match its source pointer.')
    commit = original.get('commit')
    if not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('Snapshot has no exact source commit.')
    manifest_path = relative(provenance.get('original_manifest_path'))
    if path != '.' and not manifest_path.startswith(path + '/'):
        raise ValueError('Snapshot manifest is outside its selected package.')
    last = provenance.get('last_success')
    if not isinstance(last, str) or len(last) > 100 or datetime.fromisoformat(last.replace('Z', '+00:00')).utcoffset() is None:
        raise ValueError('Snapshot has no attributable refresh timestamp.')
    manifest = _manifest(read(source_path.with_name('SMART_TOOL.md'), checkout, 256_000))
    return {'id':source_path.parent.name, 'repository':repository,'ref':ref,'path':path,
            'name':manifest['name'],'description':manifest['description'],'version':manifest['version'],
            'manifest':manifest,'status':'listed', 'snapshot':{'status':'verified','commit':commit,
            'originalManifestPath':manifest_path,'lastSuccess':last,'freshness':'not_checked_against_upstream'}}
