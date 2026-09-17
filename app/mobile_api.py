from __future__ import annotations
import secrets
from fastapi import Request, Form, HTTPException
from fastapi.responses import JSONResponse
from .main import app, verify_password, now, event, notify
from .database import db


def _token_user(request: Request):
    auth=(request.headers.get('authorization') or '').strip()
    if not auth.lower().startswith('bearer '):
        raise HTTPException(401,'Missing mobile token')
    token=auth.split(' ',1)[1].strip()
    with db() as con:
        row=con.execute('SELECT u.* FROM mobile_tokens t JOIN users u ON u.id=t.user_id WHERE t.token=? AND u.active=1',(token,)).fetchone()
    if not row: raise HTTPException(401,'Invalid mobile token')
    return row, token


def _shopping_job(con, row):
    stops=con.execute('SELECT store_name,store_address,shopping_list FROM shopping_stops WHERE order_id=? ORDER BY stop_number',(row['id'],)).fetchall()
    pickup='Multiple Spokane stores'
    if stops:
        pickup=(stops[0]['store_address'] or stops[0]['store_name'] or 'Spokane store').strip()
    details=[]
    for s in stops:
        where=(s['store_address'] or s['store_name'] or '').strip()
        details.append(f"{s['store_name']} ({where}): {s['shopping_list']}" if where and where!=s['store_name'] else f"{s['store_name']}: {s['shopping_list']}")
    return {
        'id':row['id'],
        'job_type':'shopping',
        'pickup':pickup,
        'dropoff':row['dropoff'],
        'item_description':'Personal shopping — '+' | '.join(details),
        'distance_miles':0.0,
        'driver_pay_cents':int(row['driver_pay_cents']),
        'estimated_goods_cents':int(row['estimated_goods_cents'] or 0),
        'actual_goods_cents':int(row['actual_goods_cents'] or 0),
        'status':row['status'],
        'handoff_required':0,
    }


