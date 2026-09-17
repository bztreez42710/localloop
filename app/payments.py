from __future__ import annotations
import os, json, time, hmac, hashlib
import httpx
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from .main import app, db, now, page, require_user

STRIPE_SECRET_KEY=os.environ.get('STRIPE_SECRET_KEY','').strip()
STRIPE_WEBHOOK_SECRET=os.environ.get('STRIPE_WEBHOOK_SECRET','').strip()
STRIPE_BASE='https://api.stripe.com/v1'

def configured():
    return bool(STRIPE_SECRET_KEY)

def stripe(method:str,path:str,**kwargs):
    if not configured():
        raise HTTPException(503,'LocalLoop Pay is not connected to Stripe yet.')
    headers=kwargs.pop('headers',{})
    headers['Authorization']=f'Bearer {STRIPE_SECRET_KEY}'
    r=httpx.request(method,STRIPE_BASE+path,headers=headers,timeout=30,**kwargs)
    if r.status_code>=400:
        detail='Stripe request failed.'
        try: detail=r.json().get('error',{}).get('message') or detail
        except Exception: pass
        raise HTTPException(502,detail)
    return r.json() if r.content else {}

def _base_url(request:Request):
    return str(request.base_url).rstrip('/')

def _create_checkout(request:Request,amount:int,name:str,kind:str,oid:int):
    base=_base_url(request)
    success=f'{base}/pay/{"shop" if kind=="shopping" else "market"}/{oid}/stripe/success?session_id={{CHECKOUT_SESSION_ID}}'
    cancel=f'{base}/pay/{"shop" if kind=="shopping" else "market"}/{oid}'
    data={
        'mode':'payment',
        'success_url':success,
        'cancel_url':cancel,
        'line_items[0][price_data][currency]':'usd',
        'line_items[0][price_data][unit_amount]':str(amount),
        'line_items[0][price_data][product_data][name]':name,
        'line_items[0][quantity]':'1',
        'metadata[kind]':kind,
        'metadata[order_id]':str(oid),
    }
    return stripe('POST','/checkout/sessions',data=data)

def _session_paid(session:dict):
    return (session.get('payment_status') or '').lower()=='paid' or (session.get('status') or '').lower()=='complete'

