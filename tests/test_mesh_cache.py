from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from server.mesh_service import Jobs, input_key


class MeshCacheTests(unittest.TestCase):
    def test_identical_pending_video_joins_one_job_without_creating_a_directory(self):
        with TemporaryDirectory() as directory:
            jobs = Jobs(Path(directory), None, None)
            one, hit = jobs.accept('input', 'a' * 40, 'fast')
            two, hit2 = jobs.accept('input', 'b' * 40, 'fast')
            self.assertFalse(hit)
            self.assertTrue(hit2)
            self.assertEqual(one['id'], two['id'])
            self.assertFalse((Path(directory) / one['id']).exists())
            self.assertEqual(jobs.accept('other', 'c' * 40, 'fast'), (None, False))
            jobs.active.release()

    def test_ready_mesh_survives_restart_and_missing_binary_is_rebuilt(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = Jobs(root, None, None)
            token = 'd' * 40
            jobs.accept('same-file-and-pipeline', token, 'fast')
            (root / token).mkdir()
            (root / token / 'mesh.bin').write_bytes(b'\0' * (3 * 15 + 12))
            jobs.set(token, 'ready', vertices=3, triangles=1, indexBytes=4)
            jobs.active.release()
            restarted = Jobs(root, None, None)
            hit, cached = restarted.accept('same-file-and-pipeline', 'e' * 40, 'fast')
            self.assertTrue(cached)
            self.assertEqual(hit['id'], token)
            self.assertNotIn('_inputKey', hit)
            (root / token / 'mesh.bin').write_bytes(b'corrupt')
            self.assertIsNone(restarted.get(token))
            new, cached = restarted.accept('same-file-and-pipeline', 'e' * 40, 'fast')
            self.assertFalse(cached)
            self.assertEqual(new['id'], 'e' * 40)
            restarted.active.release()

    def test_profile_and_video_content_have_distinct_keys(self):
        self.assertNotEqual(input_key('a' * 64, 'fast'), input_key('a' * 64, 'detailed'))
        self.assertNotEqual(input_key('a' * 64, 'fast'), input_key('b' * 64, 'fast'))


if __name__ == '__main__':
    unittest.main()
