import json
from types import SimpleNamespace
import pytest
from backend.sandbox.runtimes import detect_stack
from backend.verifier.detect import test_command as detect_tests, tests_passed as passed, install_command
from backend.sandbox.process import Output
from backend.sandbox.patches import protected
from backend.evidence.generator import dockerfile
from backend.orchestrator.local import LocalProvider
from backend.orchestrator.models import Decision, RepositoryContext
from backend.orchestrator.store import Store
from backend.app.server import Application, Submission


@pytest.mark.parametrize('stack,manifest,content', [
    ('go','go.mod','module example.com/app\ngo 1.23\n'),
    ('rust','Cargo.toml','[package]\nname="app"\nversion="0.1.0"\n'),
    ('java','pom.xml','<project/>'), ('dotnet','App.csproj','<Project/>')])
def test_runtime_manifests_and_helpers(tmp_path,stack,manifest,content):
    (tmp_path/manifest).write_text(content)
    (tmp_path/'helper.py').write_text('pass')
    assert detect_stack(tmp_path)==stack
    assert RepositoryContext(repository_url='fixture://x',snapshot='x',stack=stack,files={}).stack==stack
    assert install_command(tmp_path,stack)
    recipe=dockerfile(SimpleNamespace(path=tmp_path,image='example@sha256:digest'),stack,['ls'])
    assert 'USER 65534:65534' in recipe


@pytest.mark.parametrize('text', ['--- PASS: TestSum (0.00s)', 'test result: ok. 2 passed; 0 failed;',
    'Tests run: 1, Failures: 0, Errors: 0, Skipped: 0', 'Passed: 1'])
def test_additional_frameworks_require_successful_exit(text):
    assert passed(Output(0,text,''))
    assert not passed(Output(1,text,''))


@pytest.mark.parametrize('text', ['? package [no test files]', 'test result: ok. 0 passed;', 'Tests run: 0, Failures: 0, Errors: 0', 'Passed: 0'])
def test_zero_tests_never_pass(text):
    assert not passed(Output(0,text,''))


@pytest.mark.parametrize('name',['sum_test.go','src/test/java/FooTest.java','SumTests.cs'])
def test_original_test_files_protected(name):
    assert protected(name)


async def test_local_missing_entrypoint_returns_structured_review():
    result=await LocalProvider().generate('',{'environment':{'stack':'unknown'},'results':[]},Decision)
    assert result.human_reason and not result.commands


def test_resume_missing_checkpoint_and_active_checkpoint_rejected(tmp_path):
    from fastapi import HTTPException
    app=Application(tmp_path)
    rid='a'*32
    with pytest.raises(HTTPException) as missing:
        app.launch(Submission(resume_from=rid))
    assert missing.value.status_code==404


def test_go_requires_actual_test_files(tmp_path):
    (tmp_path/'go.mod').write_text('module example.com/app\n')
    assert not detect_tests(tmp_path,'go')
    (tmp_path/'sum_test.go').write_text('package app')
    assert detect_tests(tmp_path,'go')==['go','test','-v','./...']


async def test_checkpoint_and_scoped_file_loading(tmp_path,monkeypatch):
    from backend.sandbox.execution import LocalExecution
    old=tmp_path/'previous'
    old.mkdir()
    (old/'requirements.txt').write_text('')
    (old/'fixed.py').write_text('answer=42\n')
    async def create(*args): pass
    ex=LocalExecution(tmp_path/'runs',tmp_path/'fixtures')
    monkeypatch.setattr(ex.docker,'create',create)
    context=await ex.prepare('newrun','https://github.com/example/repo',resume_path=old)
    assert context.files['fixed.py']=='answer=42\n'
    assert ex.scoped_files('newrun',['fixed.py','missing.py'])=={'fixed.py':'answer=42\n'}
    (old/'fixed.py').write_text('changed=1\n')
    assert ex.scoped_files('newrun',['fixed.py'])=={'fixed.py':'answer=42\n'}


async def test_passing_baseline_never_calls_a_model():
    import asyncio
    from backend.orchestrator.demo import DemoExecution,demo_context,approve_all
    from backend.orchestrator.controller import Controller
    from backend.orchestrator.providers import Router
    from backend.orchestrator.models import Specialist
    class ForbiddenProvider:
        async def generate(self,*args): raise AssertionError('Baseline must run first')
    ctx=demo_context()
    ctx.files['requirements.txt']='requests\n'
    provider=ForbiddenProvider()
    controller=Controller(Router(provider,{s:provider for s in Specialist}),DemoExecution(ctx),Store(':memory:'),environment={'test_argv':['python','-m','pytest']})
    task=asyncio.create_task(controller.run(ctx))
    await approve_all(controller,task)
    result=await task
    assert result.status=='completed'
    assert not any(e.type=='model.completed' for e in controller.store.events(result.run_id))


def test_report_preserves_failed_attempts(tmp_path):
    from backend.sandbox.execution import LocalExecution
    from backend.orchestrator.models import RunView
    store=Store(':memory:')
    store.save(RunView(run_id='report',status='stopped',reason='missing dependency'))
    store.emit('report','execution.started',argv=['go','test','./...'])
    store.emit('report','execution.output',exit_code=1,stderr='dependency unavailable')
    ex=LocalExecution(tmp_path/'runs',tmp_path/'fixtures',store)
    ex.finalize('report',{'max_steps':100,'repository_url':'https://github.com/example/repo'})
    text=(ex.evidence_dir('report')/'REPORT.md').read_text()
    assert 'go test ./...' in text and 'dependency unavailable' in text and '/100' in text
