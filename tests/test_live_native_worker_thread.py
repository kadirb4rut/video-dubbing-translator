"""Regress the real CUDA-Graph TLS failure without initializing GPU models."""
import threading
import types
import unittest
from unittest.mock import patch


class NativeOwnerThreadTests(unittest.TestCase):
    def test_load_warm_render_and_close_keep_one_owner_while_source_can_run(self):
        from live_dubbing.worker import _model_main
        threads = []

        class Models:
            load_seconds = 0.
            contextual_chunking = False
            def __init__(self, *args, **kwargs):
                threads.append(('load', threading.get_ident()))
            def render(self, **kwargs):
                threads.append(('render', threading.get_ident()))
                return kwargs
            def recognize(self, *args):
                threads.append(('source', threading.get_ident()))
                return args
            def close(self):
                threads.append(('close', threading.get_ident()))

        class Connection:
            def __init__(self):
                self.messages = []
                self.condition = threading.Condition()
                self.requests = iter([
                    {'id':'a', 'operation':'render', 'kwargs':{'text':'one'}},
                    {'id':'b', 'operation':'recognize', 'args':()},
                    {'id':'c', 'operation':'render', 'kwargs':{'text':'two'}},
                ])
                self.closed = False
            def poll(self, timeout): return True
            def send(self, value):
                with self.condition:
                    self.messages.append(value)
                    self.condition.notify_all()
            def recv(self):
                try: return next(self.requests)
                except StopIteration:
                    with self.condition:
                        if not self.condition.wait_for(
                                lambda:sum('id' in x for x in self.messages)==3, timeout=3):
                            raise TimeoutError('Worker operations did not finish')
                    return {'operation':'stop'}
            def close(self): self.closed = True

        connection = Connection()
        with patch('live_dubbing.worker.os.setsid'), \
                patch('live_dubbing.models.LocalModels', Models), \
                patch.dict('sys.modules', {'torch':types.SimpleNamespace()}):
            _model_main(connection, threading.Event(), '.', {'device':'cpu'})
        owner = next(value for operation,value in threads if operation=='load')
        self.assertEqual(sum(operation=='render' for operation,_ in threads), 2)
        self.assertTrue(all(value==owner for operation,value in threads if operation!='source'))
        self.assertNotEqual(next(value for operation,value in threads if operation=='source'), owner)
        self.assertTrue(connection.closed)
        self.assertTrue(all(x.get('ok', True) for x in connection.messages))
        timed = [x['worker_timing'] for x in connection.messages if x.get('id')]
        self.assertEqual(len(timed), 3)
        self.assertTrue(all(x['pool_wait_seconds'] >= 0 and x['execution_seconds'] >= 0 for x in timed))