@app.on_event('startup')
def payments_startup():
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS owner_payout_settings(
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            legal_name TEXT DEFAULT '',business_name TEXT DEFAULT '',contact_email TEXT DEFAULT '',
            payout_method TEXT DEFAULT 'bank',bank_name TEXT DEFAULT '',bank_last4 TEXT DEFAULT '',
            status TEXT DEFAULT 'not_connected',updated_at TEXT DEFAULT '')''')
        for table,definition in [('shopping_orders',"payment_provider_ref TEXT DEFAULT ''"),('driver_compliance',"payout_email TEXT DEFAULT ''")]:
            try: con.execute(f'ALTER TABLE {table} ADD COLUMN {definition}')
            except Exception: pass

@app.get('/pay/shop/{oid}',response_class=HTMLResponse)
def localpay_shop(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=? AND customer_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        if o['status']!='awaiting_payment': return RedirectResponse('/shop',303)
        amount=o['estimated_goods_cents']+o['driver_pay_cents']+o['platform_fee_cents']
    return page(request,'payment_portal.html',order=o,amount_cents=amount,processor_ready=configured())

@app.post('/pay/shop/{oid}/stripe/start')
def stripe_shop_start(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=? AND customer_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        if o['status']!='awaiting_payment': raise HTTPException(409,'This order is not awaiting payment.')
        amount=o['estimated_goods_cents']+o['driver_pay_cents']+o['platform_fee_cents']
    s=_create_checkout(request,amount,f'LocalLoop shopping order #{oid}','shopping',oid)
    with db() as con:
        con.execute("UPDATE shopping_orders SET payment_provider_ref=?,payment_status='pending',updated_at=? WHERE id=?",(s.get('id',''),now(),oid))
        con.execute('INSERT INTO payment_records(user_id,shopping_order_id,provider,provider_ref,amount_cents,status,kind,created_at) VALUES(?,?,?,?,?,?,?,?)',(u['id'],oid,'stripe',s.get('id',''),amount,'pending','customer_funding',now()))
    return RedirectResponse(s['url'],303)

@app.get('/pay/shop/{oid}/stripe/success')
def stripe_shop_success(oid:int,request:Request,session_id:str):
    u=require_user(request)
    s=stripe('GET',f'/checkout/sessions/{session_id}')
    if str((s.get('metadata') or {}).get('order_id'))!=str(oid) or (s.get('metadata') or {}).get('kind')!='shopping': raise HTTPException(400,'Payment session does not match this order.')
    paid=_session_paid(s)
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=? AND customer_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        con.execute('UPDATE shopping_orders SET payment_provider_ref=?,payment_status=?,status=?,updated_at=? WHERE id=?',(session_id,'funded' if paid else 'pending','posted' if paid else 'awaiting_payment',now(),oid))
        con.execute('UPDATE payment_records SET status=? WHERE shopping_order_id=? AND provider_ref=?',('funded' if paid else 'pending',oid,session_id))
    return RedirectResponse('/shop',303)

@app.get('/admin/payout-settings',response_class=HTMLResponse)
def payout_settings_page(request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    with db() as con: row=con.execute('SELECT * FROM owner_payout_settings WHERE user_id=?',(u['id'],)).fetchone()
    return page(request,'payout_settings.html',settings=row,processor_ready=configured(),processor_env='stripe')

@app.post('/admin/payout-settings')
def payout_settings_save(request:Request,legal_name:str=Form(...),business_name:str=Form(''),contact_email:str=Form(...),payout_method:str=Form('bank'),bank_name:str=Form(''),bank_last4:str=Form('')):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    if '@' not in contact_email: raise HTTPException(400,'Enter a valid contact email.')
    last4=''.join(ch for ch in bank_last4 if ch.isdigit())[-4:]
    with db() as con:
        con.execute('''INSERT OR REPLACE INTO owner_payout_settings(user_id,legal_name,business_name,contact_email,payout_method,bank_name,bank_last4,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?)''',(u['id'],legal_name.strip(),business_name.strip(),contact_email.strip().lower(),payout_method,bank_name.strip(),last4,'processor_ready' if configured() else 'awaiting_processor_onboarding',now()))
    return RedirectResponse('/admin/payout-settings',303)

@app.get('/payments/status')
def payment_status(request:Request):
    require_user(request)
    return JSONResponse({'portal':'LocalLoop Pay','processor':'stripe','configured':configured(),'environment':'live' if STRIPE_SECRET_KEY.startswith('sk_live_') else 'test','checks_accepted':False})

@app.post('/api/payments/stripe/webhook')
async def stripe_webhook(request:Request):
    if not STRIPE_WEBHOOK_SECRET: raise HTTPException(503,'Stripe webhook is not configured.')
    body=await request.body(); sig=request.headers.get('stripe-signature','')
    parts={}
    for item in sig.split(','):
        if '=' in item:
            k,v=item.split('=',1); parts.setdefault(k,[]).append(v)
    try: ts=int(parts.get('t',['0'])[0])
    except Exception: raise HTTPException(400,'Invalid Stripe signature.')
    if abs(time.time()-ts)>300: raise HTTPException(400,'Expired Stripe signature.')
    signed=f'{ts}.'.encode()+body
    expected=hmac.new(STRIPE_WEBHOOK_SECRET.encode(),signed,hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected,v) for v in parts.get('v1',[])): raise HTTPException(400,'Invalid Stripe signature.')
    event=json.loads(body.decode())
    if event.get('type')=='checkout.session.completed':
        s=(event.get('data') or {}).get('object') or {}; meta=s.get('metadata') or {}; oid=int(meta.get('order_id') or 0)
        if oid and _session_paid(s):
            with db() as con:
                if meta.get('kind')=='shopping':
                    con.execute("UPDATE shopping_orders SET payment_provider_ref=?,payment_status='funded',status='posted',updated_at=? WHERE id=? AND status='awaiting_payment'",(s.get('id',''),now(),oid))
                    con.execute("UPDATE payment_records SET status='funded' WHERE shopping_order_id=? AND provider_ref=?",(oid,s.get('id','')))
    return {'received':True}
