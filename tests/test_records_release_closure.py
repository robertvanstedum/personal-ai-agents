import importlib.util,pathlib
candidate=pathlib.Path(__file__).parents[1]/'scripts/ci/classify_release.py';s=importlib.util.spec_from_file_location('records_classifier_candidate',candidate);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def test_records_runtime_never_hidden_as_prototype_docs():
 for name in ('app.py','store.py','portal_identity.py','static/app.js','templates/index.html','archive.py'):
  assert m.classify(['prototype-lab/projects/project-records-room-poc/'+name])==('full',m.ALL_SERVICES)
def test_records_tests_docs_and_other_prototype_remain_nondeploying():
 for path in ('prototype-lab/projects/project-records-room-poc/tests/test_identity.py','prototype-lab/projects/project-records-room-poc/README.md','prototype-lab/projects/project-iot-connect/app/main.py'):
  assert m.classify([path])==('documents',())
def test_machine_module_is_real_portal_consumer_no_invented_records_service():
 assert m.classify(['minimoi_portal/records_machine_bridge.py'])==('domain',('portal',));assert 'records'not in m.ALL_SERVICES
