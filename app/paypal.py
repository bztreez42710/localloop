from __future__ import annotations
import os, uuid
import httpx
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from .main import app, db, now, page, require_user

CLIENT_ID=os.environ.get('PAYPAL_CLIENT_ID','').strip()
CLIENT_SECRET=os.environ.get('PAYPAL_CLIENT_SECRET','').strip()
MODE=os.environ.get('PAYPAL_MODE','sandbox').strip().lower()
PAYOUTS_ENABLED=os.environ.get('PAYPAL_PAYOUTS_ENABLED','0')=='1'
BASE='https://api-m.paypal.com' if MODE=='live' else 'https://api-m.sandbox.paypal.com'
PUBLIC_URL=os.environ.get('LOCALLOOP_PUBLIC_URL','https://localloop-app.onrender.com').rstrip('/')

def configured(): return bool(CLIENT_ID and CLIENT_SECRET)

def token():
    if not configured(): raise HTTPException(503,'PayPal is not connected to LocalLoop yet.')
    r=httpx.post(BASE+'/v1/oauth2/token',auth=(CLIENT_ID,CLIENT_SECRET),data={'grant_type':'client_credentials'},timeout=20)
    if r.status_code>=400: raise HTTPException(502,'PayPal authentication failed.')
    return r.json()['access_token']

def pp(method,path,**kwargs):
    headers=kwargs.pop('headers',{})
    headers.update({'Authorization':'Bearer '+token(),'Content-Type':'application/json','PayPal-Request-Id':str(uuid.uuid4())})
    r=httpx.request(method,BASE+path,headers=headers,timeout=25,**kwargs)
    if r.status_code>=400: raise HTTPException(502,f'PayPal request failed ({r.status_code}).')
    return r.json() if r.content else {}

