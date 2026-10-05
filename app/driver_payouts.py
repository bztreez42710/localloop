from __future__ import annotations
import uuid
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from .main import app, now
from .database import db
from .mobile_api import _token_user
from .payments import stripe, configured

def _col(con, table:str, definition:str):
    try: con.execute(f'ALTER TABLE {table} ADD COLUMN {definition}')
    except Exception: pass


@app.on_event('startup')
def driver_payouts_startup():
    with db() as con:
        _col(con,'driver_compliance',"stripe_account_id TEXT DEFAULT ''")
        con.execute('''CREATE TABLE IF NOT EXISTS driver_payout_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            amount_cents INTEGER NOT NULL,
            stripe_transfer_id TEXT DEFAULT '',
            status TEXT DEFAULT 'created',
            idempotency_key TEXT DEFAULT '',
            error TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT DEFAULT ''
        )''')
        _col(con,'driver_payout_requests',"idempotency_key TEXT DEFAULT ''")
        _col(con,'driver_payout_requests',"error TEXT DEFAULT ''")
        _col(con,'driver_payout_requests',"updated_at TEXT DEFAULT ''")
        con.execute('CREATE INDEX IF NOT EXISTS idx_driver_payout_status ON driver_payout_requests(driver_id,status,id)')


def _driver_account(con, uid:int):
    row=con.execute('SELECT stripe_account_id FROM driver_compliance WHERE user_id=?',(uid,)).fetchone()
    return (row['stripe_account_id'] if row else '') or ''


def _stripe_account_status(account_id:str):
    if not account_id:
        return {'connected':False,'details_submitted':False,'payouts_enabled':False,'transfers_active':False}
    acct=stripe('GET',f'/accounts/{account_id}')
    caps=acct.get('capabilities') or {}
    return {
        'connected':True,
        'details_submitted':bool(acct.get('details_submitted')),
        'payouts_enabled':bool(acct.get('payouts_enabled')),
        'transfers_active':caps.get('transfers')=='active',
    }


@app.get('/api/mobile/payout')
def mobile_payout_status(request:Request):
    u,_=_token_user(request)
    if not configured(): raise HTTPException(503,'Stripe is not connected.')
    with db() as con:
        p=con.execute('SELECT payout_balance_cents FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        account_id=_driver_account(con,u['id'])
    status=_stripe_account_status(account_id) if account_id else {'connected':False,'details_submitted':False,'payouts_enabled':False,'transfers_active':False}
    return {'available_cents':int(p['payout_balance_cents'] if p else 0),**status}


@app.post('/api/mobile/payout/onboard')
def mobile_payout_onboard(request:Request):
    u,_=_token_user(request)
    if not configured(): raise HTTPException(503,'Stripe is not connected.')
    with db() as con:
        con.execute('INSERT OR IGNORE INTO driver_compliance(user_id,updated_at) VALUES(?,?)',(u['id'],now()))
        account_id=_driver_account(con,u['id'])
        if not account_id:
            acct=stripe('POST','/accounts',data={
                'type':'express','country':'US','email':u['email'],
                'capabilities[transfers][requested]':'true',
                'metadata[localloop_driver_id]':str(u['id']),
            },headers={'Idempotency-Key':f'localloop-driver-{u["id"]}'})
            account_id=acct.get('id','')
            if not account_id: raise HTTPException(502,'Stripe did not create a payout account.')
            con.execute('UPDATE driver_compliance SET stripe_account_id=?,updated_at=? WHERE user_id=?',(account_id,now(),u['id']))
    base=str(request.base_url).rstrip('/')
    link=stripe('POST','/account_links',data={
        'account':account_id,
        'refresh_url':f'{base}/driver/payout/return?retry=1',
        'return_url':f'{base}/driver/payout/return',
        'type':'account_onboarding',
    })
    return {'url':link.get('url','')}


@app.post('/api/mobile/payout/request')
def mobile_payout_request(request:Request,amount_cents:int=Form(0)):
    u,_=_token_user(request)
    if not configured(): raise HTTPException(503,'Stripe is not connected.')

    # Reserve the balance before talking to Stripe. This prevents two fast cash-out
    # requests from both transferring the same available balance.
    with db() as con:
        p=con.execute('SELECT payout_balance_cents FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        available=int(p['payout_balance_cents'] if p else 0)
        amount=available if int(amount_cents or 0)<=0 else int(amount_cents)
        if amount<100: raise HTTPException(400,'At least $1.00 is required to cash out.')
        if amount>available: raise HTTPException(400,'Cash-out amount exceeds available earnings.')
        account_id=_driver_account(con,u['id'])
        if not account_id: raise HTTPException(409,'Set up driver payouts first.')

    acct=_stripe_account_status(account_id)
    if not acct['details_submitted'] or not acct['transfers_active']:
        raise HTTPException(409,'Finish Stripe payout setup before cashing out.')

    key=f'll-payout-{u["id"]}-{uuid.uuid4()}'
    with db() as con:
        cur=con.execute(
            'UPDATE driver_profiles SET payout_balance_cents=payout_balance_cents-? WHERE user_id=? AND payout_balance_cents>=?',
            (amount,u['id'],amount)
        )
        if cur.rowcount!=1: raise HTTPException(409,'Available earnings changed. Refresh and try again.')
        pr=con.execute(
            'INSERT INTO driver_payout_requests(driver_id,amount_cents,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?)',
            (u['id'],amount,'processing',key,now(),now())
        )
        payout_id=pr.lastrowid

    try:
        tr=stripe('POST','/transfers',data={
            'amount':str(amount),'currency':'usd','destination':account_id,
            'metadata[localloop_driver_id]':str(u['id']),
            'metadata[localloop_payout_id]':str(payout_id),
        },headers={'Idempotency-Key':key})
        tid=tr.get('id','')
        if not tid: raise HTTPException(502,'Stripe did not confirm the transfer.')
    except Exception as exc:
        # Release only a still-processing reservation. This makes retry/recovery safe.
        with db() as con:
            row=con.execute('SELECT status FROM driver_payout_requests WHERE id=? AND driver_id=?',(payout_id,u['id'])).fetchone()
            if row and row['status']=='processing':
                con.execute('UPDATE driver_profiles SET payout_balance_cents=payout_balance_cents+? WHERE user_id=?',(amount,u['id']))
                con.execute('UPDATE driver_payout_requests SET status=?,error=?,updated_at=? WHERE id=?',('failed',str(exc)[:500],now(),payout_id))
        raise

    with db() as con:
        con.execute(
            'UPDATE driver_payout_requests SET stripe_transfer_id=?,status=?,error=?,updated_at=? WHERE id=? AND driver_id=?',
            (tid,'sent','',now(),payout_id,u['id'])
        )
        con.execute(
            'INSERT INTO ledger(user_id,delivery_id,kind,amount_cents,note,created_at) VALUES(?,NULL,?,?,?,?)',
            (u['id'],'driver_payout',-amount,f'Driver cash out to Stripe · payout #{payout_id}',now())
        )
    return {'ok':True,'amount_cents':amount,'transfer_id':tid,'payout_id':payout_id}


@app.get('/driver/payout/return',response_class=HTMLResponse)
def payout_return(retry:int=0):
    msg='Stripe payout setup needs another step. Return to the LocalLoop Driver app and tap Set up payouts again.' if retry else 'Stripe payout setup is complete. You can return to the LocalLoop Driver app and refresh payout status.'
    return HTMLResponse(f'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><body style="font-family:system-ui;background:#07111f;color:#fff;padding:32px;max-width:640px;margin:auto"><h1>LocalLoop Driver Payouts</h1><p>{msg}</p></body>')
