"""Catalog pointers bind metadata; malformed snapshots stay attributable."""
import copy
import json
from pathlib import Path
import shutil

import pytest

from amplifier_unified_mcp.catalog_snapshots import entry
from amplifier_unified_mcp.smart_tools import SmartToolsManager


def write(root, name='example', *, source=None, provenance=None):
    folder=root/'tools'/name;folder.mkdir(parents=True,exist_ok=True)
    source=source or {'repository':'https://example.invalid/tool','path':'packages/tool'}
    provenance=provenance or {'source':{'repository':source['repository'],'ref':source.get('ref','main'),'path':source.get('path','.'),'commit':'a'*40},'original_manifest_path':'packages/tool/src/SMART_TOOL.md','last_success':'2026-10-04T04:35:45Z'}
    (folder/'source.json').write_text(json.dumps(source));(folder/'provenance.json').write_text(json.dumps(provenance))
    (folder/'SMART_TOOL.md').write_text("---\nsmart_tool_format: 1\nname: Example\nversion: '1'\ndescription: Pointer-bound fixture\n---\n")
    return folder/'source.json'


def test_pointer_defaults_and_provenance_timestamp_are_exposed_without_currency_claim(tmp_path):
    source=write(tmp_path);row=entry(source,tmp_path)
    assert row['ref']=='main' and row['path']=='packages/tool'
    assert row['snapshot']=={'status':'verified','commit':'a'*40,'originalManifestPath':'packages/tool/src/SMART_TOOL.md','lastSuccess':'2026-10-04T04:35:45Z','freshness':'not_checked_against_upstream'}
    assert row['manifest']['name']=='Example'


@pytest.mark.parametrize('field,value',[('repository','https://example.invalid/another'),('repository','https://example.invalid/tool/'),('ref','HEAD'),('path','.'),('commit','main'),('commit',None)])
def test_provenance_mismatch_cannot_present_unverified_manifest(tmp_path,field,value):
    source=write(tmp_path);p=source.with_name('provenance.json');pro=json.loads(p.read_text());pro['source'][field]=value;p.write_text(json.dumps(pro))
    with pytest.raises(ValueError):entry(source,tmp_path)


@pytest.mark.parametrize('field,value',[('last_success',None),('last_success','not a date'),('last_success','2026-10-04T04:35:45'),('original_manifest_path','../private'),('original_manifest_path','/private'),('original_manifest_path','other-package/SMART_TOOL.md'),('original_manifest_path','packages/tool/../SMART_TOOL.md'),('original_manifest_path','packages\\tool\\SMART_TOOL.md')])
def test_missing_or_unsafe_attribution_is_refused(tmp_path,field,value):
    source=write(tmp_path);p=source.with_name('provenance.json');pro=json.loads(p.read_text());pro[field]=value;p.write_text(json.dumps(pro))
    with pytest.raises(ValueError):entry(source,tmp_path)


def test_missing_oversized_duplicate_and_symlink_metadata_refuse(tmp_path):
    for name in ('source.json','provenance.json','SMART_TOOL.md'):
        root=tmp_path/name.replace('.','-');source=write(root);target=source.with_name(name);original=target.read_bytes()
        target.unlink()
        with pytest.raises(ValueError):entry(source,root)
        target.write_bytes(b'x'*256001)
        with pytest.raises(ValueError):entry(source,root)
        target.unlink();outside=tmp_path/('outside-'+name);outside.write_bytes(original);target.symlink_to(outside)
        with pytest.raises(ValueError):entry(source,root)
    source=write(tmp_path/'duplicate');source.write_text('{"repository":"https://example.invalid/tool","repository":"https://example.invalid/other"}')
    with pytest.raises(ValueError,match='Duplicate'):entry(source,source.parents[2])


def test_credential_url_and_escaping_package_pointer_refuse(tmp_path):
    for n,source in enumerate([{'repository':'https://user:pass@example.invalid/tool'},{'repository':'https://example.invalid/tool','path':'../private'},{'repository':'https://example.invalid/tool','ref':'--option'}]):
        p=write(tmp_path/str(n),source=source)
        with pytest.raises(ValueError):entry(p,p.parents[2])


@pytest.fixture
async def manager(tmp_path):
    value=SmartToolsManager(tmp_path/'state');yield value
    await value.close();value.db.close()


async def test_partial_catalog_names_failed_snapshot_instead_of_silently_skipping(manager,tmp_path,monkeypatch):
    checkout=tmp_path/'catalog';good=write(checkout,'good');bad=write(checkout,'bad');bad.with_name('provenance.json').unlink()
    async def clone(*_):return checkout,'b'*40
    monkeypatch.setattr(manager,'_checkout',clone)
    result=await manager.catalog()
    assert [x['id'] for x in result['items']]==['good']
    assert result['complete'] is False and result['truncated'] is False and result['isError'] is True
    assert result['problems'][0]['id']=='bad' and result['problems'][0]['code']=='snapshot_unverified'
    assert manager.state['catalog']==result['items'] and not manager.connections


async def test_all_invalid_catalog_retains_previous_inventory(manager,tmp_path,monkeypatch):
    checkout=tmp_path/'catalog';source=write(checkout,'bad');source.with_name('provenance.json').unlink()
    manager.state['catalog']=[{'id':'original','status':'listed'}];original=copy.deepcopy(manager.state['catalog'])
    async def clone(*_):return checkout,'b'*40
    monkeypatch.setattr(manager,'_checkout',clone)
    await manager.command('smartTools.catalog',{},'failed-catalog')
    receipt=manager.operation('failed-catalog')
    assert receipt['status']=='failed' and receipt['outcome']=='confirmed'
    assert receipt['result']['problems'][0]['id']=='bad'
    assert 'Previous inventory was retained.' in receipt['error']
    assert manager.state['catalog']==original


async def test_catalog_limit_is_partial_not_complete(manager,tmp_path,monkeypatch):
    checkout=tmp_path/'catalog'
    for i in range(201):write(checkout,f'entry-{i:03d}')
    async def clone(*_):return checkout,'b'*40
    monkeypatch.setattr(manager,'_checkout',clone)
    result=await manager.catalog()
    assert len(result['items'])==200 and result['truncated'] is True and result['complete'] is False
    assert not result['problems']
