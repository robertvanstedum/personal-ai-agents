"""Focused resolver bounds; fixtures contain synthetic text only."""
import tempfile,unittest
from pathlib import Path
from minimoi_portal.guild_ui import references

class ReferenceAliases(unittest.TestCase):
 def setUp(self):
  self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup);self.root=Path(self.folder.name).resolve()
 def write(self,name,text='synthetic'):
  path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);return path
 def test_all_exact_aliases_use_canonical_instead_of_private_copy(self):
  for legacy,target in references.LEGACY_ALIASES.items():
   canonical=self.write(target);self.write('_working/'+legacy,'different private fixture')
   self.assertEqual(references.resolve(legacy,self.root),canonical)
 def test_missing_alias_target_never_private_fallback(self):
  for legacy in references.LEGACY_ALIASES:
   self.write('_working/'+legacy)
   self.assertIsNone(references.resolve(legacy,self.root))
 def test_alias_symlink_to_private_source_refused(self):
  legacy,target=next(iter(references.LEGACY_ALIASES.items()));private=self.write('_working/'+legacy)
  canonical=self.root/target;canonical.parent.mkdir(parents=True);canonical.symlink_to(private)
  self.assertIsNone(references.resolve(legacy,self.root))
 def test_existing_canonical_root_and_other_flat_private_behavior(self):
  for name in ('docs/specs/exact.md','planning-studio/initiatives/one/documents/spec.md','prototype-lab/projects/one/spec.md','ARCHITECTURE.md'):
   path=self.write(name);self.assertEqual(references.resolve(name,self.root),path)
  fallback=self.write('_working/other_legacy.md');self.assertEqual(references.resolve('other_legacy.md',self.root),fallback)
  self.assertIsNone(references.resolve('_working/other_legacy.md',self.root));self.assertIsNone(references.resolve('private_nested.md',self.root))
  self.write('_working/nested/private_nested.md');self.assertIsNone(references.resolve('private_nested.md',self.root))
 def test_traversal_absolute_nonmarkdown_and_external_symlink_refused(self):
  for value in ('docs/../secret.md','../secret.md','/tmp/secret.md','docs\\secret.md','docs//secret.md','README.md','docs/a.txt',None):self.assertIsNone(references.resolve(value,self.root))
  outside=self.write('outside.md');self.write('docs/place.md');(self.root/'docs/escape.md').symlink_to(outside);self.assertIsNone(references.resolve('docs/escape.md',self.root))
 def test_alias_target_size_bound_unchanged(self):
  legacy,target=next(iter(references.LEGACY_ALIASES.items()));self.write(target,'x'*1_000_001)
  self.assertIsNone(references.resolve(legacy,self.root))
