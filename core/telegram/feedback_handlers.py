"""Transport adapters for validated feedback; caller performs owner admission."""
from core.telegram.feedback_contract import parse_callback, field


def process(query, tracker, writer):
    request = parse_callback(query)
    answer = tracker.execute(request.key, lambda: writer(request.action, request.rank, request.expected_url))
    return request, answer


def confirmation(text, action):
    return text.split('\n✅')[0] + f"\n\n✅ {action.capitalize()}d!"


async def polling_feedback(query, tracker, writer):
    try:
        # Validate before any acknowledgment or writer call.
        parse_callback(query)
    except ValueError as exc:
        if query is not None:
            await query.answer(str(exc), show_alert=True)
        return
    try:
        await query.answer("Recording feedback...")
    except Exception:
        pass  # Failure to acknowledge does not establish a writer outcome.
    request, answer = process(query, tracker, writer)
    if answer["success"]:
        try:
            await query.edit_message_text(
                text=confirmation(field(query.message, "text", ""), request.action),
                reply_markup=field(query.message, "reply_markup"),
                entities=field(query.message, "entities", ()),
            )
        except Exception:
            pass  # Durable success remains deduplicated even if editing fails.
    else:
        try:
            await query.answer(answer["message"], show_alert=True)
        except Exception:
            pass
