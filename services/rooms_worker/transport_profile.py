"""Explicit laptop Records destinations, independent of user/model arguments."""
# This module also ships in the minimal standalone worker image, which has no portal package.
PRODUCTION_ENDPOINT='https://minimoi.ai/internal/records'
DEV_ENDPOINTS=frozenset({'http://minimoi-records:18880','http://127.0.0.1:18881','http://localhost:18881'})
def validate_endpoint(url,profile='dev'):
 if profile=='production'and url==PRODUCTION_ENDPOINT:return url
 if profile=='dev'and url in DEV_ENDPOINTS:return url
 raise ValueError('records_transport_profile_refused')
def from_env(environ,default):
 profile=environ.get('ROOMS_RECORDS_PROFILE','dev')
 if profile=='production'and environ.get('ROOMS_RECORDS_PRODUCTION')!='1':raise ValueError('records_production_admission_required')
 return validate_endpoint(environ.get('RECORDS_URL',default),profile),profile


def admit_runtime(environ, *, kind):
 """Require explicit production custody/state; never inherit laptop dev homes.
 This validates locators, not credential identity or filesystem provisioning.
 """
 default='http://127.0.0.1:18881' if kind=='connector' else 'http://minimoi-records:18880'
 url,profile=from_env(environ,default)
 if profile!='production':return url,profile
 keys=('ROOMS_CONNECTOR_SECRETS','ROOMS_CONNECTOR_STATE') if kind=='connector' else ('ROOMS_SECRETS_DIR','ROOMS_JOURNAL_DIR','MC_RELAY_TOKEN_FILE')
 if environ.get('ROOMS_MANAGED_LEDGER'):keys+=('ROOMS_MANAGED_LEDGER',)
 from pathlib import PurePosixPath
 for key in keys:
  value=environ.get(key,'')
  path=PurePosixPath(value)
  if (not value or not path.is_absolute() or '..' in path.parts or '~' in value
      or 'minimoi-staging' in path.parts or any(path==root or root in path.parents for root in map(PurePosixPath,('/state','/journal','/run/secrets/rooms')))):
   raise ValueError('records_production_locator_required:'+key)
 # Distinct credentials and state are an operational invariant, not one folder.
 if PurePosixPath(environ[keys[0]])==PurePosixPath(environ[keys[1]]):raise ValueError('records_production_locator_overlap')
 return url,profile
