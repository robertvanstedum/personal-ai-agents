"""Service-origin helper continuation; no owner turn or action grant is fabricated.

Host.lane(op, packet) must participate in the SAME exclusion as normal chat,
refuse while the original turn is unsettled, and yield None when busy. A lane
supplies bounded context(), reserve(), review(), accept(), and publish().
reserve() consumes the already-admitted shared task allowance before inference.
accept/publish must revalidate source, result and stop state at their own commit
boundary. publish is idempotent on op and returns only a read-back canonical
answer receipt. presentation(op,packet) reconciles a previous/inline saved
answer; acceptance alone must never satisfy it.

This module is not activated by constructing an inbox. Both host adapters and
explicit runtime wiring are required. Reviewed output survives effect failures;
an inference interrupted or unconfirmed is never automatically repeated.
"""
import uuid
from core.lead_wake.inbox import StaleWake
from services.helper_seats.spend import CapReached


class Runner:
    def __init__(self, inbox, host):
        self.inbox, self.host = inbox, host

    def pass_once(self):
        report=[]
        for operation_id, packet in self.inbox.pending():
            try:
                with self.host.lane(operation_id,packet) as lane:
                    if lane is None:
                        report.append((operation_id,'busy'))
                        continue
                    # An actual previously saved answer needs no second review.
                    receipt=lane.presentation(operation_id,packet)
                    if receipt:
                        self.inbox.mark_presented(operation_id,receipt)
                        report.append((operation_id,'presented'))
                        continue
                    self._current(operation_id,packet)
                    result=self.inbox.review(operation_id)
                    if result is None:
                        context=lane.context(packet)
                        claim=uuid.uuid4().hex
                        if not self.inbox.claim(operation_id,claim):
                            continue
                        try:
                            try:
                                lane.reserve(operation_id,packet)
                            except CapReached:
                                self.inbox.budget_blocked(operation_id,claim)
                                report.append((operation_id,"budget_blocked"))
                                continue
                            self._current(operation_id,packet)
                            result=lane.review(packet,context)
                            self.inbox.save_review(operation_id,claim,result)
                        except StaleWake:
                            raise
                        except Exception:
                            self.inbox.uncertain(operation_id,claim)
                            report.append((operation_id,'uncertain'))
                            continue
                    # Reconcile effects from kept model output after a process restart.
                    self._current(operation_id,packet)
                    if not lane.accept(operation_id,packet,result):
                        report.append((operation_id,'review_effect_unconfirmed'))
                        continue
                    self._current(operation_id,packet)
                    receipt=lane.publish(operation_id,packet,result)
                    if receipt is None:
                        report.append((operation_id,'presentation_unconfirmed'))
                        continue
                    self.inbox.mark_presented(operation_id,receipt)
                    report.append((operation_id,'presented'))
            except StaleWake:
                self.inbox.supersede(operation_id)
                report.append((operation_id,'superseded'))
            except Exception:
                # No text/provider errors in logs. A durable review can reconcile
                # effects; an interrupted model claim remains non-replayable.
                report.append((operation_id,'unavailable'))
        return report

    def _current(self, operation_id,packet):
        if self.inbox.resolve(operation_id)!=packet:
            raise StaleWake('wake changed')
