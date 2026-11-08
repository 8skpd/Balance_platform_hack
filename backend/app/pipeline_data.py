"""Shared stage contracts and relational facts with exact application membership."""
import json

COMPARISON_SQL = '        SELECT coalesce(b.APP_ID, a.APP_ID) APP_ID, coalesce(b.PATH, a.PATH) PATH,\n            coalesce(b.ARRAY_KEY, a.ARRAY_KEY) ARRAY_KEY,\n            coalesce(b.PREFIX, a.PREFIX) PREFIX, coalesce(b.CLASS, a.CLASS) AS "CLASS",\n            coalesce(b.SUFFIX, a.SUFFIX) SUFFIX, coalesce(b.KEY, a.KEY) AS "KEY",\n            b.VALUE VALUE_BEFORE, a.VALUE VALUE_AFTER,\n            b.VALUE_TYPE TYPE_BEFORE, a.VALUE_TYPE TYPE_AFTER,\n            b.ADJUSTED_VALUE ADJUSTED_BEFORE, a.ADJUSTED_VALUE ADJUSTED_AFTER,\n            CASE WHEN b.APP_ID IS NULL THEN \'ATTRIBUTE_NEW\'\n                 WHEN a.APP_ID IS NULL THEN \'ATTRIBUTE_LOST\'\n                 WHEN b.VALUE = a.VALUE AND b.VALUE_TYPE = a.VALUE_TYPE THEN \'SAME\'\n                 ELSE \'VALUE_CHANGED\' END CHANGE_TYPE,\n            CASE WHEN b.APP_ID IS NOT NULL AND a.APP_ID IS NOT NULL\n                 AND b.VALUE = a.VALUE AND b.VALUE_TYPE = a.VALUE_TYPE THEN 0 ELSE 1 END DIFF_FLAG\n        FROM (SELECT * FROM normalized WHERE SOURCE=\'BEFORE\') b\n        FULL OUTER JOIN (SELECT * FROM normalized WHERE SOURCE=\'AFTER\') a\n        ON b.APP_ID=a.APP_ID AND b.PATH=a.PATH AND b.ARRAY_KEY=a.ARRAY_KEY'
NORMAL_COLUMNS = ['APP_ID','SOURCE','PATH','PREFIX','CLASS','SUFFIX','ARRAY_KEY','KEY','VALUE','VALUE_TYPE','ADJUSTED_VALUE']
COMPARE_COLUMNS = ['APP_ID','PATH','ARRAY_KEY','PREFIX','CLASS','SUFFIX','KEY','VALUE_BEFORE','VALUE_AFTER','TYPE_BEFORE','TYPE_AFTER','ADJUSTED_BEFORE','ADJUSTED_AFTER','CHANGE_TYPE','DIFF_FLAG']
SEGMENT_FIELDS = ['DECISION_BEFORE','DECISION_AFTER','PD_MODEL_BEFORE','PD_MODEL_AFTER','PRODUCTS','PARTNER_TYPE','ROUTING_METHOD','TOP_OFFER_BEFORE','TOP_OFFER_AFTER','CUTOFF_BEFORE','CUTOFF_AFTER','REJECT_REASON_BEFORE','REJECT_REASON_AFTER','SCORE_OKB_BEFORE','SCORE_OKB_AFTER']
FACT_FIELDS = ['PATH','PREFIX','CLASS','SUFFIX','KEY','ARRAY_KEY','SOURCE','ADJUSTED_VALUE','CHANGE_TYPE','DIFF_FLAG']

def quoted(fields): return ', '.join('"'+f+'"' for f in fields)

def fact_rows_sql():
    return '''SELECT n.APP_ID,n.PATH,n.PREFIX,n.CLASS,n.SUFFIX,n.KEY,n.ARRAY_KEY,n.SOURCE,
      n.ADJUSTED_VALUE,c.CHANGE_TYPE,c.DIFF_FLAG
      FROM normalized n JOIN comparison c USING(APP_ID,PATH,ARRAY_KEY)
      JOIN applications a USING(APP_ID)'''

def build_facts(conn):
    conn.execute('CREATE TEMP VIEW fact_input AS '+fact_rows_sql())
    conn.execute('CREATE TABLE facts AS SELECT row_number() OVER ()::BIGINT FACT_ID,* FROM (SELECT '+quoted(FACT_FIELDS)+',count(DISTINCT APP_ID)::BIGINT CNT FROM fact_input GROUP BY ALL)')
    match=' AND '.join('f."'+k+'" IS NOT DISTINCT FROM n."'+k+'"' for k in FACT_FIELDS)
    conn.execute('CREATE TABLE fact_members AS SELECT f.FACT_ID,n.APP_ID,n.PATH,n.ARRAY_KEY FROM fact_input n JOIN facts f ON '+match)
    conn.execute('DROP VIEW fact_input')

def stream(conn,sql,args=()):
    cursor=conn.cursor()
    try:
        cursor.execute(sql,args);names=[d[0] for d in cursor.description]
        while True:
            chunk=cursor.fetchmany(256)
            if not chunk:break
            for row in chunk:yield dict(zip(names,row))
    finally:cursor.close()

class StageData:
    """Read-only, streaming interface supplied to comparison and aggregation scripts."""
    def __init__(self,conn): self.conn=conn
    def normalized(self):return stream(self.conn,'SELECT * FROM normalized ORDER BY APP_ID,SOURCE,PATH,ARRAY_KEY')
    def segments(self):return stream(self.conn,'SELECT * FROM segments ORDER BY APP_ID')
    def comparison(self):return stream(self.conn,'SELECT * FROM comparison ORDER BY APP_ID,PATH,ARRAY_KEY')
    def default_comparison(self):return stream(self.conn,COMPARISON_SQL)
    def default_facts(self):
        return stream(self.conn,'SELECT '+quoted(FACT_FIELDS)+', count(DISTINCT APP_ID)::BIGINT CNT,list(DISTINCT APP_ID ORDER BY APP_ID) APP_IDS FROM ('+fact_rows_sql()+') GROUP BY ALL')
