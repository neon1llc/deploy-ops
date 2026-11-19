# tests/test_artifacts.py - Measure exact retained bytes without treating inventory or scanner output as release authority.


import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deploy_ops.cli import dispatch,parser
from deploy_ops.console import create_app
from deploy_ops.delivery import artifacts,backup
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.process import Process
from deploy_ops.delivery.scaffold import create
from deploy_ops.errors import Refused


# Leave a real reviewed static artifact waiting, proving collection needs no deployment or fresh approval
@pytest.fixture
def scene(tmp_path):
    project=create(tmp_path/'workspace')
    build=project.parent/'application/build.py'
    build.write_text(build.read_text()+"\nPath('dist/requirements.txt').write_text('requests==2.31.0\\n')\n")
    engine=Engine(project)
    release=engine.advance(engine.create('development')['id'])
    engine.approve(release['id'],'foundation',release['nodes']['foundation']['request_digest'])
    release=engine.advance(release['id'])
    assert release['status']=='waiting' and release['nodes']['web']['artifact_digest']
    return engine,release


# Keep native analyzer behavior independent from real source preparation and the actual ledger
@pytest.fixture
def native(monkeypatch):
    class Native(Process):
        calls=[]
        after_scan=None
        empty=False
        unavailable=False

        # Emit only known outputs while verifying tool credentials/configuration remain private
        def run(self,argv,cwd,timeout=600,capture=False,input_text=None):
            self.calls.append(argv)
            assert capture and not any(key.startswith(('AWS_','GOOGLE_','CLOUDSDK_AUTH','DEPLOY_OPS_','ARM_')) for key in self.env)
            assert Path(self.env['HOME']).parent==cwd
            if self.unavailable:raise Refused('native tool unavailable')
            if argv[:2]==['syft','version']:return 'Version: 1.51.1\n'
            if argv[:2]==['trivy','version']:
                return json.dumps({'Version':'0.74.0','VulnerabilityDB':{'UpdatedAt':'2026-01-01T00:00:00Z','NextUpdate':'2030-01-01T00:00:00Z'}})
            if argv[:2]==['syft','scan']:
                assert self.env['SYFT_CHECK_FOR_APP_UPDATE']=='false'
                path=Path(argv[argv.index('-o')+1].split('=',1)[1])
                path.write_text(json.dumps({'bomFormat':'CycloneDX','specVersion':'1.7','components':[] if self.empty else
                    [{'name':'requests','version':'2.31.0','type':'library'}, {'name':'PRIVATE-NATIVE-PATH-SENTINEL','type':'file'}]}))
            else:
                assert argv[:2]==['trivy','fs'] and argv[argv.index('--scanners')+1]=='vuln'
                assert '--offline-scan' in argv and '--disable-telemetry' in argv and '--skip-java-db-update' in argv
                Path(argv[argv.index('--output')+1]).write_text(json.dumps({'SchemaVersion':2,'Results':[] if self.empty else
                    [{'Packages':[{'Name':'requests'}],'Vulnerabilities':[{'Severity':'HIGH','Title':'PRIVATE-NATIVE-SENTINEL'}]}]}))
            if self.after_scan:self.after_scan()
            return ''
    monkeypatch.setattr(artifacts,'Process',Native)
    return Native


# Bind actual prepared/site identities, separate package/file counts and offline vulnerability coverage
def test_collect_exact_waiting_artifact_without_authority_change(scene,native,tmp_path,monkeypatch):
    engine,release=scene
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY','PRIVATE-AMBIENT-SENTINEL')
    before=engine.ledger.store.audit()
    result=artifacts.collect(engine.state,release['id'],'web',tmp_path/'evidence',artifacts.Profile(vulnerabilities=True),engine.path.parent)
    report=result['report']
    assert result['verified'] and report['prepared_sha256']==release['nodes']['web']['artifact_digest']
    assert report['inventory']['package_count']==1 and report['inventory']['file_count']==1
    assert report['vulnerabilities']['severity_counts']['HIGH']==1
    assert report['vulnerabilities']['database_freshness']=='current'
    assert report['authenticity']=='not_checked' and report['policy']=='not_evaluated'
    assert 'PRIVATE-' not in json.dumps(report)
    assert engine.ledger.store.audit()==before and engine.ledger.get(release['id'])==release
    trivy=next(call for call in native.calls if call[:2]==['trivy','fs'])
    assert '--skip-db-update' in trivy
    assert all(path.stat().st_mode&0o077==0 for path in (tmp_path/'evidence').glob('*.json'))
    assert dispatch(parser().parse_args(['artifact','inspect',str(tmp_path/'evidence')]))==result


# Invalid current configuration/source availability never turns artifact inspection into a new Engine
def test_cli_retained_collect_with_invalid_current_configuration(scene,native,tmp_path,monkeypatch):
    engine,release=scene;engine.path.write_text('not valid TOML [[[')
    def forbidden(*args,**kwargs):raise AssertionError('artifact read bootstrapped an Engine')
    monkeypatch.setattr(Engine,'__init__',forbidden)
    args=['--project',str(engine.path),'--state',str(engine.state),'artifact','collect',release['id'],'--component','web','--out',str(tmp_path/'evidence')]
    result=dispatch(parser().parse_args(args));assert result['report']['inventory']['package_count']==1


