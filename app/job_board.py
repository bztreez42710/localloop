from __future__ import annotations
import re
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from .main import app, page, require_user, now, notify
from .database import db

CATEGORIES={'yard':'Yard & outdoor','moving':'Moving & lifting','cleaning':'Cleaning','assembly':'Assembly & setup','tech':'Tech help','pet':'Pet help','errand':'Errands','home':'Home help','other':'Other'}
PROHIBITED={
 'gun','firearm','ammo','ammunition','weapon','explosive','firework','cannabis','marijuana','thc','weed','cbd',
 'cocaine','meth','fentanyl','heroin','drug','prescription','alcohol','beer','wine','liquor','tobacco','nicotine','vape',
 'sex','escort','porn','gambling','stolen','counterfeit','cash transfer','money transfer','childcare','babysit',
 'electrical panel','gas line','asbestos','hazmat','hazardous waste','roofing','tree removal'
}

SCHEMA='''
CREATE TABLE IF NOT EXISTS community_tasks(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 poster_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 worker_id INTEGER REFERENCES users(id),
 title TEXT NOT NULL, description TEXT NOT NULL, category TEXT NOT NULL,
 neighborhood TEXT NOT NULL DEFAULT '', location_note TEXT NOT NULL DEFAULT '',
 offered_cents INTEGER NOT NULL DEFAULT 0, timing_text TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'open', worker_note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, accepted_at TEXT,
 worker_completed_at TEXT, completed_at TEXT, cancelled_at TEXT
);
CREATE TABLE IF NOT EXISTS community_task_reports(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 task_id INTEGER NOT NULL REFERENCES community_tasks(id) ON DELETE CASCADE,
 reporter_id INTEGER REFERENCES users(id), reason TEXT NOT NULL, details TEXT DEFAULT '',
 status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_community_tasks_status ON community_tasks(status,created_at);
CREATE INDEX IF NOT EXISTS idx_community_tasks_worker ON community_tasks(worker_id,status);
CREATE INDEX IF NOT EXISTS idx_community_task_reports ON community_task_reports(status,created_at);
'''

def _clean(v:str,n:int)->str: return (v or '').strip()[:n]
def _blocked(text:str)->bool:
    t=re.sub(r'[^a-z0-9 ]+',' ',(text or '').lower())
    return any(term in t for term in PROHIBITED)

def _staff(con,uid:int)->bool:
    return bool(con.execute('SELECT 1 FROM staff_access WHERE user_id=?',(uid,)).fetchone())

def _verified_to_post(con,u)->bool:
    if u['role']=='admin': return not _staff(con,u['id'])
    if u['role'] in {'customer','business'}:
        r=con.execute('SELECT verification_status FROM account_verifications WHERE user_id=?',(u['id'],)).fetchone()
        return bool(r and r['verification_status']=='verified')
    return False

