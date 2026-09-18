from __future__ import annotations
from fastapi import Request, Form, HTTPException
from .main import app, now, notify
from .database import db
from .mobile_api import _token_user
from . import driver_experience as de

def _task_offer(row):
    location=(row['location_note'] or row['neighborhood'] or 'Spokane, WA').strip()
    return {
        'id':row['id'],
        'job_type':'task',
        'pickup':location,
        'dropoff':location,
        'item_description':row['description'],
        'title':row['title'],
        'category':row['category'],
        'timing_text':row['timing_text'],
        'driver_pay_cents':int(row['offered_cents'] or 0),
        'distance_miles':0.0,
        'stop_count':1,
        'estimated_minutes':0,
        'complexity':'Community task',
        'pay_per_mile':None,
        'status':row['status'],
    }

def _task_current(con,uid):
    row=con.execute("SELECT * FROM community_tasks WHERE worker_id=? AND status IN ('accepted','awaiting_confirmation') ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
    if not row:return None
    location=(row['location_note'] or row['neighborhood'] or 'Spokane, WA').strip()
    return {
        'kind':'task','id':row['id'],'status':row['status'],
        'stage':'Task accepted' if row['status']=='accepted' else 'Waiting for customer confirmation',
        'next_label':row['title'],'next_address':location,
        'notes':row['description'],'expected_earnings_cents':int(row['offered_cents'] or 0),
        'eta_minutes':None,'distance_to_next_miles':None,'arrived':False,
        'gps_age_seconds':None,'gps_stale':False,'route_deviation':False,
        'trip_miles':0.0,'stop_count':1,'category':row['category'],'timing_text':row['timing_text']
    }

for r in list(app.router.routes):
    if getattr(r,'path',None)=='/api/mobile/smart-dashboard' and 'GET' in (getattr(r,'methods',set()) or set()):
        app.router.routes.remove(r)

@app.get('/api/mobile/smart-dashboard')
def smart_dashboard_with_tasks(request:Request):
    u,_=_token_user(request)
    with db() as con:
        p=con.execute('SELECT online,location_updated_at FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        current=de._current_job_payload(con,u['id']) or _task_current(con,u['id'])
        offers=de._enhanced_offers(con)
        rows=con.execute("SELECT * FROM community_tasks WHERE status='open' AND poster_id<>? ORDER BY id DESC LIMIT 40",(u['id'],)).fetchall()
        offers.extend(_task_offer(r) for r in rows)
        return {'driver':{'id':u['id'],'name':u['name'],'online':bool(p and p['online'])},'current_job':current,'money':de._money(con,u['id']),'offers':offers}

@app.post('/api/mobile/tasks/{tid}/accept')
def mobile_accept_task(tid:int,request:Request):
    u,_=_token_user(request)
    with db() as con:
        p=con.execute('SELECT online FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        if not p or not p['online']: raise HTTPException(409,'Go online before accepting a task')
        c=con.execute('SELECT identity_status,background_status FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone()
        if not c or c['identity_status']!='approved' or c['background_status']!='approved':
            raise HTTPException(403,'Driver verification is not complete')
        task=con.execute('SELECT * FROM community_tasks WHERE id=?',(tid,)).fetchone()
        if not task: raise HTTPException(404,'Task not found')
        if task['poster_id']==u['id']: raise HTTPException(400,'You cannot accept your own task')
        cur=con.execute("UPDATE community_tasks SET worker_id=?,status='accepted',accepted_at=?,updated_at=? WHERE id=? AND status='open'",(u['id'],now(),now(),tid))
        if cur.rowcount!=1: raise HTTPException(409,'Someone else already accepted this task')
        notify(con,task['poster_id'],'Task accepted',f'Your task #{tid} was accepted by a LocalLoop driver.')
    return {'ok':True,'task_id':tid,'status':'accepted'}

@app.post('/api/mobile/tasks/{tid}/complete')
def mobile_complete_task(tid:int,request:Request,note:str=Form('')):
    u,_=_token_user(request)
    with db() as con:
        task=con.execute("SELECT * FROM community_tasks WHERE id=? AND worker_id=? AND status='accepted'",(tid,u['id'])).fetchone()
        if not task: raise HTTPException(409,'Task is not active for this driver')
        con.execute("UPDATE community_tasks SET status='awaiting_confirmation',worker_note=?,worker_completed_at=?,updated_at=? WHERE id=?",(note.strip()[:1000],now(),now(),tid))
        notify(con,task['poster_id'],'Task ready for confirmation',f'Task #{tid} was marked finished. Please confirm the work.')
    return {'ok':True,'task_id':tid,'status':'awaiting_confirmation'}
