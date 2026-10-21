"""Crash/concurrency regression for the shared single-link publication seam."""
import errno
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.workshop_journal import fsutil as fs
from core.workshop_journal.errors import IdConflict, UnsafeRoot


def crashing_publish(root, after):
    folder = fs.open_root(root)
    rename = fs._rename_exclusive
    def interrupted(*args):
        if after:
            rename(*args)
        os._exit(17)
    with patch.object(fs, '_rename_exclusive', side_effect=interrupted):
        fs.publish_new(folder, 'record', b'exact original')


def competing_publish(root, value, result):
    folder = fs.open_root(root)
    try:
        result.put(('ok',fs.publish_new(folder,'record',value)))
    except IdConflict:
        result.put(('conflict',))
    finally:
        os.close(folder)


class AtomicPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = fs.open_root(str(self.root))
        self.addCleanup(os.close,self.folder)

    def test_process_death_on_either_side_of_rename_is_retryable(self):
        for after in (False,True):
            with self.subTest(after=after):
                child = multiprocessing.get_context('fork').Process(
                    target=crashing_publish,args=(str(self.root),after))
                child.start(); child.join(5)
                if child.is_alive():
                    child.kill(); child.join(); self.fail('owned fixture timed out')
                self.assertEqual(child.exitcode,17)
                path = self.root/'record'
                self.assertEqual(path.exists(),after)
                if after:
                    self.assertEqual(path.stat().st_nlink,1)
                self.assertEqual(fs.publish_new(self.folder,'record',b'exact original'),not after)
                fd = fs.open_file(self.folder,'record',os.O_RDONLY)
                try:
                    self.assertEqual(fs.read_all(fd),b'exact original')
                finally:
                    os.close(fd)
                path.unlink()

    def test_competing_publishers_never_replace_a_winner(self):
        ctx = multiprocessing.get_context('fork'); result = ctx.Queue()
        children = [ctx.Process(target=competing_publish,args=(str(self.root),bytes([i]),result)) for i in range(4)]
        for child in children: child.start()
        for child in children:
            child.join(5)
            if child.is_alive():
                child.kill(); child.join(); self.fail('owned fixture timed out')
            self.assertEqual(child.exitcode,0)
        outcomes = [result.get(timeout=1) for _ in children]; result.close()
        self.assertEqual(outcomes.count(('ok',True)),1)
        self.assertEqual(outcomes.count(('conflict',)),3)
        self.assertEqual((self.root/'record').stat().st_nlink,1)

    def test_unsupported_exclusive_rename_fails_without_link_fallback(self):
        with patch.object(fs,'_rename_exclusive',side_effect=OSError(errno.ENOTSUP,'unsupported')):
            with self.assertRaises(OSError):
                fs.publish_new(self.folder,'record',b'content')
        self.assertEqual(list(self.root.iterdir()),[])

    def test_existing_symlink_or_external_hardlink_is_still_refused(self):
        fs.publish_new(self.folder,'original',b'content')
        (self.root/'record').symlink_to('original')
        with self.assertRaises(UnsafeRoot):
            fs.publish_new(self.folder,'record',b'content')
        (self.root/'record').unlink()
        os.link(self.root/'original',self.root/'record')
        with self.assertRaises(UnsafeRoot):
            fs.publish_new(self.folder,'record',b'content')

    def test_error_after_rename_cannot_leave_two_link_published_file(self):
        original = fs._rename_exclusive
        def rename_then_error(*args):
            original(*args)
            raise OSError(errno.EIO,'injected after rename')
        with patch.object(fs,'_rename_exclusive',side_effect=rename_then_error),self.assertRaises(OSError):
            fs.publish_new(self.folder,'record',b'content')
        self.assertEqual((self.root/'record').stat().st_nlink,1)
        self.assertFalse(fs.publish_new(self.folder,'record',b'content'))
