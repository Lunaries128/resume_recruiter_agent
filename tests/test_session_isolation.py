"""离线测试，不读取真实数据库、不调用百炼、不需要上传真实简历。"""
import ast
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
TEMP = tempfile.TemporaryDirectory()
old_root = os.environ.get('RECRUITMENT_HISTORY_DIR')
os.environ['RECRUITMENT_HISTORY_DIR'] = TEMP.name
spec = importlib.util.spec_from_file_location('session_database_under_test', ROOT/'database.py')
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)
if old_root is None:
    os.environ.pop('RECRUITMENT_HISTORY_DIR', None)
else:
    os.environ['RECRUITMENT_HISTORY_DIR'] = old_root


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.a = db.create_session('A')['id']
        self.b = db.create_session('B')['id']

    def upload(self, sid, successful=True):
        uid = uuid.uuid4().hex
        path = db.HISTORY_DIR/sid/'uploads'/(uid+'.txt')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('测试原文', encoding='utf-8')
        db.add_upload(sid, '简历.txt', path, uid)
        db.claim_upload(sid, uid)
        if successful:
            db.finish_upload(sid, uid, profile={'skills':['Python'], 'masked_name':'张*'}, confidence=80)
        else:
            db.finish_upload(sid, uid, status='解析失败', error='测试错误')
        return uid, path

    def test_new_session_is_empty(self):
        self.upload(self.a)
        self.assertEqual(db.list_uploads(self.b), [])
        with db.session_scope(self.b):
            self.assertEqual(db.list_candidates(), [])

    def test_same_filename_is_isolated(self):
        first, _ = self.upload(self.a)
        second, _ = self.upload(self.b)
        with db.session_scope(self.a):
            self.assertIsNotNone(db.get_candidate('CAND-'+first))
            self.assertIsNone(db.get_candidate('CAND-'+second))
        with self.assertRaises(ValueError):
            db.get_upload(self.b, first)

    def test_scope_is_required(self):
        with self.assertRaises(ValueError):
            db.list_candidates()

    def test_score_is_bound_to_jd_and_session(self):
        uid, _ = self.upload(self.a)
        db.update_session(self.a, jd='Python')
        with db.session_scope(self.a):
            db.save_score('CAND-'+uid, 'hash', {'total_score': 85})
            self.assertEqual(db.list_candidates()[0]['match_score'], 85)
        with db.session_scope(self.b):
            with self.assertRaises(ValueError):
                db.save_score('CAND-'+uid, 'hash', {'total_score': 0})
        db.update_session(self.a, jd='Java')
        with db.session_scope(self.a):
            self.assertIsNone(db.list_candidates()[0]['match_score'])

    def test_messages_jd_and_metadata_persist(self):
        db.update_session(self.a, title='已改名', pinned=True, jd='Python')
        db.add_message(self.a, 'user', '仅A可见')
        self.assertEqual(db.list_messages(self.b), [])
        with sqlite3.connect(db.DATABASE_PATH) as conn:
            row = conn.execute('SELECT title,pinned,jd FROM sessions WHERE id=?', (self.a,)).fetchone()
        self.assertEqual(row, ('已改名', 1, 'Python'))

    def test_failed_upload_preserves_original(self):
        uid, path = self.upload(self.a, successful=False)
        record = db.get_upload(self.a, uid)
        self.assertEqual(record['confidence'], 0)
        self.assertTrue(path.exists())
        self.assertEqual(record['error'], '测试错误')

    def test_confirmation_is_scoped_and_one_use(self):
        uid, path = self.upload(self.a)
        token = db.create_delete_request(self.a, 'uploads', [uid])['token']
        with self.assertRaises(ValueError):
            db.confirm_delete(self.b, token)
        self.assertTrue(path.exists())
        db.confirm_delete(self.a, token)
        self.assertFalse(path.exists())
        with self.assertRaises(ValueError):
            db.confirm_delete(self.a, token)

    def test_delete_session_cascades_without_harming_other(self):
        uid, path = self.upload(self.a)
        other, other_path = self.upload(self.b)
        db.add_message(self.a, 'user', 'hello')
        with db.session_scope(self.a):
            db.save_score('CAND-'+uid, 'hash', {'total_score':70})
        token = db.create_delete_request(self.a, 'session', [])['token']
        db.confirm_delete(self.a, token)
        self.assertFalse(path.exists())
        self.assertTrue(other_path.exists())
        with db.get_connection() as conn:
            for table in ('uploads','candidates','scores','messages'):
                count = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE session_id=?', (self.a,)).fetchone()[0]
                self.assertEqual(count, 0)

    def test_deleted_session_cannot_receive_late_result(self):
        uid, path = self.upload(self.a, successful=False)
        token = db.create_delete_request(self.a, 'session', [])['token']
        db.confirm_delete(self.a, token)
        with self.assertRaises(ValueError):
            db.finish_upload(self.a, uid, profile={'skills':[]})

    def test_concurrent_scopes(self):
        self.upload(self.a)
        barrier = threading.Barrier(2)
        counts = {}
        def read(sid):
            with db.session_scope(sid):
                barrier.wait()
                counts[sid] = len(db.list_candidates())
        threads = [threading.Thread(target=read, args=(sid,)) for sid in (self.a,self.b)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(counts, {self.a:1,self.b:0})

    def test_recover_interrupted_parse(self):
        uid, _ = self.upload(self.a, successful=False)
        db.claim_upload(self.a, uid)
        db.recover_interrupted_uploads()
        self.assertEqual(db.get_upload(self.a, uid)['status'], '解析中断')
        db.claim_upload(self.a, uid)

    def test_timeout_kills_real_process_and_keeps_original(self):
        uid, path = self.upload(self.a, successful=False)
        source = ast.parse((ROOT/'api.py').read_text(encoding='utf-8'))
        node = next(x for x in source.body if isinstance(x, ast.FunctionDef) and x.name=='process_upload')
        processes = []
        def spawn(*args, **kwargs):
            process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            processes.append(process)
            return process
        fake_subprocess = SimpleNamespace(Popen=spawn, PIPE=subprocess.PIPE,
            DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired)
        namespace = {'db':db, 'subprocess':fake_subprocess, 'sys':sys, 'Path':Path,
                     '__file__':str(ROOT/'api.py'), 'json':json, 'PARSE_TIMEOUT_SECONDS':0.1}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<process_upload>', 'exec'), namespace)
        result = namespace['process_upload'](self.a, uid)
        self.assertEqual(result['status'], '解析超时')
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(path.exists())
        with db.session_scope(self.a):
            self.assertEqual(db.list_candidates(), [])


if __name__ == '__main__':
    unittest.main()
