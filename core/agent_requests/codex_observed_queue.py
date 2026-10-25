"""Reviewed queue plus pre-dispatch persisted native baseline. No auto-enrollment."""
from .codex_desktop_queue import CodexDesktopQueue, admission, message_for
from .codex_read_observer import baseline_for, need
from .direct_contact import _hash
from .status_v2 import request_message


class ObservedQueue(CodexDesktopQueue):
    def __init__(self, enrollment, observer, **kwargs):
        super().__init__(enrollment, native_home=observer.enrollment.codex_home, **kwargs)
        need(observer.enrollment.target == enrollment.target and
             observer.enrollment.binary_identity == enrollment.binary_identity,
             'observer_binding_changed')
        self.observer = observer

    def prepare(self, target, request, correlation):
        need(admission(self.enrollment)['ready'], 'direct_contact_not_ready')
        need(target == self.enrollment.target, 'target_binding_changed')
        prepared={'v': 1, 'baseline': baseline_for(self.observer.snapshot()),
                  'message_sha256': _hash(request_message(target, request, correlation))}
        if 'reply_protocol' in request:prepared['reply_protocol']=request['reply_protocol']
        return prepared