@app.on_event('startup')
def mobile_api_startup():
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS mobile_tokens(
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            platform TEXT DEFAULT '',
            push_token TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            last_used_at TEXT NOT NULL
        )''')

@app.post('/api/mobile/login')
def mobile_login(email:str=Form(...),password:str=Form(...),platform:str=Form('android')):
    with db() as con:
        u=con.execute('SELECT * FROM users WHERE email=?',(email.lower().strip(),)).fetchone()
        if not u or not u['active'] or u['role']!='driver' or not verify_password(password,u['password_hash']):
            raise HTTPException(401,'Invalid driver credentials')
        token=secrets.token_urlsafe(40)
        con.execute('INSERT INTO mobile_tokens(token,user_id,platform,created_at,last_used_at) VALUES(?,?,?,?,?)',(token,u['id'],platform[:20],now(),now()))
        p=con.execute('SELECT online,payout_balance_cents,completed FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
    return {'token':token,'driver':{'id':u['id'],'name':u['name'],'email':u['email'],'online':bool(p and p['online']),'payout_balance_cents':int(p['payout_balance_cents'] if p else 0),'completed':int(p['completed'] if p else 0)}}

@app.post('/api/mobile/logout')
def mobile_logout(request:Request):
    u,token=_token_user(request)
    with db() as con:
        con.execute('DELETE FROM mobile_tokens WHERE token=?',(token,))
        con.execute('UPDATE driver_profiles SET online=0,latitude=NULL,longitude=NULL WHERE user_id=?',(u['id'],))
    return {'ok':True}

@app.get('/api/mobile/me')
def mobile_me(request:Request):
    u,token=_token_user(request)
    with db() as con:
        con.execute('UPDATE mobile_tokens SET last_used_at=? WHERE token=?',(now(),token))
        p=con.execute('SELECT * FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        c=con.execute('SELECT identity_status,background_status,insurance_status FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone()
    ready=bool(c and c['identity_status']=='approved' and c['background_status']=='approved' and c['insurance_status']=='approved')
    return {'id':u['id'],'name':u['name'],'email':u['email'],'online':bool(p and p['online']),'ready':ready,'payout_balance_cents':int(p['payout_balance_cents'] if p else 0),'completed':int(p['completed'] if p else 0)}

@app.get('/api/mobile/offers')
def mobile_offers(request:Request):
    u,_=_token_user(request)
    with db() as con:
        p=con.execute('SELECT online FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        if not p or not p['online']: return {'online':False,'offers':[],'active':[]}
        offers=[]
        for r in con.execute("SELECT id,pickup,dropoff,item_description,distance_miles,driver_pay_cents,status FROM deliveries WHERE status='posted' ORDER BY id DESC LIMIT 40").fetchall():
            x=dict(r); x['job_type']='delivery'; offers.append(x)
        for r in con.execute("SELECT * FROM shopping_orders WHERE status='posted' AND payment_status='funded' ORDER BY id DESC LIMIT 40").fetchall():
            offers.append(_shopping_job(con,r))
        active=[]
        for r in con.execute("SELECT id,pickup,dropoff,item_description,distance_miles,driver_pay_cents,status,handoff_required FROM deliveries WHERE driver_id=? AND status IN ('accepted','picked_up') ORDER BY id DESC",(u['id'],)).fetchall():
            x=dict(r); x['job_type']='delivery'; active.append(x)
        for r in con.execute("SELECT * FROM shopping_orders WHERE driver_id=? AND status IN ('accepted','shopping','delivering') ORDER BY id DESC",(u['id'],)).fetchall():
            active.append(_shopping_job(con,r))
    return {'online':True,'offers':offers,'active':active}

@app.post('/api/mobile/online')
def mobile_online(request:Request,online:int=Form(...)):
    u,_=_token_user(request)
    with db() as con:
        c=con.execute('SELECT identity_status,background_status,insurance_status FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone()
        ready=bool(c and c['identity_status']=='approved' and c['background_status']=='approved' and c['insurance_status']=='approved')
        if online and not ready: raise HTTPException(403,'Driver verification is not complete')
        con.execute('UPDATE driver_profiles SET online=?,latitude=CASE WHEN ?=0 THEN NULL ELSE latitude END,longitude=CASE WHEN ?=0 THEN NULL ELSE longitude END WHERE user_id=?',(1 if online else 0,1 if online else 0,1 if online else 0,u['id']))
    return {'ok':True,'online':bool(online)}

@app.post('/api/mobile/location')
def mobile_location(request:Request,latitude:float=Form(...),longitude:float=Form(...)):
    u,_=_token_user(request)
    if not (47.45 <= latitude <= 47.85 and -117.75 <= longitude <= -117.05): raise HTTPException(400,'Location is outside the Spokane service area')
    with db() as con:
        p=con.execute('SELECT online FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        active_delivery=con.execute("SELECT 1 FROM deliveries WHERE driver_id=? AND status IN ('accepted','picked_up') LIMIT 1",(u['id'],)).fetchone()
        active_shop=con.execute("SELECT 1 FROM shopping_orders WHERE driver_id=? AND status IN ('accepted','shopping','delivering') LIMIT 1",(u['id'],)).fetchone()
        if not p or not p['online'] or not (active_delivery or active_shop): raise HTTPException(409,'Location sharing is only available while online with an active job')
        con.execute('UPDATE driver_profiles SET latitude=?,longitude=? WHERE user_id=?',(latitude,longitude,u['id']))
    return {'ok':True}

@app.post('/api/mobile/deliveries/{did}/accept')
def mobile_accept(did:int,request:Request):
    u,_=_token_user(request)
    with db() as con:
        p=con.execute('SELECT online FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        if not p or not p['online']: raise HTTPException(409,'Go online before accepting an offer')
        c=con.execute('SELECT identity_status,background_status,insurance_status FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone()
        if not c or any(c[k]!='approved' for k in ('identity_status','background_status','insurance_status')): raise HTTPException(403,'Driver verification is not complete')
        cur=con.execute("UPDATE deliveries SET driver_id=?,status='accepted',accepted_at=?,updated_at=? WHERE id=? AND status='posted'",(u['id'],now(),now(),did))
        if cur.rowcount!=1: raise HTTPException(409,'Delivery was already claimed')
        d=con.execute('SELECT * FROM deliveries WHERE id=?',(did,)).fetchone(); event(con,did,u['id'],'accepted'); notify(con,d['customer_id'],'Driver assigned',f'Delivery #{did} was accepted.')
    return {'ok':True,'delivery_id':did,'status':'accepted'}

@app.post('/api/mobile/shopping/{oid}/accept')
def mobile_accept_shopping(oid:int,request:Request):
    u,_=_token_user(request)
    with db() as con:
        p=con.execute('SELECT online FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        if not p or not p['online']: raise HTTPException(409,'Go online before accepting an offer')
        c=con.execute('SELECT identity_status,background_status,insurance_status FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone()
        if not c or any(c[k]!='approved' for k in ('identity_status','background_status','insurance_status')): raise HTTPException(403,'Driver verification is not complete')
        cur=con.execute("UPDATE shopping_orders SET driver_id=?,status='accepted',accepted_at=?,updated_at=? WHERE id=? AND status='posted' AND payment_status='funded'",(u['id'],now(),now(),oid))
        if cur.rowcount!=1: raise HTTPException(409,'Shopping order was already claimed or is not funded')
        o=con.execute('SELECT customer_id FROM shopping_orders WHERE id=?',(oid,)).fetchone()
        notify(con,o['customer_id'],'Shopper assigned',f'Shopping order #{oid} was accepted.')
    return {'ok':True,'shopping_order_id':oid,'status':'accepted'}

@app.post('/api/mobile/deliveries/{did}/status')
def mobile_status(did:int,request:Request,status:str=Form(...),proof:str=Form(''),handoff_code:str=Form('')):
    u,_=_token_user(request)
    with db() as con:
        d=con.execute('SELECT * FROM deliveries WHERE id=? AND driver_id=?',(did,u['id'])).fetchone()
        if not d: raise HTTPException(404,'Delivery not found')
        if status=='picked_up' and d['status']=='accepted':
            con.execute("UPDATE deliveries SET status='picked_up',picked_up_at=?,updated_at=? WHERE id=?",(now(),now(),did))
        elif status=='delivered' and d['status']=='picked_up':
            if d['handoff_required'] and (handoff_code or '').strip()!=d['handoff_code']: raise HTTPException(400,'Customer handoff code is required')
            con.execute("UPDATE deliveries SET status='delivered',proof=?,handoff_code='',delivered_at=?,updated_at=? WHERE id=?",(proof.strip(),now(),now(),did))
            con.execute('UPDATE driver_profiles SET completed=completed+1,payout_balance_cents=payout_balance_cents+?,latitude=NULL,longitude=NULL WHERE user_id=?',(d['driver_pay_cents'],u['id']))
            con.execute('INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(?,?,?,?,?,?)',(u['id'],did,'driver_earning',d['driver_pay_cents'],'Delivery earning',now()))
            con.execute('INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(NULL,?,?,?,?,?)',(did,'platform_fee',d['platform_fee_cents'],'Platform revenue',now()))
        else: raise HTTPException(409,'Complete delivery steps in order')
        event(con,did,u['id'],status); notify(con,d['customer_id'],'Delivery update',f"Delivery #{did}: {status.replace('_',' ')}")
    return {'ok':True,'delivery_id':did,'status':status}

@app.post('/api/mobile/shopping/{oid}/status')
def mobile_shopping_status(oid:int,request:Request,status:str=Form(...),actual_goods_cents:int=Form(0)):
    u,_=_token_user(request)
    if status not in {'shopping','delivering','delivered'}: raise HTTPException(400,'Invalid shopping status')
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=? AND driver_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404,'Shopping order not found')
        transitions={'accepted':'shopping','shopping':'delivering','delivering':'delivered'}
        if transitions.get(o['status'])!=status: raise HTTPException(409,'Complete shopping steps in order')
        if status=='shopping':
            con.execute("UPDATE shopping_orders SET status='shopping',updated_at=? WHERE id=?",(now(),oid))
        elif status=='delivering':
            actual=max(0,int(actual_goods_cents))
            if actual>int(o['estimated_goods_cents'] or 0): raise HTTPException(400,'Receipt total is above the customer-funded merchandise limit')
            con.execute("UPDATE shopping_orders SET status='delivering',actual_goods_cents=?,updated_at=? WHERE id=?",(actual,now(),oid))
        else:
            payout=int(o['driver_pay_cents'])+int(o['actual_goods_cents'] or 0)
            con.execute("UPDATE shopping_orders SET status='delivered',delivered_at=?,updated_at=? WHERE id=?",(now(),now(),oid))
            con.execute('UPDATE driver_profiles SET completed=completed+1,payout_balance_cents=payout_balance_cents+?,latitude=NULL,longitude=NULL WHERE user_id=?',(payout,u['id']))
            con.execute('INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(?,NULL,?,?,?,?)',(u['id'],'shopping_reimbursement',payout,f'Shopping order #{oid}: merchandise reimbursement + driver pay',now()))
            con.execute('INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(NULL,NULL,?,?,?,?)',('platform_fee',o['platform_fee_cents'],f'Shopping order #{oid}',now()))
            con.execute("INSERT INTO payment_records(user_id,shopping_order_id,provider,amount_cents,status,kind,created_at) VALUES(?,?,?,?,?,?,?)",(u['id'],oid,'stripe',payout,'pending','shopper_payout',now()))
        notify(con,o['customer_id'],'Shopping update',f"Shopping order #{oid}: {status.replace('_',' ')}")
    return {'ok':True,'shopping_order_id':oid,'status':status}

@app.get('/api/mobile/earnings')
def mobile_earnings(request:Request):
    u,_=_token_user(request)
    with db() as con:
        p=con.execute('SELECT payout_balance_cents,completed FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        rows=[dict(r) for r in con.execute("SELECT l.id,l.delivery_id,l.amount_cents,l.note,l.created_at,d.pickup,d.dropoff FROM ledger l LEFT JOIN deliveries d ON d.id=l.delivery_id WHERE l.user_id=? AND l.kind IN ('driver_earning','shopping_reimbursement') ORDER BY l.id DESC LIMIT 100",(u['id'],)).fetchall()]
    return {'payout_balance_cents':int(p['payout_balance_cents'] if p else 0),'completed':int(p['completed'] if p else 0),'history':rows}
