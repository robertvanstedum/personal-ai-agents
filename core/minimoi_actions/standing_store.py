"""Commit a standing brief and its ingress receipt in one PostgreSQL transaction.

Uses the existing FloorStores note operation, with a borrowed transaction.
No conversational model invocation, foreground lease or arbitrary destination.
S2 finding import is deliberately deferred, not represented as a fake task.
"""

import hashlib

from .binding_store import BindingStore
from .context import ActionContractError
from .standing import StandingEvent, ProducerPolicy, ack, brief_text


class StandingStore:
    def __init__(self, connect):
        # Reuse the bounded transaction/error handling of the harness store;
        # this does not require or touch any runtime binding rows.
        self._transactions = BindingStore(connect)

    def accept(self, event: StandingEvent, policy: ProducerPolicy, load_payload) -> tuple[dict, int]:
        destination = event.destination(policy)
        data = event.data
        scope = (policy.environment, policy.producer_id, data['event_id'])
        with self._transactions._transaction() as conn, conn.cursor() as cur:
            cur.execute('''INSERT INTO guild.mc_standing_events
                (environment,producer_id,event_id,principal_id,event_digest,payload_digest,state)
                VALUES (%s,%s,%s,%s,%s,%s,'prepared') ON CONFLICT DO NOTHING RETURNING event_id''',
                scope + (policy.principal_id, event.digest, data['payload_digest']))
            inserted = cur.fetchone() is not None
            if not inserted:
                cur.execute('''SELECT principal_id,event_digest,state,record_id
                    FROM guild.mc_standing_events WHERE environment=%s AND producer_id=%s
                    AND event_id=%s FOR UPDATE''', scope)
                owner, digest, state, record = cur.fetchone()
                if owner != policy.principal_id:
                    raise ActionContractError('standing_producer_owner_mismatch')
                if digest != event.digest:
                    # Quarantine the conflicting input by retaining its digest;
                    # never overwrite the original accepted receipt/content.
                    cur.execute('''UPDATE guild.mc_standing_events SET conflict_count=conflict_count+1,
                        last_conflict_digest=%s WHERE environment=%s AND producer_id=%s AND event_id=%s''',
                        (event.digest,) + scope)
                    return ack(data['event_id'], 'refused', 'event_id_reused'), 409
                if state == 'accepted':
                    return ack(data['event_id'], 'already_applied', 'brief_kept', record), 200
            if data['kind'] != 'brief':
                cur.execute('''UPDATE guild.mc_standing_events SET state='deferred'
                    WHERE environment=%s AND producer_id=%s AND event_id=%s''', scope)
                return ack(data['event_id'], 'deferred', 'work_import_unavailable'), 200

            # Resolve only a pre-registered object in this producer's scope.
            # The injected resolver must bound reads/deadlines; no URL/path
            # resolution is implemented in the event or HTTP parser.
            content = brief_text(load_payload(policy, data['payload_ref']), data['payload_digest'])
            from minimoi_portal.guild_ui.payment_scrub import scrub
            from minimoi_portal.guild_ui.stores import FloorStores, Author
            content = scrub(content)
            if not content.strip() or len(content) > 4000:
                raise ActionContractError('standing_payload_invalid')
            request_key = 'standing-' + hashlib.sha256(
                '\0'.join(scope).encode('utf-8')).hexdigest()
            floor = FloorStores(lambda: None, floor=destination.floor_key)
            result = floor.add_note(request_key, content,
                Author('mc_standing', 'platform', 'MC standing operations'),
                area='Operate', page='mc-operations', transaction=conn)
            if result.outcome != 'kept' or not result.value:
                raise ActionContractError('standing_note_conflict')
            record = result.value['id']
            cur.execute('''UPDATE guild.mc_standing_events SET state='accepted',record_id=%s
                WHERE environment=%s AND producer_id=%s AND event_id=%s''', (record,) + scope)
            return ack(data['event_id'], 'accepted', 'brief_kept', record), 200

    def get(self, event_id: str, policy: ProducerPolicy) -> dict | None:
        with self._transactions._transaction() as conn, conn.cursor() as cur:
            cur.execute('''SELECT state,record_id FROM guild.mc_standing_events
                WHERE environment=%s AND producer_id=%s AND event_id=%s AND principal_id=%s''',
                (policy.environment, policy.producer_id, event_id, policy.principal_id))
            row = cur.fetchone()
            if row is None:
                return None
            state, record = row
            if state == 'accepted':
                return ack(event_id, 'accepted', 'brief_kept', record)
            return ack(event_id, 'deferred', 'work_import_unavailable')
