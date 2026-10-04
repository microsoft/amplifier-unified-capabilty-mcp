"""Actual broker; replace only catalog network checkout with static fixtures."""
import json
import uuid
from amplifier_unified_mcp.smart_tools import SmartToolsManager, CATALOG_REPOSITORY
from amplifier_unified_mcp.server import main

async def checkout(self, repository, ref=None):
    assert repository == CATALOG_REPOSITORY and ref == 'main'
    root=self.root/'sources'/uuid.uuid4().hex
    for name in ('verified','mismatch'):
        folder=root/'tools'/name;folder.mkdir(parents=True)
        pointer={'repository':'https://example.invalid/'+name}
        provenance={'source':{**pointer,'ref':'main','path':'.','commit':'a'*40},'original_manifest_path':'SMART_TOOL.md','last_success':'2026-10-04T04:35:45Z'}
        if name=='mismatch':provenance['source']['repository']='https://example.invalid/different'
        (folder/'source.json').write_text(json.dumps(pointer))
        (folder/'provenance.json').write_text(json.dumps(provenance))
        (folder/'SMART_TOOL.md').write_text(f"---\nsmart_tool_format: 1\nname: {name}\nversion: '1'\ndescription: Static catalog fixture\n---\n")
    count=self.root/'fixture-catalog-checkouts';count.write_text(str(int(count.read_text())+1 if count.exists() else 1))
    return root,'b'*40

SmartToolsManager._checkout=checkout
main()