@app.on_event('startup')
def paypal_startup():
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS owner_payout_settings(
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            legal_name TEXT DEFAULT '',business_name TEXT DEFAULT '',paypal_email TEXT DEFAULT '',
            payout_method TEXT DEFAULT 'bank',bank_name TEXT DEFAULT '',bank_last4 TEXT DEFAULT '',
            status TEXT DEFAULT 'not_connected',updated_at TEXT DEFAULT '')''')

@app.get('/admin/payout-settings',response_class=HTMLResponse)
def payout_settings_page(request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    with db() as con: row=con.execute('SELECT * FROM owner_payout_settings WHERE user_id=?',(u['id'],)).fetchone()
    return page(request,'payout_settings.html',settings=row,paypal_ready=configured(),paypal_mode=MODE,payouts_enabled=PAYOUTS_ENABLED)

@app.post('/admin/payout-settings')
def payout_settings_save(request:Request,legal_name:str=Form(...),business_name:str=Form(''),paypal_email:str=Form(...),payout_method:str=Form('bank'),bank_name:str=Form(''),bank_last4:str=Form('')):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    if '@' not in paypal_email: raise HTTPException(400,'Enter the email on your PayPal Business account.')
    if payout_method not in {'bank','paypal','debit'}: raise HTTPException(400,'Invalid payout method.')
    last4=''.join(ch for ch in bank_last4 if ch.isdigit())[-4:]
    with db() as con:
        con.execute('''INSERT OR REPLACE INTO owner_payout_settings(user_id,legal_name,business_name,paypal_email,payout_method,bank_name,bank_last4,status,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?)''',(u['id'],legal_name.strip(),business_name.strip(),paypal_email.strip().lower(),payout_method,bank_name.strip(),last4,'api_connected' if configured() else 'awaiting_paypal_connection',now()))
    return RedirectResponse('/admin/payout-settings',303)

@app.post('/shop/orders/{oid}/paypal/create')
def create_shop_paypal_order(oid:int,request:Request,estimated_goods_dollars:float=Form(...)):
    u=require_user(request)
    if estimated_goods_dollars<0 or estimated_goods_dollars>1500: raise HTTPException(400,'Estimated merchandise total must be between $0 and $1,500 for this Spokane pilot.')
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=? AND customer_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        if o['status'] not in {'awaiting_payment','posted'}: raise HTTPException(400,'This order can no longer be funded.')
        goods=round(estimated_goods_dollars*100); total=goods+o['driver_pay_cents']+o['platform_fee_cents']
        con.execute('UPDATE shopping_orders SET estimated_goods_cents=?,updated_at=? WHERE id=?',(goods,now(),oid))
    order=pp('POST','/v2/checkout/orders',json={'intent':'CAPTURE','purchase_units':[{'reference_id':f'shop-{oid}','custom_id':f'localloop-shop-{oid}','amount':{'currency_code':'USD','value':f'{total/100:.2f}'},'description':f'LocalLoop Spokane shopping order #{oid}'}],'payment_source':{'paypal':{'experience_context':{'return_url':f'{PUBLIC_URL}/shop/orders/{oid}/paypal/return','cancel_url':f'{PUBLIC_URL}/shop'}}}})
    approval=next((x['href'] for x in order.get('links',[]) if x.get('rel') in {'payer-action','approve'}),None)
    with db() as con:
        con.execute("UPDATE shopping_orders SET paypal_order_id=?,payment_status='approval_pending',updated_at=? WHERE id=?",(order.get('id',''),now(),oid))
        con.execute("INSERT INTO payment_records(user_id,shopping_order_id,provider,provider_ref,amount_cents,status,kind,created_at) VALUES(?,?,?,?,?,?,?,?)",(u['id'],oid,'paypal',order.get('id',''),total,'approval_pending','customer_order',now()))
    if not approval: raise HTTPException(502,'PayPal did not return an approval link.')
    return RedirectResponse(approval,303)

@app.get('/shop/orders/{oid}/paypal/return')
def paypal_return(oid:int,request:Request,token:str=''):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=? AND customer_id=?',(oid,u['id'])).fetchone()
        if not o: raise HTTPException(404)
        order_id=o['paypal_order_id'] or token
    if not order_id: raise HTTPException(400,'Missing PayPal order.')
    result=pp('POST',f'/v2/checkout/orders/{order_id}/capture',json={}); status=result.get('status','UNKNOWN')
    capture_id=''
    try: capture_id=result['purchase_units'][0]['payments']['captures'][0]['id']
    except Exception: pass
    with db() as con:
        funded=status=='COMPLETED'
        con.execute('UPDATE shopping_orders SET payment_status=?,status=?,updated_at=? WHERE id=?',('funded' if funded else status.lower(),'posted' if funded else 'awaiting_payment',now(),oid))
        con.execute('UPDATE payment_records SET status=? WHERE shopping_order_id=? AND provider_ref=?',('completed' if funded else status.lower(),oid,order_id))
        if capture_id:
            amount=o['estimated_goods_cents']+o['driver_pay_cents']+o['platform_fee_cents']
            con.execute("INSERT INTO payment_records(user_id,shopping_order_id,provider,provider_ref,amount_cents,status,kind,created_at) VALUES(?,?,?,?,?,?,?,?)",(u['id'],oid,'paypal',capture_id,amount,'completed','customer_capture',now()))
    return RedirectResponse('/shop',303)

def settle_shopping_order(oid:int):
    if not configured(): return {'status':'paypal_not_connected'}
    with db() as con:
        o=con.execute('SELECT * FROM shopping_orders WHERE id=?',(oid,)).fetchone()
        if not o or o['status']!='delivered': return {'status':'not_ready'}
        d=con.execute('SELECT c.paypal_email,u.name FROM driver_compliance c JOIN users u ON u.id=c.user_id WHERE c.user_id=?',(o['driver_id'],)).fetchone()
        capture=con.execute("SELECT provider_ref FROM payment_records WHERE shopping_order_id=? AND kind='customer_capture' AND status='completed' ORDER BY id DESC LIMIT 1",(oid,)).fetchone()
        refunded=con.execute("SELECT 1 FROM payment_records WHERE shopping_order_id=? AND kind='customer_refund' AND status IN ('completed','submitted')",(oid,)).fetchone()
        paid=con.execute("SELECT 1 FROM payment_records WHERE shopping_order_id=? AND kind='shopper_payout' AND status IN ('submitted','completed')",(oid,)).fetchone()
    result={'status':'settling'}
    refund=max(0,o['estimated_goods_cents']-o['actual_goods_cents'])
    if refund and capture and not refunded:
        rr=pp('POST',f"/v2/payments/captures/{capture['provider_ref']}/refund",json={'amount':{'value':f'{refund/100:.2f}','currency_code':'USD'},'note_to_payer':f'Unused merchandise budget for LocalLoop shopping order #{oid}'})
        with db() as con: con.execute("INSERT INTO payment_records(user_id,shopping_order_id,provider,provider_ref,amount_cents,status,kind,created_at) VALUES(?,?,?,?,?,?,?,?)",(o['customer_id'],oid,'paypal',rr.get('id',''),refund,'completed' if rr.get('status')=='COMPLETED' else rr.get('status','submitted').lower(),'customer_refund',now()))
        result['refund_cents']=refund
    payout=o['actual_goods_cents']+o['driver_pay_cents']
    if payout and not paid:
        if PAYOUTS_ENABLED and d and d['paypal_email']:
            pr=pp('POST','/v1/payments/payouts',json={'sender_batch_header':{'sender_batch_id':f'localloop-{oid}-{uuid.uuid4().hex[:8]}','email_subject':'Your LocalLoop payout'},'items':[{'recipient_type':'EMAIL','amount':{'value':f'{payout/100:.2f}','currency':'USD'},'receiver':d['paypal_email'],'note':f'Shopping order #{oid}: reimbursement and driver pay','sender_item_id':f'shop-{oid}'}]})
            batch=(pr.get('batch_header') or {}).get('payout_batch_id','')
            with db() as con: con.execute("UPDATE payment_records SET provider_ref=?,status='submitted' WHERE shopping_order_id=? AND kind='shopper_payout' AND status='pending'",(batch,oid))
            result['payout']='submitted'
        else:
            result['payout']='pending_paypal_payouts_activation'
    result['platform_fee_cents']=o['platform_fee_cents']
    return result

@app.post('/admin/shop/orders/{oid}/settle')
def admin_settle_shop(oid:int,request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    settle_shopping_order(oid)
    return RedirectResponse('/shopping/ops',303)
