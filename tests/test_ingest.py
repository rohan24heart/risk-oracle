"""Offline orchestration checks: collection and persistence are replaced with fakes."""
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID
import json
import re

import pytest

from test_aave_v3_base_collector import rpc, collect, verified_metadata, NOW
from risk_oracle import ingest
from risk_oracle.config import Settings
from risk_oracle.models import ReserveSnapshot
from risk_oracle.persistence import PersistenceError, WriteResult

SHA = 'a' * 40
SECRET = 'synthetic-private-test-value'


@pytest.fixture
def pipeline(rpc, monkeypatch):
    verified_metadata(rpc)
    snapshot = collect()
    collector = Mock()
    collector.collect.return_value = snapshot
    writer = Mock()
    writer.write.return_value = WriteResult(UUID(int=1), UUID(int=2), (UUID(int=3),))
    monkeypatch.setattr(ingest, 'AaveV3BaseCollector', lambda: collector)
    monkeypatch.setattr(ingest, 'SupabaseWriter', lambda: writer)
    monkeypatch.setattr(ingest, 'load_settings', lambda: Settings(
        base_rpc_url='https://synthetic.test/'+SECRET,
        supabase_url='https://synthetic.supabase.co', supabase_service_key=SECRET))
    return snapshot, collector, writer


def test_one_cycle_uses_real_v01_and_preserves_models(pipeline, monkeypatch):
    from risk_oracle import risk_engine
    monkeypatch.setattr(risk_engine, 'calculate_risk', lambda *args: pytest.fail('Mock scorer invoked'))
    snapshot, collector, writer = pipeline
    result = ingest.run_cycle(code_revision=SHA, run_id='offline-run', clock=lambda: NOW)
    collector.collect.assert_called_once_with(run_id='offline-run', code_revision=SHA)
    writer.write.assert_called_once()
    assessment, stored = writer.write.call_args.args
    assert stored is snapshot
    assert assessment.snapshot_id == snapshot.id
    assert assessment.methodology_version == 'v0.1' and len(assessment.factors) == 4
    assert assessment.score is not None and assessment.confidence == pytest.approx(4/7)
    assert writer.write.call_args.kwargs == {'methodology_version':'v0.1'}
    assert result['status'] == 'ok' and result['evidence_records'] == 1
    assert result['block_number'] == str(snapshot.block.number)
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize('missing', ['base_rpc_url','supabase_url','supabase_service_key'])
def test_missing_configuration_stops_before_live_collection(pipeline, monkeypatch, missing):
    values=dict(base_rpc_url='test',supabase_url='test',supabase_service_key=SECRET)
    values[missing]=''
    monkeypatch.setattr(ingest, 'load_settings', lambda: Settings(**values))
    with pytest.raises(ingest.IngestionError) as exc:
        ingest.run_cycle(code_revision=SHA)
    assert exc.value.stage == 'configuration'
    pipeline[1].collect.assert_not_called()
    pipeline[2].write.assert_not_called()


@pytest.mark.parametrize('stage', ['collection','scoring','persistence'])
def test_failure_is_nonzero_sanitized_and_never_retried(pipeline, monkeypatch, capsys, stage):
    monkeypatch.setenv('GITHUB_SHA', SHA)
    snapshot, collector, writer = pipeline
    if stage == 'collection':
        collector.collect.side_effect = RuntimeError(SECRET)
    elif stage == 'scoring':
        monkeypatch.setattr(ingest, 'assess_snapshot', Mock(side_effect=ValueError(SECRET)))
    else:
        # Avoid depending on the real wall clock for this exception-path test.
        from risk_oracle.scoring_v01 import assess_snapshot
        monkeypatch.setattr(ingest, 'assess_snapshot', lambda *args, **kwargs: assess_snapshot(snapshot, calculated_at=NOW))
        writer.write.side_effect = PersistenceError(SECRET, completed_tables=('assessments',), outcome_unknown=True)
    assert ingest.main() == 1
    captured = capsys.readouterr()
    assert captured.out == '' and SECRET not in captured.err and 'Traceback' not in captured.err
    report=json.loads(captured.err)
    assert report['stage'] == stage
    assert collector.collect.call_count == 1
    assert writer.write.call_count == (1 if stage == 'persistence' else 0)
    if stage == 'persistence':
        assert report['completed_tables'] == ['assessments']
        assert report['commit_outcome_unknown'] is True


