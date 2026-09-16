from __future__ import annotations
import os, re, sqlite3
from pathlib import Path

DATABASE_URL=os.environ.get('DATABASE_URL','').strip()
BASE=Path(__file__).resolve().parent
SQLITE_PATH=Path(os.environ.get('LOCALLOOP_DB',str(BASE.parent/'localloop.db')))

_ID_TABLES={'users','deliveries','shopping_orders','shopping_stops','delivery_events','notifications','ratings','disputes','promos','ledger','payment_records','community_tasks','community_task_reports'}

def _adapt(sql:str):
    s=sql.strip()
    pragma=re.match(r"PRAGMA\s+table_info\(([^)]+)\)",s,re.I)
    if pragma:
        table=pragma.group(1).strip().strip('"\'')
        return "SELECT column_name AS name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",(table,),False
    s=re.sub(r"date\('now'\)",'CURRENT_DATE',s,flags=re.I)
    # SQLite integer auto IDs become PostgreSQL BIGSERIAL. Foreign-key IDs are widened to BIGINT.
    s=re.sub(r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT','BIGSERIAL PRIMARY KEY',s,flags=re.I)
    s=re.sub(r'INTEGER(\s+(?:NOT\s+NULL\s+)?)REFERENCES',r'BIGINT\1REFERENCES',s,flags=re.I)
    s=re.sub(r'INTEGER\s+PRIMARY\s+KEY\s+REFERENCES',r'BIGINT PRIMARY KEY REFERENCES',s,flags=re.I)
    ignore=bool(re.match(r'INSERT\s+OR\s+IGNORE\s+INTO',s,re.I))
    if ignore:
        s=re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO','INSERT INTO',s,count=1,flags=re.I)
        s=s.rstrip().rstrip(';')+' ON CONFLICT DO NOTHING'
    rep=re.match(r'INSERT\s+OR\s+REPLACE\s+INTO\s+([\w]+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)',s,re.I|re.S)
    if rep:
        table,cols,vals=rep.group(1),rep.group(2),rep.group(3)
        names=[c.strip() for c in cols.split(',')]
        updates=', '.join(f'{c}=EXCLUDED.{c}' for c in names[1:])
        s=f"INSERT INTO {table} ({cols}) VALUES ({vals}) ON CONFLICT ({names[0]}) DO UPDATE SET {updates}"
    s=s.replace('?','%s')
    return s,None,True

class PgCursor:
    def __init__(self,cur,lastrowid=None): self._cur=cur; self.lastrowid=lastrowid
    @property
    def rowcount(self): return self._cur.rowcount
    def fetchone(self): return self._cur.fetchone()
    def fetchall(self): return self._cur.fetchall()
    def __iter__(self): return iter(self._cur)

class PgConnection:
    def __init__(self):
        import psycopg
        from psycopg.rows import dict_row
        self._psycopg=psycopg
        self._con=psycopg.connect(DATABASE_URL,row_factory=dict_row)
        self._savepoint_seq=0
    def execute(self,sql,params=()):
        adapted,forced_params,_=_adapt(sql)
        if forced_params is not None: params=forced_params
        table_match=re.match(r'\s*INSERT\s+(?:OR\s+(?:IGNORE|REPLACE)\s+)?INTO\s+([\w]+)',sql,re.I)
        table=table_match.group(1).lower() if table_match else None
        wants_id=table in _ID_TABLES and 'RETURNING ' not in adapted.upper()
        if wants_id: adapted=adapted.rstrip().rstrip(';')+' RETURNING id'
        # A number of legacy startup migrations intentionally probe schema and catch
        # database errors. SQLite allows the next statement to continue, while
        # PostgreSQL marks the whole transaction failed. Isolate each statement in
        # a savepoint so a caught compatibility/probe error cannot poison the rest
        # of application startup.
        self._savepoint_seq += 1
        sp=f'll_stmt_{self._savepoint_seq}'
        ctl=self._con.cursor()
        ctl.execute(f'SAVEPOINT {sp}')
        try:
            cur=self._con.cursor(); cur.execute(adapted,params or ())
            last=None
            if wants_id:
                row=cur.fetchone(); last=(row or {}).get('id') if isinstance(row,dict) else (row[0] if row else None)
            ctl.execute(f'RELEASE SAVEPOINT {sp}')
            return PgCursor(cur,last)
        except Exception as e:
            # Recover the PostgreSQL transaction before propagating the original
            # exception. Callers that deliberately catch schema-probe errors can
            # then safely execute their next statement, matching SQLite behavior.
            try:
                ctl.execute(f'ROLLBACK TO SAVEPOINT {sp}')
                ctl.execute(f'RELEASE SAVEPOINT {sp}')
            except Exception:
                self._con.rollback()
            if isinstance(e,self._psycopg.errors.UniqueViolation):
                raise sqlite3.IntegrityError(str(e)) from e
            raise
    def executescript(self,script):
        for stmt in script.split(';'):
            if stmt.strip(): self.execute(stmt)
    def __enter__(self): return self
    def __exit__(self,exc_type,exc,tb):
        try:
            if exc_type: self._con.rollback()
            else: self._con.commit()
        finally: self._con.close()
        return False

def db():
    if DATABASE_URL.startswith('postgres://') or DATABASE_URL.startswith('postgresql://'):
        return PgConnection()
    SQLITE_PATH.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(SQLITE_PATH)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    return con
