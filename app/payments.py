from __future__ import annotations
import os, uuid
import httpx
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from .main import app, db, now, page, require_user

FINIX_USERNAME=os.environ.get('FINIX_USERNAME','').strip()
FINIX_PASSWORD=os.environ.get('FINIX_PASSWORD','').strip()
FINIX_APPLICATION_ID=os.environ.get('FINIX_APPLICATION_ID','').strip()
FINIX_MERCHANT_ID=os.environ.get('FINIX_MERCHANT_ID','').strip()
FINIX_ENV=os.environ.get('FINIX_ENV','sandbox').strip().lower()
FINIX_BASE='https://finix.live-payments-api.com' if FINIX_ENV in {'prod','live','production'} else 'https://finix.sandbox-payments-api.com'
FINIX_JS_ENV='prod' if FINIX_ENV in {'prod','live','production'} else 'sandbox'

def configured():
    return bool(FINIX_USERNAME and FINIX_PASSWORD and FINIX_APPLICATION_ID and FINIX_MERCHANT_ID)

def finix(method:str,path:str,**kwargs):
    if not configured():
        raise HTTPException(503,'LocalLoop Pay is not connected to its payment processor yet.')
    headers=kwargs.pop('headers',{})
    headers.update({'Content-Type':'application/json','Finix-Version':'2022-02-01'})
    r=httpx.request(method,FINIX_BASE+path,auth=(FINIX_USERNAME,FINIX_PASSWORD),headers=headers,timeout=30,**kwargs)
    if r.status_code>=400:
        raise HTTPException(502,f'Payment processor request failed ({r.status_code}).')
    return r.json() if r.content else {}

@app.on_event('startup')
def payments_startup():
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS owner_payout_settings(
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            legal_name TEXT DEFAULT '',business_name TEXT DEFAULT '',contact_email TEXT DEFAULT '',
            payout_method TEXT DEFAULT 'bank',bank_name TEXT DEFAULT '',bank_last4 TEXT DEFAULT '',
            status TEXT DEFAULT 'not_connected',updated_at TEXT DEFAULT '')''')
        for table,definition in [
            ('shopping_orders',"payment_provider_ref TEXT DEFAULT ''"),
            ('driver_compliance',"payout_email TEXT DEFAULT ''")
        ]:
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
    return page(request,'payment_portal.html',order=o,amount_cents=amount,processor_ready=configured(),finix_env=FINIX_JS_ENV,finix_application_id=FINIX_APPLICATION_ID)

@app.post('/api/payments/shop/{oid}')
async def localpay_process_shop(oid:int,request:Request):
    u=require_user(request)
    body=await request.json()
    token=(body.get('token') or '').strip()
    if not token: raise HTTPException(400,'Missing secure payment token.')
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=? AND customer_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        if o['status']!='awaiting_payment': raise HTTPException(409,'This order is not awaiting payment.')
        amount=o['estimated_goods_cents']+o['driver_pay_cents']+o['platform_fee_cents']
    identity=finix('POST','/identities',json={'entity':{}})
    iid=identity.get('id')
    if not iid: raise HTTPException(502,'Could not create buyer record.')
    instrument=finix('POST','/payment_instruments',json={'identity':iid,'token':token,'type':'TOKEN'})
    pid=instrument.get('id')
    if not pid: raise HTTPException(502,'Could not create secure payment instrument.')
    transfer=finix('POST','/transfers',json={
        'amount':amount,'currency':'USD','merchant':FINIX_MERCHANT_ID,'source':pid,
        'idempotency_id':str(uuid.uuid4()),'tags':{'localloop_order':str(oid),'type':'shopping_funding'}
    })
    ref=transfer.get('id','')
    state=(transfer.get('state') or transfer.get('status') or '').upper()
    funded=state in {'SUCCEEDED','COMPLETED'}
    pending=state in {'PENDING','PROCESSING'}
    payment_status='funded' if funded else ('pending' if pending else 'failed')
    with db() as con:
        con.execute('UPDATE shopping_orders SET payment_provider_ref=?,payment_status=?,status=?,updated_at=? WHERE id=?',(ref,payment_status,'posted' if funded else 'awaiting_payment',now(),oid))
        con.execute('INSERT INTO payment_records(user_id,shopping_order_id,provider,provider_ref,amount_cents,status,kind,created_at) VALUES(?,?,?,?,?,?,?,?)',(u['id'],oid,'finix',ref,amount,payment_status,'customer_funding',now()))
    return JSONResponse({'ok':funded or pending,'status':payment_status,'reference':ref})

@app.post('/pay/shop/{oid}/refresh')
def localpay_refresh_shop(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=? AND customer_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        ref=o['payment_provider_ref']
    if not ref: raise HTTPException(400,'No payment is pending for this order.')
    transfer=finix('GET',f'/transfers/{ref}')
    state=(transfer.get('state') or transfer.get('status') or '').upper()
    funded=state in {'SUCCEEDED','COMPLETED'}
    failed=state in {'FAILED','CANCELED','CANCELLED','DECLINED'}
    status='funded' if funded else ('failed' if failed else 'pending')
    with db() as con:
        con.execute('UPDATE shopping_orders SET payment_status=?,status=?,updated_at=? WHERE id=?',(status,'posted' if funded else 'awaiting_payment',now(),oid))
        con.execute('UPDATE payment_records SET status=? WHERE shopping_order_id=? AND provider_ref=?',(status,oid,ref))
    return RedirectResponse('/shop',303)

@app.get('/admin/payout-settings',response_class=HTMLResponse)
def payout_settings_page(request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    with db() as con: row=con.execute('SELECT * FROM owner_payout_settings WHERE user_id=?',(u['id'],)).fetchone()
    return page(request,'payout_settings.html',settings=row,processor_ready=configured(),processor_env=FINIX_JS_ENV)

@app.post('/admin/payout-settings')
def payout_settings_save(request:Request,legal_name:str=Form(...),business_name:str=Form(''),contact_email:str=Form(...),payout_method:str=Form('bank'),bank_name:str=Form(''),bank_last4:str=Form('')):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    if '@' not in contact_email: raise HTTPException(400,'Enter a valid contact email.')
    if payout_method not in {'bank','debit'}: raise HTTPException(400,'Choose bank direct deposit or eligible debit card.')
    last4=''.join(ch for ch in bank_last4 if ch.isdigit())[-4:]
    with db() as con:
        con.execute('''INSERT OR REPLACE INTO owner_payout_settings(user_id,legal_name,business_name,contact_email,payout_method,bank_name,bank_last4,status,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?)''',(u['id'],legal_name.strip(),business_name.strip(),contact_email.strip().lower(),payout_method,bank_name.strip(),last4,'processor_ready' if configured() else 'awaiting_processor_onboarding',now()))
    return RedirectResponse('/admin/payout-settings',303)

@app.get('/payments/status')
def payment_status(request:Request):
    require_user(request)
    return JSONResponse({'portal':'LocalLoop Pay','processor':'finix','configured':configured(),'environment':FINIX_JS_ENV,'checks_accepted':False})