def _eligible_to_accept(con,u)->bool:
    if u['role']=='admin': return not _staff(con,u['id'])
    if u['role']=='driver':
        c=con.execute('SELECT identity_status,background_status FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone()
        return bool(c and c['identity_status']=='approved' and c['background_status']=='approved')
    if u['role'] in {'customer','business'}:
        r=con.execute('SELECT verification_status FROM account_verifications WHERE user_id=?',(u['id'],)).fetchone()
        return bool(r and r['verification_status']=='verified')
    return False

@app.on_event('startup')
def job_board_startup():
    with db() as con: con.executescript(SCHEMA)

@app.get('/jobs',response_class=HTMLResponse)
def job_board(request:Request,category:str='',view:str='available'):
    u=require_user(request)
    with db() as con:
        eligible=_eligible_to_accept(con,u)
        can_post=_verified_to_post(con,u)
        args=[]
        if view=='mine':
            sql='''SELECT t.*,p.name poster_name,w.name worker_name FROM community_tasks t JOIN users p ON p.id=t.poster_id LEFT JOIN users w ON w.id=t.worker_id WHERE (t.poster_id=? OR t.worker_id=?)'''; args=[u['id'],u['id']]
        else:
            sql="SELECT t.*,p.name poster_name,w.name worker_name FROM community_tasks t JOIN users p ON p.id=t.poster_id LEFT JOIN users w ON w.id=t.worker_id WHERE t.status='open' AND t.poster_id<>?"; args=[u['id']]
        if category in CATEGORIES:
            sql+=' AND t.category=?'; args.append(category)
        sql+=' ORDER BY t.id DESC LIMIT 100'
        tasks=con.execute(sql,args).fetchall()
        mine=con.execute('SELECT t.*,p.name poster_name,w.name worker_name FROM community_tasks t JOIN users p ON p.id=t.poster_id LEFT JOIN users w ON w.id=t.worker_id WHERE t.poster_id=? OR t.worker_id=? ORDER BY t.id DESC LIMIT 40',(u['id'],u['id'])).fetchall()
    return page(request,'job_board.html',tasks=tasks,mine=mine,categories=CATEGORIES,category=category,view=view,eligible=eligible,can_post=can_post)

@app.post('/jobs')
def post_task(request:Request,title:str=Form(...),description:str=Form(...),category:str=Form(...),neighborhood:str=Form(''),location_note:str=Form(''),offered_dollars:float=Form(...),timing_text:str=Form(''),lawful_attestation:str=Form('')):
    u=require_user(request)
    title=_clean(title,100); description=_clean(description,2500); neighborhood=_clean(neighborhood,80); location_note=_clean(location_note,180); timing_text=_clean(timing_text,120)
    if lawful_attestation!='yes': raise HTTPException(400,'Confirm that this is a lawful, safe task you are authorized to request.')
    if category not in CATEGORIES: raise HTTPException(400,'Choose a task category.')
    if len(title)<4 or len(description)<10: raise HTTPException(400,'Add a clear title and description.')
    if _blocked(' '.join((title,description,location_note))): raise HTTPException(400,'This task appears to involve work LocalLoop does not allow on the community task board.')
    if offered_dollars<5 or offered_dollars>5000: raise HTTPException(400,'Task offers must be between $5 and $5,000.')
    with db() as con:
        if not _verified_to_post(con,u): raise HTTPException(403,'Complete account verification before posting tasks.')
        t=now(); con.execute('INSERT INTO community_tasks(poster_id,title,description,category,neighborhood,location_note,offered_cents,timing_text,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(u['id'],title,description,category,neighborhood,location_note,round(offered_dollars*100),timing_text,'open',t,t))
    return RedirectResponse('/jobs?view=mine',303)

@app.post('/jobs/{tid}/accept')
def accept_task(tid:int,request:Request):
    u=require_user(request)
    with db() as con:
        if not _eligible_to_accept(con,u): raise HTTPException(403,'Complete the required account verification before accepting community tasks.')
        task=con.execute('SELECT * FROM community_tasks WHERE id=?',(tid,)).fetchone()
        if not task: raise HTTPException(404)
        if task['poster_id']==u['id']: raise HTTPException(400,'You cannot accept your own task.')
        cur=con.execute("UPDATE community_tasks SET worker_id=?,status='accepted',accepted_at=?,updated_at=? WHERE id=? AND status='open'",(u['id'],now(),now(),tid))
        if cur.rowcount!=1: raise HTTPException(409,'Someone else already accepted this task.')
        notify(con,task['poster_id'],'Task accepted',f'Your task #{tid} was accepted.')
    return RedirectResponse('/jobs?view=mine',303)

@app.post('/jobs/{tid}/worker-complete')
def worker_complete(tid:int,request:Request,note:str=Form('')):
    u=require_user(request); note=_clean(note,1000)
    with db() as con:
        task=con.execute('SELECT * FROM community_tasks WHERE id=? AND worker_id=?',(tid,u['id'])).fetchone()
        if not task: raise HTTPException(404)
        if task['status']!='accepted': raise HTTPException(400,'This task is not awaiting completion.')
        con.execute("UPDATE community_tasks SET status='awaiting_confirmation',worker_note=?,worker_completed_at=?,updated_at=? WHERE id=?",(note,now(),now(),tid))
        notify(con,task['poster_id'],'Task ready for confirmation',f'Task #{tid} was marked finished. Please confirm the work.')
    return RedirectResponse('/jobs?view=mine',303)

@app.post('/jobs/{tid}/confirm')
def confirm_task(tid:int,request:Request):
    u=require_user(request)
    with db() as con:
        task=con.execute('SELECT * FROM community_tasks WHERE id=?',(tid,)).fetchone()
        if not task or (u['role']!='admin' and task['poster_id']!=u['id']): raise HTTPException(404)
        if task['status']!='awaiting_confirmation': raise HTTPException(400,'The worker has not marked this task finished yet.')
        con.execute("UPDATE community_tasks SET status='completed',completed_at=?,updated_at=? WHERE id=?",(now(),now(),tid))
        if task['worker_id']: notify(con,task['worker_id'],'Task confirmed',f'Task #{tid} was confirmed complete by the poster.')
    return RedirectResponse('/jobs?view=mine',303)

@app.post('/jobs/{tid}/cancel')
def cancel_task(tid:int,request:Request):
    u=require_user(request)
    with db() as con:
        task=con.execute('SELECT * FROM community_tasks WHERE id=?',(tid,)).fetchone()
        if not task or (u['role']!='admin' and task['poster_id']!=u['id']): raise HTTPException(404)
        if task['status'] not in {'open','accepted'}: raise HTTPException(400,'This task can no longer be cancelled here.')
        con.execute("UPDATE community_tasks SET status='cancelled',cancelled_at=?,updated_at=? WHERE id=?",(now(),now(),tid))
        if task['worker_id']: notify(con,task['worker_id'],'Task cancelled',f'Task #{tid} was cancelled by the poster.')
    return RedirectResponse('/jobs?view=mine',303)

@app.post('/jobs/{tid}/report')
def report_task(tid:int,request:Request,reason:str=Form(...),details:str=Form('')):
    u=require_user(request); reason=_clean(reason,60); details=_clean(details,1500)
    if reason not in {'unsafe','prohibited','misleading','harassment','fraud','other'}: raise HTTPException(400,'Choose a report reason.')
    with db() as con:
        if not con.execute('SELECT 1 FROM community_tasks WHERE id=?',(tid,)).fetchone(): raise HTTPException(404)
        con.execute('INSERT INTO community_task_reports(task_id,reporter_id,reason,details,status,created_at) VALUES(?,?,?,?,?,?)',(tid,u['id'],reason,details,'open',now()))
    return RedirectResponse('/jobs?reported=1',303)

@app.get('/admin/jobs',response_class=HTMLResponse)
def admin_jobs(request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    with db() as con:
        tasks=con.execute('SELECT t.*,p.name poster_name,w.name worker_name FROM community_tasks t JOIN users p ON p.id=t.poster_id LEFT JOIN users w ON w.id=t.worker_id ORDER BY t.id DESC LIMIT 100').fetchall()
        reports=con.execute("SELECT r.*,t.title,p.name poster_name FROM community_task_reports r JOIN community_tasks t ON t.id=r.task_id JOIN users p ON p.id=t.poster_id WHERE r.status='open' ORDER BY r.id DESC").fetchall()
    return page(request,'admin_jobs.html',tasks=tasks,reports=reports,categories=CATEGORIES)

@app.post('/admin/jobs/reports/{rid}/resolve')
def admin_resolve_task_report(rid:int,request:Request,action:str=Form('dismiss')):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    if action not in {'dismiss','remove_task'}: raise HTTPException(400)
    with db() as con:
        r=con.execute('SELECT * FROM community_task_reports WHERE id=?',(rid,)).fetchone()
        if not r: raise HTTPException(404)
        if action=='remove_task': con.execute("UPDATE community_tasks SET status='cancelled',cancelled_at=?,updated_at=? WHERE id=? AND status!='completed'",(now(),now(),r['task_id']))
        con.execute("UPDATE community_task_reports SET status='resolved' WHERE id=?",(rid,))
    return RedirectResponse('/admin/jobs',303)
