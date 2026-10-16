"""Canonical observation auth with synthetic credentials; no provider or secret backend."""
import builtins,contextlib,importlib.util,io,json,sys,types
from pathlib import Path
from unittest.mock import Mock
import pytest

@pytest.fixture
def observe(monkeypatch):
 import core.get_secret as secret
 lookup=Mock(side_effect=AssertionError('real secret backend forbidden'));monkeypatch.setattr(secret,'get_secret',lookup)
 provider=types.ModuleType('anthropic');provider.Anthropic=Mock(side_effect=AssertionError('provider construction forbidden'));monkeypatch.setitem(sys.modules,'anthropic',provider)
 original=builtins.__import__
 def no_keyring(name,*args,**kwargs):
  if name=='keyring'or name.startswith('keyring.'):raise ImportError('keyring deliberately unavailable')
  return original(name,*args,**kwargs)
 monkeypatch.setattr(builtins,'__import__',no_keyring)
 # Canonical imports during repository run; private fixture uses adjacent payload.
 here=Path(__file__).resolve().parent
 source=here/'domains/curator/research/agent/observe.py'
 if not source.exists():source=here.parent/'domains/curator/research/agent/observe.py'
 spec=importlib.util.spec_from_file_location('synthetic_observe_auth',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 return module,lookup,provider

def test_import_without_keyring_never_fetches_credentials_or_constructs_provider(observe):
 module,lookup,provider=observe;lookup.assert_not_called();provider.Anthropic.assert_not_called();assert callable(module.main)

def prepare(module,monkeypatch):
 monkeypatch.setattr(sys,'argv',['observe.py','--topic','synthetic','--command','status'])
 monkeypatch.setattr(module,'load_config',lambda:{})

@pytest.mark.parametrize('failure',[None,'empty','exception'])
def test_missing_credentials_fail_fixed_without_secret_values_or_provider(observe,monkeypatch,capsys,failure):
 module,lookup,provider=observe;prepare(module,monkeypatch)
 if failure=='exception':lookup.side_effect=RuntimeError('SYNTHETIC_SENSITIVE_BACKEND_DETAIL')
 else:lookup.side_effect=None;lookup.return_value=''if failure=='empty'else None
 with pytest.raises(SystemExit)as raised:module.main()
 assert raised.value.code==1
 result=capsys.readouterr();assert json.loads(result.out)=={'ok':False,'error':'Anthropic API key unavailable'};assert not result.err
 lookup.assert_called_once_with('ANTHROPIC_API_KEY','anthropic','api_key');provider.Anthropic.assert_not_called()

def test_canonical_helper_arguments_and_key_passed_to_client_without_model_call(observe,monkeypatch):
 module,lookup,provider=observe;prepare(module,monkeypatch);lookup.side_effect=None;lookup.return_value='SYNTHETIC-NONCREDENTIAL'
 class StopBeforeContext(Exception):pass
 provider.Anthropic.side_effect=StopBeforeContext
 with pytest.raises(StopBeforeContext):module.main()
 lookup.assert_called_once_with('ANTHROPIC_API_KEY','anthropic','api_key');provider.Anthropic.assert_called_once_with(api_key='SYNTHETIC-NONCREDENTIAL')
