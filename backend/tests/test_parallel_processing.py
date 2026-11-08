import json
from pathlib import Path
import duckdb
import pytest
from app import dataset_storage as storage
from app.parallel_normalization import run
from app.worker import build_database
from app.mart import Mart,StreamingMart
from app.normalizer import DEFAULT_CONFIG


def make_dataset(folder,pairs=1300):
    folder.mkdir()
    with storage.DatasetWriter(folder) as writer:
        for side in storage.SIDES:
            for i in range(pairs):
                doc={'appData':{'application_id':f'A{i:05d}'},'score':i+int(side=='AFTER_BETA' and i%7==0),'decisionResult':{'decision':'APPROVE'}}
                writer.write(side,f'A{i:05d}',json.dumps(doc).encode())
        return writer.finish()


def test_packed_reader_preview_and_parallel_match_serial(tmp_path):
    folder=tmp_path/'dataset';assert make_dataset(folder)=='sqlite'
    assert not (folder/'RESULTS').exists()
    assert storage.inventory(folder)=={'before':1300,'after':1300,'pairs':1300,'unpaired':0}
    assert len(storage.sample_ids(folder))==20 and len(list(storage.iter_ids(folder)))==1300
    assert json.loads(storage.read_raw(folder,'AFTER_BETA','A00000'))['score']==1
    with pytest.raises(OverflowError):storage.read_raw(folder,'BEFORE_MASTER','A00000',5)
    with pytest.raises(FileNotFoundError):storage.read_raw(folder,'BEFORE_MASTER','missing')
    from app.normalization_preview import preview
    assert preview(folder,DEFAULT_CONFIG,'A01299')['pairs']==1300
    databases=[]
    for workers in [1,3]:
        work=tmp_path/str(workers);work.mkdir();progress=[]
        nf,sf,stats=run(folder,DEFAULT_CONFIG,work,1300,workers,progress.append)
        assert stats['pairs']==1300 and stats['workers']==workers
        assert stats['processes']>=min(2,workers)
        assert progress[-1]==1300 and progress==sorted(progress)
        conn=duckdb.connect();conn.execute('SET threads=1');build_database(conn,nf,sf);databases.append(conn)
        assert StreamingMart(conn).count()==3900
        assert list(StreamingMart(conn).segments())==Mart(conn).segments()
    for table in ['normalized','segments','comparison','applications','facts']:
        assert databases[0].execute(f'SELECT * FROM {table} ORDER BY ALL').fetchall()==databases[1].execute(f'SELECT * FROM {table} ORDER BY ALL').fetchall()
    for conn in databases:conn.close()


def test_parallel_failure_keeps_context(tmp_path):
    folder=tmp_path/'dataset';make_dataset(folder)
    import sqlite3
    with sqlite3.connect(folder/'documents.sqlite3') as c:c.execute("UPDATE documents SET body='[]' WHERE app_id='A00500' AND side='AFTER_BETA'")
    work=tmp_path/'work';work.mkdir()
    with pytest.raises(ValueError,match='AFTER/A00500'):
        run(folder,DEFAULT_CONFIG,work,1300,3,lambda n:None)


def test_small_writer_keeps_legacy_files(tmp_path):
    folder=tmp_path/'dataset';assert make_dataset(folder,2)=='files'
    assert not (folder/'documents.sqlite3').exists()
    assert len(list(storage.iter_pairs(folder)))==2
    assert storage.inventory(folder)['pairs']==2