# Empty analyzer output and unavailable tooling never become clean, signed or policy-admitted artifacts
@pytest.mark.parametrize('condition',['empty','unavailable'])
def test_no_data_and_native_failure_are_truthful(scene,native,tmp_path,condition):
    setattr(native,condition,True)
    engine,release=scene
    result=artifacts.collect(engine.state,release['id'],'web',tmp_path/'evidence',artifacts.Profile(vulnerabilities=True))
    expected='no_data' if condition=='empty' else 'unavailable'
    assert result['report']['inventory']['status']==result['report']['vulnerabilities']['status']==expected
    assert result['report']['policy']=='not_evaluated' and result['report']['trusted_signer']=='not_checked'


# A moved inspection copy may measure its own frozen bytes without redirecting original targets or executing
def test_restored_frozen_artifact_measurement_remains_inspection_only(scene,native,tmp_path):
    engine,release=scene
    backup.capture(engine.path,engine.state,tmp_path/'backup')
    restored=backup.restore(tmp_path/'backup',tmp_path/'restored')
    result=artifacts.collect(Path(restored['state']),release['id'],'web',tmp_path/'evidence',artifacts.Profile())
    assert result['verified']
    with pytest.raises(Refused,match='inspection-only'):Engine(Path(restored['project']))
    with pytest.raises(Refused):artifacts.collect(Path(restored['state']),release['id'],'web',tmp_path/'restored/evidence',artifacts.Profile())


# Report directories and source/state trees are never overwrite destinations
@pytest.mark.parametrize('choice',['existing','state','workspace'])
def test_preserve_existing_and_owned_paths(scene,native,tmp_path,choice):
    engine,release=scene
    out={'existing':tmp_path/'existing','state':engine.state/'new','workspace':engine.path.parent/'new'}[choice]
    if choice=='existing':out.mkdir();(out/'owner.txt').write_text('preserve')
    with pytest.raises(Refused):artifacts.collect(engine.state,release['id'],'web',out,artifacts.Profile(),engine.path.parent)
    assert not native.calls
    if choice=='existing':assert (out/'owner.txt').read_text()=='preserve'
    else:assert not out.exists()


# Changes before a tool prevent launch; changes during collection cannot publish completed evidence
@pytest.mark.parametrize('when',['before','during'])
def test_changed_subject_has_no_completed_report(scene,native,tmp_path,when):
    engine,release=scene
    target=engine.state/'releases'/release['id']/'web/artifacts/site/index.html'
    if when=='before':target.write_text('changed')
    else:native.after_scan=staticmethod(lambda:target.write_text('changed'))
    with pytest.raises(Refused):artifacts.collect(engine.state,release['id'],'web',tmp_path/'evidence',artifacts.Profile())
    assert not (tmp_path/'evidence/report.json').exists()
    if when=='before':assert not native.calls


# Hash, format and contradictory check changes refuse offline without running another command
@pytest.mark.parametrize('change',['bytes','format','check'])
def test_changed_report_refused_without_native_activity(scene,native,tmp_path,change):
    engine,release=scene;out=tmp_path/'evidence'
    artifacts.collect(engine.state,release['id'],'web',out,artifacts.Profile())
    if change=='bytes':(out/'inventory.cdx.json').write_text('{}')
    else:
        from deploy_ops.config import fingerprint
        path=out/'report.json';value=json.loads(path.read_text())
        if change=='format':value['version']=2
        else:value['inventory']['tool']='trivy'
        value['sha256']=fingerprint({k:v for k,v in value.items() if k!='sha256'});path.write_text(json.dumps(value))
    native.calls.clear()
    with pytest.raises(Refused):artifacts.inspect(out)
    assert not native.calls


# Explicit advisory consent changes only DB retrieval; target/dependency network and telemetry stay disabled
def test_explicit_database_network_and_authenticated_api(scene,native,tmp_path):
    engine,release=scene;client=TestClient(create_app(engine,'x'*43),base_url='http://127.0.0.1:8765');path='/api/releases/'+release['id']+'/artifacts/collect'
    body={'component':'web','output_directory':str(tmp_path/'evidence'),'inventory':False,'vulnerabilities':True,'allow_network':True}
    assert client.post(path,json=body).status_code==401
    headers={'Authorization':'Bearer '+'x'*43}
    assert client.post(path,json={**body,'allow_network':'true'},headers=headers).status_code==422
    assert client.post(path,json={**body,'output_directory':'relative'},headers=headers).status_code==422
    response=client.post(path,json=body,headers=headers);assert response.status_code==200,response.text
    trivy=next(call for call in native.calls if call[:2]==['trivy','fs'])
    assert '--skip-db-update' not in trivy and '--offline-scan' in trivy and '--disable-telemetry' in trivy
    inspect_path=path.rsplit('/',1)[0]+'/inspect'
    assert client.post(inspect_path,json=body,headers=headers).json()==response.json()
    assert client.post(inspect_path,json={**body,'component':'foundation'},headers=headers).status_code==409
    assert client.get('/artifacts.js').status_code==200


# No selection and meaningless network consent fail before creating files or running tools
@pytest.mark.parametrize('profile',[artifacts.Profile(inventory=False),artifacts.Profile(allow_network=True)])
def test_empty_or_invalid_profile_has_no_effect(scene,native,tmp_path,profile):
    engine,release=scene
    with pytest.raises(Refused):artifacts.collect(engine.state,release['id'],'web',tmp_path/'evidence',profile)
    assert not native.calls and not (tmp_path/'evidence').exists()


# Execute the shipped client contract with Node rather than treating its source as browser proof
def test_artifact_client_contract():
    import subprocess
    result=subprocess.run(['node','tests/artifacts_ui.cjs'],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
