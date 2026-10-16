import importlib.util,sys
from pathlib import Path
import pytest
PATH=Path(__file__).resolve().parents[1]/'domains/curator/research/paths.py'
spec=importlib.util.spec_from_file_location('candidate_research_paths',PATH);module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)

def test_code_data_state_tips_are_independent_without_creating_files(tmp_path):
 repo=tmp_path/'repo';data=tmp_path/'private-data';state=tmp_path/'private-history'
 paths=module.configured({'CURATOR_RESEARCH_DATA_DIR':str(data),'CURATOR_RESEARCH_STATE_DIR':str(state)},repo=repo)
 assert paths.code==repo/'domains/curator/research' and paths.data==data
 assert paths.config==state/'agent/config.json' and paths.library==state/'library'
 assert paths.tips==repo/'config/curator/tips.json'
 assert not any(p.exists() for p in (repo,data,state))

@pytest.mark.parametrize('value',['data/dives/topic-001.md','topics/topic/session-001.md','library/session-log.md','agent/config.json'])
def test_old_relative_and_absolute_labels_preserved_in_memory(tmp_path,value):
 paths=module.configured({},repo=tmp_path)
 result=paths.saved(value)
 assert result==paths.saved('/app/_NewDomains/research-intelligence/'+value)
 assert result==paths.saved(str(tmp_path/'_NewDomains/research-intelligence'/value))
 if value!='agent/config.json':assert paths.saved_label(result)==value
 assert not result.exists()

@pytest.mark.parametrize('value',['../secret','data/../secret','/etc/passwd','/app/_NewDomains/other/data/x','agent/other.json','topics','data\\x'])
def test_history_paths_do_not_enable_arbitrary_file_access(tmp_path,value):
 with pytest.raises(ValueError):module.configured({},repo=tmp_path).saved(value)

@pytest.mark.parametrize('env',[{'CURATOR_RESEARCH_DATA_DIR':'relative'},{'CURATOR_RESEARCH_STATE_DIR':'/x/../y'}])
def test_untrusted_relative_roots_refused(tmp_path,env):
 with pytest.raises(ValueError):module.configured(env,repo=tmp_path)

def test_private_root_cannot_be_packaged_code(tmp_path):
 with pytest.raises(ValueError):module.configured({'CURATOR_RESEARCH_DATA_DIR':str(tmp_path/'domains/curator/research/data')},repo=tmp_path)
