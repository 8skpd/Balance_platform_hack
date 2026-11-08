"""Immutable dataset reader and bounded writer; small legacy directories stay supported."""
import json
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

PACK_AFTER = 2000  # documents, not pairs
SIDES = ('BEFORE_MASTER', 'AFTER_BETA')

@contextmanager
def connection(folder):
    path = Path(folder)/'documents.sqlite3'
    conn=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    try: yield conn
    finally: conn.close()

class DatasetWriter:
    def __init__(self, folder):
        self.folder=Path(folder);self.count=0;self.packed=False
        self.conn=sqlite3.connect(self.folder/'documents.sqlite3')
        self.conn.execute('PRAGMA journal_mode=OFF')
        self.conn.execute('PRAGMA synchronous=OFF')
        self.conn.execute('PRAGMA cache_size=-8192')
        self.conn.execute('CREATE TABLE documents (side TEXT NOT NULL, app_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(side,app_id)) WITHOUT ROWID')
    def __enter__(self):return self
    def __exit__(self,*args):self.conn.close()
    def write(self, side, app_id, encoded):
        self.conn.execute('INSERT INTO documents VALUES (?,?,?)',(side,app_id,encoded.decode('utf-8')))
        self.count+=1
        if self.count<=PACK_AFTER:
            dest=self.folder/'RESULTS'/side/(app_id+'.json');dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(encoded)
        elif not self.packed:
            shutil.rmtree(self.folder/'RESULTS',ignore_errors=True);self.packed=True
    def finish(self):
        self.conn.commit()
        if not self.packed:
            self.conn.close();(self.folder/'documents.sqlite3').unlink()
        return 'sqlite' if self.packed else 'files'

def inventory(folder):
    folder=Path(folder)
    if (folder/'documents.sqlite3').exists():
        with connection(folder) as conn:
            before,after=[conn.execute('SELECT count(*) FROM documents WHERE side=?',(s,)).fetchone()[0] for s in SIDES]
            pairs=conn.execute('SELECT count(*) FROM documents b JOIN documents a USING(app_id) WHERE b.side=? AND a.side=?',SIDES).fetchone()[0]
        return {'before':before,'after':after,'pairs':pairs,'unpaired':before+after-2*pairs}
    before,after=[{p.stem for p in (folder/'RESULTS'/s).glob('*.json')} for s in SIDES]
    return {'before':len(before),'after':len(after),'pairs':len(before & after),'unpaired':len(before ^ after)}

def sample_ids(folder, limit=20):
    folder=Path(folder)
    if (folder/'documents.sqlite3').exists():
        with connection(folder) as conn:
            return [r[0] for r in conn.execute('SELECT b.app_id FROM documents b JOIN documents a USING(app_id) WHERE b.side=? AND a.side=? ORDER BY b.app_id LIMIT ?',(*SIDES,limit))]
    return sorted({p.stem for p in (folder/'RESULTS'/SIDES[0]).glob('*.json')} & {p.stem for p in (folder/'RESULTS'/SIDES[1]).glob('*.json')})[:limit]

def read_raw(folder, side, app_id, max_bytes=None):
    folder=Path(folder)
    if Path(app_id).name!=app_id or side not in SIDES:raise ValueError('Некорректная заявка')
    if (folder/'documents.sqlite3').exists():
        with connection(folder) as conn:
            if max_bytes is not None:
                size=conn.execute('SELECT length(CAST(body AS BLOB)) FROM documents WHERE side=? AND app_id=?',(side,app_id)).fetchone()
                if size and size[0]>max_bytes:raise OverflowError('Слишком большой документ')
            row=conn.execute('SELECT body FROM documents WHERE side=? AND app_id=?',(side,app_id)).fetchone()
            if not row:raise FileNotFoundError(app_id)
            return row[0].encode('utf-8')
    path=folder/'RESULTS'/side/(app_id+'.json')
    if path.is_symlink():raise ValueError('Ссылки на файлы не поддерживаются')
    with path.open('rb') as stream:raw=stream.read(-1 if max_bytes is None else max_bytes+1)
    if max_bytes is not None and len(raw)>max_bytes:raise OverflowError('Слишком большой документ')
    return raw

def iter_pairs(folder):
    folder=Path(folder)
    if (folder/'documents.sqlite3').exists():
        with connection(folder) as conn:
            for app_id,before,after in conn.execute('SELECT b.app_id,b.body,a.body FROM documents b JOIN documents a USING(app_id) WHERE b.side=? AND a.side=? ORDER BY b.app_id',SIDES):
                yield app_id,before,after
    else:
        ids=sorted(p.stem for p in (folder/'RESULTS'/SIDES[0]).glob('*.json'))
        for app_id in ids:yield app_id,read_raw(folder,SIDES[0],app_id),read_raw(folder,SIDES[1],app_id)


def iter_ids(folder):
    folder=Path(folder)
    if (folder/'documents.sqlite3').exists():
        with connection(folder) as conn:
            for row in conn.execute('SELECT b.app_id FROM documents b JOIN documents a USING(app_id) WHERE b.side=? AND a.side=? ORDER BY b.app_id',SIDES):yield row[0]
    else:
        before={p.stem for p in (folder/'RESULTS'/SIDES[0]).glob('*.json')}
        after={p.stem for p in (folder/'RESULTS'/SIDES[1]).glob('*.json')}
        yield from sorted(before & after)
