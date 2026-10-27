import types,unittest
from unittest.mock import Mock
from minimoi_portal.guild_ui import agent_ask as ask, jobs_api as jobs

class OwnerGuardTests(unittest.TestCase):
 def guard(self,text,to,files=None,ids=()):return ask.owner_ask_proposal(text,{'to':to,'title':'Model title is not authority','files':files},ids)
 def test_explicit_claude_excludes_model_codex_and_both(self):
  for to in(['codex'],['claude-code','codex']):self.assertIsNotNone(self.guard('Ask Claude Code, not Codex.',to)[1])
 def test_codex_title_is_not_a_recipient_directive(self):
  text='Codex builder verification. Hand the attached code-only diff to Claude Code only. No other agents.'
  got,error=self.guard(text,['claude-code'],ids=['d-synthetic']);self.assertIsNone(error);self.assertEqual(got['files'],'turn')
  self.assertIsNotNone(self.guard(text,['codex'],ids=['d-synthetic'])[1])
 def test_review_of_another_agents_work_is_not_dispatch_to_it(self):
  got,error=self.guard("Ask Claude Code to review Codex's work.",['claude-code']);self.assertIsNone(error)
 def test_explicit_both_or_named_and_allowed_but_or_ambiguous(self):
  for text in('Ask both to review.','Ask Claude Code and Codex to review.'):
   self.assertIsNone(self.guard(text,['claude-code','codex'])[1])
  self.assertIsNotNone(self.guard('Ask Claude Code or Codex to review.',['claude-code'])[1])
 def test_no_history_recipient_fallback(self):self.assertIsNotNone(self.guard('And the second one as well.',['codex'])[1])
 def test_unknown_recipient_refused(self):self.assertIsNotNone(self.guard('Ask Grok to review.',['codex'])[1])
 def test_quoted_fenced_blockquoted_inline_examples_do_not_authorize(self):
  for text in('"Ask Codex to review"',"'Ask Codex to review'",'```\nAsk Codex to review\n```','~~~\nAsk Codex to review\n~~~','> Ask Codex to review','The example is `Ask Codex to review`'):
   with self.subTest(text=text):self.assertIsNotNone(self.guard(text,['codex'])[1])
 def test_negated_directives_do_not_authorize(self):
  for text in("Don't ask Codex to review.",'Do not send this to Codex.','Never ask Claude Code.'):
   self.assertIsNotNone(self.guard(text,['codex']if'Codex'in text else['claude-code'])[1])
 def test_conflicting_multiple_directives_refused(self):self.assertIsNotNone(self.guard('Ask Claude Code. Ask Codex.',['claude-code','codex'])[1])
 def test_current_attachment_forced_or_refused_missing_ids_and_latest(self):
  text='Hand the attached diff to Claude Code only.'
  self.assertEqual(self.guard(text,['claude-code'],ids=['d-current'])[0]['files'],'turn')
  self.assertIsNotNone(self.guard(text,['claude-code'])[1]);self.assertIsNotNone(self.guard(text,['claude-code'],'latest',['d-current'])[1])
 def test_model_cannot_add_file_sharing_without_owner_instruction(self):
  for mode in('turn','latest'):self.assertIsNotNone(self.guard('Ask Claude Code to review.', ['claude-code'],mode,['d-current'])[1])
 def test_latest_only_when_explicit_and_never_entire_conversation(self):
  got,error=self.guard('Send the latest file to Claude Code.',['claude-code']);self.assertIsNone(error);self.assertEqual(got['files'],'latest')
 def test_no_files_override(self):self.assertIsNotNone(self.guard('Ask Claude Code to review without attachments.',['claude-code'],'turn',['d-current'])[1])
 def hook(self,text,doc_ids=()):
  service=types.SimpleNamespace(ask=Mock(return_value=[{'ok':True}]),words=Mock(return_value='Recorded, not delivered'))
  hook=jobs.JobHook(None,{'id':'c-12345678'},'owner',{'text':text},'owner-note',doc_ids,service);hook._evidence=Mock();return hook,service
 def test_actual_jobhook_wrong_recipient_has_zero_ledger_or_evidence_calls(self):
  hook,service=self.hook('Codex builder verification. Hand the attached diff to Claude Code only.',['d-current']);decision=hook.decide('ASK codex: Model title',clean=True)
  self.assertTrue(decision.text.startswith('**Not sent.**'));service.ask.assert_not_called();hook._evidence.assert_not_called()
 def test_actual_jobhook_current_files_forwarded_even_without_model_modifier(self):
  text='Hand the attached diff to Claude Code only.';hook,service=self.hook(text,['d-current']);hook.decide('ASK claude-code: Review',clean=True);args,kw=service.ask.call_args;self.assertEqual(args[1]['text'],text);self.assertEqual(args[3],['claude-code']);self.assertEqual(kw['files'],'turn');self.assertIs(kw['evidence'],hook._evidence)
 def test_actual_jobhook_latest_mismatch_or_missing_current_never_dispatches(self):
  for ids,reply in([[],'ASK claude-code: Review'],[['d-current'],'ASK claude-code +files:latest: Review']):
   hook,service=self.hook('Hand the attached diff to Claude Code only.',ids);decision=hook.decide(reply,clean=True);self.assertTrue(decision.text.startswith('**Not sent.**'));service.ask.assert_not_called();hook._evidence.assert_not_called()
 def test_reviewed_ambiguity_and_deny_cases_never_dispatch(self):
  cases=[('Ask Claude Code to review the latest file. Do not send the file.','ASK claude-code: Review'),('Ask Codex to review. Do not ask Codex.','ASK codex: Review'),('No need to ask Codex to review.','ASK codex: Review'),('Yesterday I decided to ask Codex to review.','ASK codex: Review'),('"Example:\nAsk Codex to review.\n"','ASK codex: Review'),('The latest file is private. Ask Claude Code to review only the summary.','ASK claude-code +files:latest: Review')]
  for text,reply in cases:
   with self.subTest(text=text):
    hook,service=self.hook(text,['d-current']);decision=hook.decide(reply,clean=True);self.assertTrue(decision.text.startswith('**Not sent.**'));service.ask.assert_not_called();hook._evidence.assert_not_called()
 def test_file_history_mention_does_not_autoforward(self):
  proposal,error=self.guard('The latest file is private. Ask Claude Code to review only the summary.',['claude-code'])
  self.assertIsNone(error);self.assertIsNone(proposal['files'])

 def test_singular_attachment_never_expands_to_multiple_current_files(self):
  hook,service=self.hook('Hand the attached diff to Claude Code only.',['d-one','d-two']);decision=hook.decide('ASK claude-code: Review',clean=True)
  self.assertTrue(decision.text.startswith('**Not sent.**'));service.ask.assert_not_called();hook._evidence.assert_not_called()
 def test_explicit_plural_current_attachments_can_forward_all_current_ids(self):
  got,error=self.guard('Hand all attached files to Claude Code only.',['claude-code'],ids=['d-one','d-two']);self.assertIsNone(error);self.assertEqual(got['files'],'turn')

 def test_exact_observed_owner_request_is_conservatively_refused_without_side_effects(self):
  text = "Codex builder verification under Robert's approved dev build/review plan, not owner acceptance: Please hand the attached code-only Admin CSS diff to Claude Code for an actual-diff, read-only review. Ask for a concise verdict and any concrete defects. Do not apply changes, build, deploy, change the Board, or contact any other agent. Use the permanent MC handoff home and report whether the handoff is staged, acknowledged, or completed; do not claim review completion until Claude's result arrives."
  for reply in ('ASK codex: Review', 'ASK claude-code: Review'):
   hook,service=self.hook(text,['d-observed-current']);decision=hook.decide(reply,clean=True);self.assertTrue(decision.text.startswith('**Not sent.**'));service.ask.assert_not_called();hook._evidence.assert_not_called()
 def test_clear_natural_positive_retry_keeps_owner_text_and_current_file(self):
  text='Please ask Claude Code to review the attached code-only Admin CSS diff. No other agents.'
  hook,service=self.hook(text,['d-current']);hook.decide('ASK claude-code: Review',clean=True);args,kw=service.ask.call_args;self.assertEqual(args[1]['text'],text);self.assertEqual(args[3],['claude-code']);self.assertEqual(kw['files'],'turn')

 def test_multiline_or_unclosed_inline_data_never_authorizes_dispatch(self):
  for text in ('Example: `\nAsk Codex to review.\n`','Example: `\nAsk Codex to review.','Example: ``\nAsk Codex to review.\n``','```\n"\nAsk Codex to review.\n```\n"'):
   with self.subTest(text=text):
    hook,service=self.hook(text,['d-current']);decision=hook.decide('ASK codex: Review',clean=True);self.assertTrue(decision.text.startswith('**Not sent.**'));service.ask.assert_not_called();hook._evidence.assert_not_called()

 def test_typed_plural_or_subset_never_expands_to_all_mixed_ids(self):
  for text in ('Ask Claude Code to review attached files.','Ask Claude Code to review attached screenshots.','Ask Claude Code to review all attached files except the private screenshot.','Ask Claude Code to review all attached attachments excluding the code file.'):
   with self.subTest(text=text):
    hook,service=self.hook(text,['d-code','i-picture']);decision=hook.decide('ASK claude-code: Review',clean=True);self.assertTrue(decision.text.startswith('**Not sent.**'));service.ask.assert_not_called();hook._evidence.assert_not_called()

 def test_question_about_file_sharing_is_not_file_authority(self):
  for text in ('Ask Claude Code whether I should share the latest file.','Ask Claude Code whether I should send the attached screenshot.','Ask Claude Code to review whether I should send the latest file.'):
   with self.subTest(text=text):
    hook,service=self.hook(text,['d-current']);decision=hook.decide('ASK claude-code: Review',clean=True);self.assertTrue(decision.text.startswith('**Not sent.**'));service.ask.assert_not_called();hook._evidence.assert_not_called()

 def test_unsupported_or_extended_recipient_lists_never_take_a_subset(self):
  cases=[('Ask Claude Code, or Codex, to review.','ASK claude-code: Review'),('Ask Claude Code & Codex to review.','ASK claude-code: Review'),('Ask Claude Code / Codex to review.','ASK claude-code: Review'),('Ask Claude Code and Codex or both to review.','ASK both: Review')]
  for text,reply in cases:
   with self.subTest(text=text):
    hook,service=self.hook(text,['d-current']);decision=hook.decide(reply,clean=True);self.assertTrue(decision.text.startswith('**Not sent.**'));service.ask.assert_not_called();hook._evidence.assert_not_called()

 def test_unknown_or_qualified_list_tail_never_selects_known_subset(self):
  for text in ('Ask Claude Code or Grok to review.','Ask Claude Code or maybe Codex to review.','Ask Claude Code and Grok to review.'):
   hook,service=self.hook(text);decision=hook.decide('ASK claude-code: Review',clean=True);self.assertTrue(decision.text.startswith('**Not sent.**'));service.ask.assert_not_called();hook._evidence.assert_not_called()
 def test_lazy_blockquote_and_indented_code_never_authorize(self):
  for text in ('> Example for later:\nAsk Codex to review.','    Ask Codex to review.','\tAsk Codex to review.'):
   hook,service=self.hook(text);decision=hook.decide('ASK codex: Review',clean=True);self.assertTrue(decision.text.startswith('**Not sent.**'));service.ask.assert_not_called();hook._evidence.assert_not_called()
 def test_fresh_directive_after_blank_line_ends_quoted_data(self):
  got,error=self.guard('> Example for later:\nAsk Codex to review.\n\nAsk Claude Code to review.', ['claude-code']);self.assertIsNone(error);self.assertEqual(got['to'],['claude-code'])

 def test_grok_and_all_are_recipients_too(self):
  self.assertIsNone(self.guard('Ask Grok to review.',['grok'])[1])
  self.assertIsNone(self.guard('Ask all to review the plan.',['claude-code','codex','grok'])[1])
  self.assertIsNone(self.guard('Ask Codex and Grok to review.',['codex','grok'])[1])
  self.assertIsNotNone(self.guard('Ask all to review the plan.',['claude-code','codex'])[1])         # all is three, never two
  self.assertIsNotNone(self.guard('Ask Claude Code, not Grok.',['grok'])[1])
 def test_actual_jobhook_ask_grok_line_dispatches_to_grok_only(self):
  hook,service=self.hook('Please ask Grok to check the phone path.');hook.decide('ASK grok: Phone path',clean=True)
  args,kw=service.ask.call_args;self.assertEqual(args[3],['grok'])

if __name__=='__main__':unittest.main()
