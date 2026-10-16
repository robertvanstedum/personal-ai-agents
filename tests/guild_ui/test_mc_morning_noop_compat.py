"""Parser compatibility only; no native call, DB or actual Board proof."""
import pytest
from minimoi_portal.guild_ui.mc import morning as m

ACTUAL_SHAPE='```json\n{"action":"noop"}\n```\n\nThe synthetic Board is test material, not real work requiring a reminder.'

def test_leading_exact_noop_fence_with_rationale_is_harmless_noop():
 assert m._proposal(ACTUAL_SHAPE,{12,13})=={'action':'noop'}

@pytest.mark.parametrize('text',[
 'Prose before\n'+ACTUAL_SHAPE,
 ACTUAL_SHAPE+'\n```json\n{"action":"remind","text":"Synthetic","source_ids":[12]}\n```',
 '```json\n{"action":"noop","extra":true}\n```\nRationale.',
 '```json\n{"action":"remind","text":"Synthetic","source_ids":[12]}\n```\nRationale.',
 '```json\n{"action":"noop"}\n```\nAlternative {"action":"remind"}',
 '```json\n{"action":"noop","action":"noop"}\n```\nRationale.',
 '{"action":"noop"}\nRationale.',
 '```python\n{"action":"noop"}\n```\nRationale.',
 '```json\n[]\n```\nRationale.',
 '```json\n{"action":"noop"}\n```\n'+('x'*4096),
])
def test_rationale_path_never_relaxes_mutation_or_ambiguity(text):
 with pytest.raises(m.MorningError,match='reply_invalid'):m._proposal(text,{12,13})

def test_existing_whole_reminder_contract_preserved():
 assert m._proposal('```json\n{"action":"remind","text":"Synthetic","source_ids":[12]}\n```',{12,13})['action']=='remind'
