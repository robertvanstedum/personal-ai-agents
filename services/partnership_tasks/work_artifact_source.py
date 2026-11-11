"""Host-kept conversation source for private working-document writes."""
from core.canonical_json import digest
from core.workshop_journal import artifacts
from services.partnership_tasks.client import command


def prepare_source(*, source, words, environment, scope):
    if source.get('store') not in ('mc-floor', 'cos-history') or not isinstance(words, str) or not words.strip():
        raise ValueError('kept owner source required')
    key = digest([source, words])[7:39]
    sid = 'SE-work-' + key
    # Scrub before truncation so a cut credential cannot evade recognition.
    kept = artifacts.prepare(words.encode(), source_class='conversation').data.decode()
    content = kept[:7800] + '\n[Full canonical source sha256: ' + digest(words) + ']'
    return sid, command('source_event.record', 'source_event', sid,
                        {'source': source, 'content': content, 'correction_of': None},
                        command_id='cmd-work-source-' + key, environment=environment, scope=scope)
