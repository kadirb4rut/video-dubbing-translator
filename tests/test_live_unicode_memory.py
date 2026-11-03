from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from live_dubbing.memory import LiveContextMemory


class LiveUnicodeMemoryTests(unittest.TestCase):
    def test_distant_names_numbers_and_units_survive_unrelated_turns(self):
        for term in ('東京大学730kg','جامعةالقاهرة730kg','มหาวิทยาลัย730kg','Élodie730kg'):
            with self.subTest(term=term),TemporaryDirectory() as folder:
                memory=LiveContextMemory(Path(folder)/'memory.sqlite3')
                memory.remember(kind='live_committed',source=term,speaker_id='A')
                for i in range(120):
                    memory.remember(kind='live_committed',source=f'and the repeated background conversation {i}',speaker_id='A')
                found=memory.relevant(term,speaker_id='A',limit=2)
                self.assertEqual(found[0].source,term)
                self.assertLessEqual(len(found),2)
                self.assertEqual(memory.relevant(term,speaker_id='B'),[])
                memory.close()

    def test_reopen_preserves_index_and_unicode_normalization(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'context.sqlite3';memory=LiveContextMemory(path)
            memory.remember(source='Élodie works at 東京大学',kind='live_committed')
            memory.close();memory=LiveContextMemory(path)
            self.assertEqual(len(memory.relevant('E\u0301lodie 東京大学')),1)
            memory.close()

    def test_empty_unrelated_and_syntax_like_queries_do_not_inject_context(self):
        with TemporaryDirectory() as folder:
            memory=LiveContextMemory(Path(folder)/'context.sqlite3')
            memory.remember(source='東京大学730kg')
            self.assertEqual(memory.relevant(''),[])
            self.assertEqual(memory.relevant('" OR DROP TABLE xyz --'),[])
            self.assertEqual(memory.relevant('unrelated weather forecast'),[])
            self.assertEqual(len(memory.relevant('東京大学')),1)
            memory.close()
