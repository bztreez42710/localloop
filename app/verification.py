from __future__ import annotations
import os
import httpx
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from .main import app, require_user, now, page
from .database import db

PERSONA_API_KEY=os.environ.get('PERSONA_API_KEY','').strip()
PERSONA_TEMPLATE_ID=os.environ.get('PERSONA_TEMPLATE_ID','').strip()
CHECKR_API_KEY=os.environ.get('CHECKR_API_KEY','').strip()
CHECKR_PACKAGE=os.environ.get('CHECKR_PACKAGE','').strip()
PUBLIC_BASE_URL=os.environ.get('PUBLIC_BASE_URL','https://localloop-app.onrender.com').rstrip('/')


def _add_column(con,table,definition):
    try: con.execute(f'ALTER TABLE {table} ADD COLUMN {definition}')
    except Exception: pass

@app.on_event('startup')
def verification_startup():
    with db() as con:
        _add_column(con,'driver_compliance',"identity_provider_ref TEXT DEFAULT ''")
        _add_column(con,'driver_compliance',"background_provider_ref TEXT DEFAULT ''")
        _add_column(con,'driver_compliance',"background_report_ref TEXT DEFAULT ''")

@app.get('/driver/verify/status',response_class=HTMLResponse)
def verification_status_page(request:Request):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    with db() as con:
        c=con.execute('SELECT * FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone()
    return page(request,'verification_status.html',compliance=c,persona_ready=bool(PERSONA_API_KEY and PERSONA_TEMPLATE_ID),checkr_ready=bool(CHECKR_API_KEY and CHECKR_PACKAGE))

@app.post('/driver/verify/identity/start')
def start_identity(request:Request):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    if not (PERSONA_API_KEY and PERSONA_TEMPLATE_ID): raise HTTPException(503,'Identity verification provider onboarding is not connected yet.')
    payload={'data':{'attributes':{'inquiry-template-id':PERSONA_TEMPLATE_ID,'reference-id':str(u['id'])}}}
    r=httpx.post('https://api.withpersona.com/api/v1/inquiries',headers={'Authorization':f'Bearer {PERSONA_API_KEY}','Content-Type':'application/json'},json=payload,timeout=30)
    if r.status_code>=300: raise HTTPException(502,'Identity provider could not start verification.')
    data=r.json(); inquiry=(data.get('data') or {}).get('id',''); link=(data.get('meta') or {}).get('one-time-link','')
    if not inquiry or not link: raise HTTPException(502,'Identity provider did not return a verification session.')
    sep='&' if '?' in link else '?'; link=f'{link}{sep}redirect-uri={PUBLIC_BASE_URL}/driver/verify/identity/return'
    with db() as con:
        con.execute('INSERT OR IGNORE INTO driver_compliance(user_id,updated_at) VALUES(?,?)',(u['id'],now()))
        con.execute("UPDATE driver_compliance SET identity_status='pending',identity_provider_ref=?,updated_at=? WHERE user_id=?",(inquiry,now(),u['id']))
    return RedirectResponse(link,303)

@app.get('/driver/verify/identity/return')
def identity_return(request:Request,inquiry_id:str='',status:str=''):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    with db() as con:
        c=con.execute('SELECT * FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone(); expected=c['identity_provider_ref'] if c else ''
    inquiry_id=inquiry_id or request.query_params.get('inquiry-id','') or expected
    if not inquiry_id or inquiry_id!=expected: raise HTTPException(400,'Verification session does not match this account.')
    if not PERSONA_API_KEY: raise HTTPException(503,'Identity provider is not connected.')
    r=httpx.get(f'https://api.withpersona.com/api/v1/inquiries/{inquiry_id}',headers={'Authorization':f'Bearer {PERSONA_API_KEY}'},timeout=30)
    if r.status_code>=300: raise HTTPException(502,'Could not confirm identity verification status.')
    pstatus=((r.json().get('data') or {}).get('attributes') or {}).get('status','').lower()
    local='approved' if pstatus in {'completed','approved'} else ('rejected' if pstatus in {'failed','declined','expired'} else 'pending')
    with db() as con: con.execute('UPDATE driver_compliance SET identity_status=?,updated_at=? WHERE user_id=?',(local,now(),u['id']))
    return RedirectResponse('/driver/verify/status',303)

@app.post('/driver/verify/background/start')
def start_background(request:Request):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    if not (CHECKR_API_KEY and CHECKR_PACKAGE): raise HTTPException(503,'Background screening provider credentialing is not connected yet.')
    with db() as con:
        c=con.execute('SELECT * FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone()
        if not c or not c['background_consent']: raise HTTPException(400,'Save your background-check consent first.')
    auth=(CHECKR_API_KEY,'')
    cr=httpx.post('https://api.checkr.com/v1/candidates',auth=auth,data={'email':u['email']},timeout=30)
    if cr.status_code>=300: raise HTTPException(502,'Could not create background screening candidate.')
    cid=cr.json().get('id')
    ir=httpx.post('https://api.checkr.com/v1/invitations',auth=auth,data=[('candidate_id',cid),('package',CHECKR_PACKAGE),('work_locations[][country]','US'),('work_locations[][state]','WA'),('work_locations[][city]','Spokane')],timeout=30)
    if ir.status_code>=300: raise HTTPException(502,'Could not create background screening invitation.')
    invitation=ir.json(); iid=invitation.get('id',''); url=invitation.get('invitation_url') or invitation.get('url') or ''
    with db() as con:
        con.execute("UPDATE driver_compliance SET background_status='pending',background_provider_ref=?,updated_at=? WHERE user_id=?",(iid,now(),u['id']))
    if url: return RedirectResponse(url,303)
    return RedirectResponse('/driver/verify/status',303)

@app.post('/driver/verify/background/refresh')
def refresh_background(request:Request):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    if not CHECKR_API_KEY: raise HTTPException(503,'Background screening provider is not connected.')
    with db() as con:
        c=con.execute('SELECT * FROM driver_compliance WHERE user_id=?',(u['id'],)).fetchone(); iid=c['background_provider_ref'] if c else ''
    if not iid: raise HTTPException(400,'No background screening invitation has been started.')
    r=httpx.get(f'https://api.checkr.com/v1/invitations/{iid}',auth=(CHECKR_API_KEY,''),timeout=30)
    if r.status_code>=300: raise HTTPException(502,'Could not refresh background screening status.')
    inv=r.json(); report_id=inv.get('report_id') or ''
    local='pending'
    if report_id:
        rr=httpx.get(f'https://api.checkr.com/v1/reports/{report_id}',auth=(CHECKR_API_KEY,''),timeout=30)
        if rr.status_code<300:
            report=rr.json(); rstatus=(report.get('status') or '').lower(); result=(report.get('result') or '').lower()
            if rstatus=='complete' and result=='clear': local='approved'
            elif rstatus=='complete' and result in {'consider','suspended'}: local='pending'
    with db() as con: con.execute('UPDATE driver_compliance SET background_status=?,background_report_ref=?,updated_at=? WHERE user_id=?',(local,report_id,now(),u['id']))
    return RedirectResponse('/driver/verify/status',303)
