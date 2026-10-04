"""Disposable installer fixtures; no downloads, real packages or model calls."""
import json
import sys
import pytest
from amplifier_unified_mcp.broker import Broker
from amplifier_unified_mcp.smart_tools import SmartToolsManager
from amplifier_unified_mcp.smart_tool_installation import InstallProblem, ManagedInstaller
from test_installation_runtime import tool


def executable(tmp_path):
    path=tmp_path/'approved uv'
    path.write_text('#!/bin/sh\nexit 99\n')
    path.chmod(0o700)
    return path


@pytest.mark.parametrize('configuration',[{}, {'executable':'uv'}, {'executable':'/tmp/uv','extra':True}, {'executable':None}, {'executable':'/tmp/uv\n'}, {'executable':'/'+'x'*4096}])
def test_installer_configuration_is_absolute_bounded_and_not_tool_arguments(configuration):
    with pytest.raises(ValueError,match='Installer'):ManagedInstaller(configuration)


async def test_missing_installer_prevents_checkout_and_preserves_originals(tmp_path,monkeypatch):
    monkeypatch.setenv('PATH',str(tmp_path/'empty'))
    m=SmartToolsManager(tmp_path/'state')
    async def forbidden(*_):raise AssertionError('No checkout or installer execution admitted')
    m._checkout=m._run=forbidden
    try:
        m.state['installations']=[{'id':'original','status':'installed'}]
        before=list(m.root.iterdir())
        ready=m.installer.readiness()
        assert ready['status']=='unavailable' and ready['source']=='path'
        assert 'enable' not in ready['message'] and 'configure the installer location' in ready['message']
        with pytest.raises(InstallProblem) as failed:await m.install({'repository':'https://example.invalid/tool'})
        assert failed.value.code=='installer_unavailable'
        assert list(m.root.iterdir())==before and m.state['installations']==[{'id':'original','status':'installed'}]
        assert not m.connections
    finally:await m.close();m.db.close()


@pytest.mark.parametrize('kind',['not-executable','missing','directory'])
async def test_explicit_unavailable_executable_never_falls_back_to_path(tmp_path,monkeypatch,kind):
    configured=executable(tmp_path)
    if kind=='not-executable':configured.chmod(0o600)
    elif kind=='missing':configured.unlink()
    else:configured.unlink();configured.mkdir()
    m=SmartToolsManager(tmp_path/'state',installer_config={'executable':str(configured)})
    def forbidden(*_):raise AssertionError('Explicit installer cannot fall back to PATH')
    monkeypatch.setattr('amplifier_unified_mcp.smart_tool_installation.shutil.which',forbidden)
    try:
        assert m.installer.readiness()['source']=='configured'
        assert m.installer.readiness()['status']=='unavailable'
        with pytest.raises(InstallProblem,match='ambient PATH was not used'):
            await m.install({'repository':'https://example.invalid/tool'})
    finally:await m.close();m.db.close()


async def test_readiness_ui_agent_and_held_intake_is_passive(tmp_path,monkeypatch):
    uv=executable(tmp_path);monkeypatch.delenv('PATH',raising=False)
    b=Broker(tmp_path/'state',installer_config={'executable':str(uv)})
    try:
        c=dict(fenceId='held',commandId='maintenance',purpose='distribution-update',instanceId='fixture',dataScope='fixture')
        b.quiescence.acquire('mcp',c)
        before=b.db.total_changes
        ui=await b.action('host','smartTools.installerReadiness',{},'ui-read',origin='ui')
        agent=await b.action('host','smartTools.installerReadiness',{},'agent-read',origin='agent')
        assert ui==agent and ui['read'] is True
        assert ui['result']['status']=='available' and ui['result']['source']=='configured'
        assert b.db.total_changes==before and not b.db.execute('SELECT 1 FROM commands').fetchone()
        assert b.quiescence.current()['fenceId']=='held'
        assert not b.manager.connections
        from jsonschema import ValidationError
        with pytest.raises(ValidationError):
            await b.action('host','smartTools.installerReadiness',{'executable':'/browser/chosen/uv'},'forged-config')
    finally:await b.close()


@pytest.mark.parametrize('requirement',['>=3.13,<3.14','>=3.12,!=3.13.*',None])
async def test_explicit_installer_drives_python_selection_without_ambient_path(tmp_path,monkeypatch,requirement):
    uv=executable(tmp_path);monkeypatch.setenv('PATH',str(tmp_path/'empty'))
    m=SmartToolsManager(tmp_path/'state',installer_config={'executable':str(uv)})
    checkout=tmp_path/'checkout';tool(checkout,"[project]\nname='example'\nversion='1'\n"+(f"requires-python='{requirement}'\n" if requirement else ''))
    calls=[]
    async def cloned(*_):return checkout,'a'*40
    async def run(argv,**kw):calls.append((argv,kw));return ''
    m._checkout=cloned;m._run=run
    try:
        result=await m.install({'repository':'https://example.invalid/tool'})
        runtime,package=calls
        assert runtime[0][0]==package[0][0]==str(uv)
        assert runtime[0][runtime[0].index('--python')+1]==(requirement or sys.executable)
        assert '--no-project' in runtime[0] and '--no-config' in runtime[0]
        assert package[0][package[0].index('--python')+1]==result['python']
        assert '--no-config' in package[0]
        assert not any(k in runtime[1]['env'] for k in ['OPENAI_API_KEY','ANTHROPIC_API_KEY','AZURE_OPENAI_API_KEY'])
    finally:await m.close();m.db.close()