def test_unknown_is_persisted_without_fabricating_score(pipeline):
    snapshot, collector, writer = pipeline
    data=snapshot.model_dump()
    del data['observations']['oracle_feed_heartbeat']
    collector.collect.return_value=ReserveSnapshot.model_validate(data)
    result=ingest.run_cycle(code_revision=SHA, clock=lambda: NOW)
    assessment=writer.write.call_args.args[0]
    assert result['status']=='ok' and assessment.score is None
    assert assessment.risk_level=='unknown' and assessment.confidence is None


def test_cli_success_after_write_only(pipeline, monkeypatch, capsys):
    snapshot, collector, writer=pipeline
    from risk_oracle.scoring_v01 import assess_snapshot
    monkeypatch.setenv('GITHUB_SHA',SHA)
    monkeypatch.setattr(ingest,'assess_snapshot',lambda *args,**kwargs: assess_snapshot(snapshot,calculated_at=NOW))
    assert ingest.main()==0
    output=capsys.readouterr()
    assert json.loads(output.out)['status']=='ok' and output.err==''
    writer.write.assert_called_once()


def test_missing_revision_fails_closed(pipeline, monkeypatch, capsys):
    monkeypatch.delenv('GITHUB_SHA',raising=False)
    assert ingest.main()==1
    assert json.loads(capsys.readouterr().err)['stage']=='configuration'
    pipeline[1].collect.assert_not_called()


def test_workflow_schedule_safety_and_runtime_contract():
    text=(Path(__file__).resolve().parents[1]/'.github/workflows/ingest.yml').read_text(encoding='utf-8-sig')
    assert re.search(r"^    - cron: ['\"]7,22,37,52 \* \* \* \*['\"]$",text,re.M)
    assert re.search(r'^  workflow_dispatch:\s*$',text,re.M)
    assert 'pull_request' not in text and 'push:' not in text
    assert 'group: production-base-usdc-ingestion' in text and 'cancel-in-progress: false' in text
    assert 'contents: read' in text and 'write-all' not in text
    assert "if: github.ref == format('refs/heads/{0}', github.event.repository.default_branch)" in text
    assert "python-version: '3.12'" in text and 'timeout-minutes: 10' in text
    assert 'persist-credentials: false' in text
    assert len(re.findall(r'uses: actions/(?:checkout|setup-python)@[0-9a-f]{40}',text))==2
    assert 'run: python -m pip install --disable-pip-version-check .' in text
    assert '[dev]' not in text and 'continue-on-error' not in text
    steps=text.split('      - name:')
    secret_steps=[step for step in steps if '${{ secrets.' in step]
    assert len(secret_steps)==1
    assert 'run: python -m risk_oracle.ingest' in secret_steps[0]
    assert set(re.findall(r'\$\{\{ secrets\.([A-Z_]+) \}\}',text))=={'BASE_RPC_URL','SUPABASE_URL','SUPABASE_SERVICE_KEY'}
    assert 'echo ' not in text and 'set -x' not in text and 'upload-artifact' not in text


@pytest.mark.parametrize("http_status,code,table", [(403,"42501","assessments"), (None,None,None)])
def test_cli_preserves_safe_persistence_diagnostic(pipeline, monkeypatch, capsys, http_status, code, table):
    snapshot, collector, writer = pipeline
    from risk_oracle.scoring_v01 import assess_snapshot
    monkeypatch.setenv('GITHUB_SHA',SHA)
    monkeypatch.setattr(ingest,'assess_snapshot',lambda *args,**kwargs: assess_snapshot(snapshot,calculated_at=NOW))
    writer.write.side_effect = PersistenceError(
        "Invalid Supabase service key configuration.", table=table, http_status=http_status, error_code=code)
    assert ingest.main() == 1
    output = capsys.readouterr()
    report = json.loads(output.err)
    assert report['table'] == table and report['http_status'] == http_status and report['error_code'] == code
    assert report['message']
    assert report['completed_tables'] == [] and report['commit_outcome_unknown'] is False
    assert SECRET not in output.err and output.out == ''
    assert writer.write.call_count == 1
