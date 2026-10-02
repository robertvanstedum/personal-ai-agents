"""Synthetic tool-input detector only. No session readers or schedules."""
from .contracts import ContractError


def inspect_fixture(calls, path_classes, *, synthetic=False, trusted_adapter_receipts=()):
    if synthetic is not True:
        raise ContractError('Real-session detector policy not adopted')
    observations=[]
    for index,call in enumerate(calls):
        # Receipts are explicit trusted test inputs, never command-name matching.
        if call.get('trusted_receipt_id') in trusted_adapter_receipts:
            continue
        content=call.get('tool_input','')
        hits=sorted({kind for path,kind in path_classes.items() if path in content})
        if hits:
            observations.append(dict(provider='synthetic',session_id='fixture',message_index=index,
                                     tool_name=call['tool_name'],matched_path_classes=hits))
    return observations
