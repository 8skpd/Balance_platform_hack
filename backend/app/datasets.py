import json
import shutil
import zipfile
from pathlib import Path

from .settings import ROOT, DATASETS
from . import store
from sqlalchemy import select

def bootstrap():
    """Import only the supplied data. Never execute or extract arbitrary archive paths."""
    target = DATASETS / 'RUN-20260827-0001'
    if (target / 'manifest.json').exists():
        return
    archive = ROOT / 'ХАКАТОН_СИСТЕМА_АНАЛИЗА_ИЗМЕНЕНИЙ/ПАКЕТ/ЗАДАНИЕ.zip'
    if not archive.exists():
        return
    prefix = 'ЗАДАНИЕ/data/RUN-20260827-0001/'
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            if not item.filename.startswith(prefix) or item.is_dir():
                continue
            relative = Path(item.filename[len(prefix):])
            if '..' in relative.parts or relative.is_absolute():
                raise ValueError('Небезопасный путь в архиве')
            # Manifest is written last, so a failed import can be retried.
            if relative.name == 'manifest.json':
                continue
            dest = target / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(item) as src, dest.open('wb') as dst:
                shutil.copyfileobj(src, dst)
        target.mkdir(parents=True, exist_ok=True)
        (target / 'manifest.json').write_bytes(z.read(prefix + 'manifest.json'))

def paths(dataset_id, owner_id=None):
    if not dataset_id or Path(dataset_id).name != dataset_id or dataset_id in ('.', '..'):
        raise ValueError('Датасет не найден')
    if dataset_id.startswith('upload-'):
        with store.engine.connect() as conn:
            row=conn.execute(select(store.dataset_imports.c.id).join(store.jobs, store.jobs.c.id==store.dataset_imports.c.job_id)
                .where(store.dataset_imports.c.id==dataset_id, store.dataset_imports.c.owner_id==owner_id,
                       store.jobs.c.status=='completed')).first()
        if not row: raise ValueError('Датасет не найден')
    target = (DATASETS / dataset_id).resolve()
    if target.parent != DATASETS.resolve() or not target.is_dir():
        raise ValueError('Датасет не найден')
    return target

def inventory(owner_id=None):
    result = []
    for folder in sorted(DATASETS.iterdir()):
        if folder.name.startswith('upload-'):
            continue  # Uploaded datasets are never implicitly public, even after a crash.
        if not folder.is_dir() or not (folder / 'manifest.json').exists():
            continue
        from .dataset_storage import inventory as dataset_inventory
        result.append({'id':folder.name,'name':folder.name,'visibility':'shared',**dataset_inventory(folder)})
    if owner_id:
        with store.engine.connect() as conn:
            rows=conn.execute(select(store.dataset_imports).join(store.jobs, store.jobs.c.id==store.dataset_imports.c.job_id)
                .where(store.dataset_imports.c.owner_id==owner_id, store.jobs.c.status=='completed')
                .order_by(store.dataset_imports.c.created_at.desc())).mappings()
            for r in rows:
                if (DATASETS/r['id']/'manifest.json').is_file():
                    result.append({'id':r['id'],'name':r['name'],'visibility':'private',
                                   'created_at':r['created_at'], **(r['stats'] or {})})
    return result
