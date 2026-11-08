"""Read-only mart API for bundled and user-defined checks."""

import json

def records(cursor):
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]

def where(filters):
    mapping = {'path': 'PATH', 'key': 'KEY', 'array_key': 'ARRAY_KEY',
        'diff_flag': 'DIFF_FLAG', 'change_type': 'CHANGE_TYPE',
        'value_before': 'VALUE_BEFORE', 'value_after': 'VALUE_AFTER', 'app_ids': 'APP_ID'}
    clauses, params = [], []
    for name, value in filters.items():
        if value is None:
            continue
        if name == 'path_prefix':
            clauses.append('starts_with(PATH, ?)')
            params.append(value)
            continue
        col = mapping[name]
        if isinstance(value, (list, tuple, set)):
            if not value:
                clauses.append('FALSE')
                continue
            clauses.append(f'{col} IN ({",".join("?" for _ in value)})')
            params.extend(value)
        else:
            clauses.append(f'{col} = ?')
            params.append(value)
    return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), params

class Mart:
    def __init__(self, conn):
        self.conn = conn

    def rows(self, **filters):
        sql, args = where(filters)
        return records(self.conn.execute('SELECT * FROM comparison' + sql, args))

    def count(self, **filters):
        sql, args = where(filters)
        return self.conn.execute('SELECT count(*) FROM comparison' + sql, args).fetchone()[0]

    def app_ids(self, **filters):
        sql, args = where(filters)
        return [r[0] for r in self.conn.execute('SELECT DISTINCT APP_ID FROM comparison' + sql + ' ORDER BY APP_ID', args).fetchall()]

    def segments(self):
        return records(self.conn.execute('SELECT * FROM segments ORDER BY APP_ID'))

    def product_segments(self):
        columns = {r[0] for r in self.conn.execute('DESCRIBE segments').fetchall()}
        if 'PRODUCT_CONTEXT' not in columns:
            raise ValueError('В старом отчёте нет контекста по продуктам. Создайте новый расчёт.')
        result = []
        for app_id, payload in self.conn.execute('SELECT APP_ID, PRODUCT_CONTEXT FROM segments ORDER BY APP_ID').fetchall():
            result.extend({'APP_ID':app_id, **row} for row in json.loads(payload))
        return result

    def total_apps(self):
        return self.conn.execute('SELECT count(*) FROM segments').fetchone()[0]


class StreamingMart(Mart):
    """Bounded iterators for bundled checks; the public user-code list API is unchanged."""
    def iterate(self,sql,args=()):
        cursor=self.conn.cursor()
        try:
            cursor.execute(sql,args);names=[d[0] for d in cursor.description]
            while True:
                chunk=cursor.fetchmany(1000)
                if not chunk:break
                for row in chunk:yield dict(zip(names,row))
        finally:cursor.close()
    def rows(self,**filters):
        sql,args=where(filters)
        return self.iterate('SELECT * FROM comparison'+sql,args)
    def segments(self):return self.iterate('SELECT * FROM segments ORDER BY APP_ID')
    def product_segments(self):
        columns={r[0] for r in self.conn.execute('DESCRIBE segments').fetchall()}
        if 'PRODUCT_CONTEXT' not in columns:raise ValueError('В старом отчёте нет контекста по продуктам. Создайте новый расчёт.')
        for item in self.iterate('SELECT APP_ID,PRODUCT_CONTEXT FROM segments ORDER BY APP_ID'):
            for row in json.loads(item['PRODUCT_CONTEXT']):yield {'APP_ID':item['APP_ID'],**row}
