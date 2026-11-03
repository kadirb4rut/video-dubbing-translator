"""Persistent Unicode n-gram context retrieval without language stopword lists.

FTS5 ranks corpus-derived character n-grams. This is lexical retrieval, not a
semantic quality score. The offline ContextMemory implementation is unchanged.
"""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
import threading
import unicodedata
from engine.context import ContextItem


def grams(text):
    text = ''.join(c for c in unicodedata.normalize('NFC',text).casefold()
                   if unicodedata.category(c)[0] in 'LNM')
    return {text[i:i+n] for n in (2,3) for i in range(len(text)-n+1)}


class LiveContextMemory:
    def __init__(self, path: str | Path):
        self._lock=threading.RLock()
        self._db=sqlite3.connect(str(path),check_same_thread=False)
        self._db.row_factory=sqlite3.Row
        try:
            with self._db:
                self._db.executescript('''
                    CREATE TABLE IF NOT EXISTS live_items(
                        id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
                        source TEXT NOT NULL, target TEXT, speaker_id TEXT,
                        group_index INTEGER, metadata_json TEXT NOT NULL);
                    CREATE VIRTUAL TABLE IF NOT EXISTS live_grams USING fts5(terms,
                        tokenize="unicode61 remove_diacritics 0 categories 'L* N* Co M*'");
                    CREATE VIRTUAL TABLE IF NOT EXISTS live_vocab USING fts5vocab(live_grams,'row');
                ''')
        except Exception:
            self._db.close()
            raise

    def remember(self, item=None, *, kind='phrase', source='', target=None,
                 speaker_id=None, group_index=None, metadata=None):
        if item is not None:
            kind,source,target,speaker_id,group_index,metadata=(item.kind,item.source,
                item.target,item.speaker_id,item.group_index,item.metadata)
        if not isinstance(source,str) or not source.strip():
            raise ValueError('Context source must not be empty')
        with self._lock,self._db:
            cursor=self._db.execute('INSERT INTO live_items(kind,source,target,speaker_id,group_index,metadata_json) VALUES(?,?,?,?,?,?)',
                (kind,source,target,speaker_id,group_index,json.dumps(metadata or {},ensure_ascii=False)))
            self._db.execute('INSERT INTO live_grams(rowid,terms) VALUES(?,?)',
                (cursor.lastrowid,' '.join(sorted(grams(source)))))
            return cursor.lastrowid

    def relevant(self, text='', *, speaker_id=None, limit=6, distant=True):
        if not isinstance(text,str) or len(text)>8000 or not 1<=limit<=32:
            raise ValueError('Invalid bounded context query')
        query=sorted(grams(text))
        if not query:return []
        with self._lock:
            count=self._db.execute('SELECT count(*) FROM live_items').fetchone()[0]
            frequencies=[]
            for start in range(0,len(query),400):
                block=query[start:start+400]
                frequencies.extend(self._db.execute('SELECT term,doc FROM live_vocab WHERE term IN ('+
                    ','.join('?' for _ in block)+')',block).fetchall())
            # Ignore ubiquitous corpus terms rather than hand-written words
            # from one language. Select rare matching terms before the bounded
            # FTS query so distant names remain searchable after hours of use.
            selected=sorted((row for row in frequencies if count<8 or row['doc']<=count*.5),
                            key=lambda row:(row['doc'],row['term']))[:128]
            if not selected:return []
            match=' OR '.join('"'+row['term']+'"' for row in selected)
            sql='''SELECT i.* FROM live_grams JOIN live_items i ON i.id=live_grams.rowid
                   WHERE live_grams MATCH ?'''
            parameters=[match]
            if speaker_id is not None:
                sql+=' AND (i.speaker_id IS NULL OR i.speaker_id=?)';parameters.append(speaker_id)
            sql+=' ORDER BY bm25(live_grams),i.id DESC LIMIT ?';parameters.append(limit)
            rows=self._db.execute(sql,parameters).fetchall()
        return [ContextItem(row['kind'],row['source'],row['target'],row['speaker_id'],
                row['group_index'],json.loads(row['metadata_json'])) for row in rows]

    def close(self):
        with self._lock:self._db.close()
