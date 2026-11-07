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
  self.assertIsNone(self.guard('Ask everyone to review the plan.',['claude-code','codex','grok'])[1])
  self.assertIsNotNone(self.guard('Do not ask everyone.',['claude-code','codex','grok'])[1])
  self.assertIsNotNone(self.guard('Ask Claude Code, not Grok.',['grok'])[1])
 def test_actual_jobhook_ask_grok_line_dispatches_to_grok_only(self):
  hook,service=self.hook('Please ask Grok to check the phone path.');hook.decide('ASK grok: Phone path',clean=True)
  args,kw=service.ask.call_args;self.assertEqual(args[3],['grok'])

class NaturalWordingTests(unittest.TestCase):
 """Robert's exact sentences on Dev, 7 Nov, and what must stay refused."""
 def guard(self,text,to):return ask.owner_ask_proposal(text,{'to':to,'files':None},())
 def test_his_exact_sentences_now_pass_for_exactly_the_agent_he_named(self):
  for text,to in (("can you contact Claude and tell it github doesn't work for you",['claude-code']),
                  ('How about Codex.  Try to contact codex and send a message',['codex'])):
   with self.subTest(text=text):
    self.assertIsNone(self.guard(text,to)[1])
    for wrong in ([a for a in ('claude-code','codex','grok') if a not in to][:1],['claude-code','codex','grok']):
     self.assertIsNotNone(self.guard(text,wrong)[1])
 def test_natural_verbs(self):
  for text in ('Contact Codex about the tool.','Message Codex about the tool.','Tell Codex the tool is broken.','Ask Codex about the tool.','Ping Codex.','Reach out to Codex.','Get in touch with Codex.','Send Codex a message.','Please try to message Codex.','Go ahead and tell Codex.'):
   with self.subTest(text=text):self.assertIsNone(self.guard(text,['codex'])[1])
 def test_quoted_negated_reported_and_conditional_stay_refused(self):
  for text in ('"contact Codex"','`message Codex`','> tell Codex','Do not contact Codex.',"Don't try to message Codex.",'Try not to contact Codex.','Never message Codex.','No need to contact Codex.','Yesterday I told Codex about it.','Yesterday I decided to contact Codex.','If it breaks, contact Codex.','Did you contact Codex?','Codex said to contact Codex.','How about Codex.','Claude Code'):
   with self.subTest(text=text):self.assertIsNotNone(self.guard(text,['codex'])[1])
 def test_a_second_verb_never_brings_a_second_agent_or_a_file(self):
  for text in ('Contact Codex and tell Grok.','Contact Codex and ask Claude too.','Contact Codex or send it.','Contact Codex and maybe someone else.'):
   with self.subTest(text=text):self.assertIsNotNone(self.guard(text,['codex'])[1])
  for text in ('Contact Codex and send the attached file.','Contact Codex and send the latest file.','Contact Codex and share this screenshot.'):
   with self.subTest(text=text):self.assertEqual(self.guard(text,['codex'])[1],ask.OWNER_FILES_REFUSED)
 def test_refusal_says_plainly_what_to_say(self):
  self.assertIn('for example: Ask Codex to',ask.OWNER_INTENT_REFUSED)

class BareAnswerTests(unittest.TestCase):
 INSTRUCTION="can you contact Claude and tell it github doesn't work for you"
 QUESTION='Claude meint welchen? Claude Code, Codex oder Grok?'
 def notes(self,owner=None,mc=None,*more):
  owner=self.INSTRUCTION if owner is None else owner
  mc=self.QUESTION if mc is None else mc
  return [{'who':'robert','author_kind':'owner','text':owner},{'who':'master_craftsman','author_kind':'agent','text':mc},*more]
 def test_his_exact_answer_sends_his_earlier_words(self):
  self.assertEqual(ask.answered_ask('Claude Code',self.notes(),['claude-code']),(self.INSTRUCTION,None))
  got,error=ask.answered_ask('Codex.',self.notes('Ask one of the agents to look at this.','Which one?'),['codex']);self.assertIsNone(error);self.assertIn('Ask one of',got)
 def test_only_a_bare_name_answers(self):
  for text in ('Claude Code is broken','Yes','"Claude Code"','Not Claude Code','the second one','Codex or Grok'):
   with self.subTest(text=text):self.assertIsNotNone(ask.answered_ask(text,self.notes(),['claude-code'])[1])
 def test_recipient_is_his_answer_never_broadened(self):
  self.assertIsNotNone(ask.answered_ask('Claude Code',self.notes(),['codex'])[1])
  self.assertIsNotNone(ask.answered_ask('Claude Code',self.notes(),['claude-code','codex'])[1])
  self.assertIsNotNone(ask.answered_ask('Grok',self.notes(),['grok'])[1])                 # he had named Claude, not Grok
 def test_earlier_words_must_be_his_own_clear_instruction(self):
  for owner in ('"contact Codex"','`contact Codex`','> contact Codex','Do not contact Codex.','Codex is quiet today.','Yesterday I told Codex about it.'):
   with self.subTest(owner=owner):self.assertIsNotNone(ask.answered_ask('Codex',self.notes(owner),['codex'])[1])
 def test_agent_authored_text_authorizes_nothing(self):
  rows=[{'who':'master_craftsman','author_kind':'agent','text':'Shall I contact Codex?'},{'who':'master_craftsman','author_kind':'agent','text':'Which one?'}]
  self.assertIsNotNone(ask.answered_ask('Codex',rows,['codex'])[1])
  rows=self.notes(None,None,{'who':'codex','author_kind':'agent','text':'Please ask me, which one?'})
  self.assertIsNotNone(ask.answered_ask('Claude Code',rows,['claude-code'])[1])
  self.assertIsNotNone(ask.answered_ask('Claude Code',self.notes(None,'Done, sent.'),['claude-code'])[1])   # MC did not ask
  self.assertIsNotNone(ask.answered_ask('Claude Code',[],['claude-code'])[1])
 def test_only_the_note_right_before_mcs_question_counts(self):
  rows=[{'who':'robert','author_kind':'owner','text':self.INSTRUCTION},{'who':'master_craftsman','author_kind':'agent','text':'Which?'},
        {'who':'robert','author_kind':'owner','text':'never mind'},{'who':'master_craftsman','author_kind':'agent','text':'Sure?'}]
  self.assertIsNotNone(ask.answered_ask('Claude Code',rows,['claude-code'])[1])
 def test_files_stay_refused(self):
  self.assertEqual(ask.answered_ask('Claude Code',self.notes('Contact Claude and send the attached file.'),['claude-code'])[1],ask.OWNER_FILES_REFUSED)

if __name__=='__main__':unittest.main()
