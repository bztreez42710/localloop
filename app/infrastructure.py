from __future__ import annotations
import os
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse
from .main import app, require_user, page
from .payments import configured as payments_configured, FINIX_ENV
from .storage import configured as storage_configured

@app.get('/admin/infrastructure',response_class=HTMLResponse)
def infrastructure_status(request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    database_url=os.environ.get('DATABASE_URL','').strip()
    checks={
        'database':bool(database_url.startswith('postgres://') or database_url.startswith('postgresql://')),
        'finix':payments_configured(),
        'finix_live':payments_configured() and FINIX_ENV in {'live','prod','production'},
        'email':bool(os.environ.get('RESEND_API_KEY','').strip() and os.environ.get('RESEND_FROM_EMAIL','').strip()),
        'storage':storage_configured(),
        'identity':bool(os.environ.get('PERSONA_API_KEY','').strip() and os.environ.get('PERSONA_TEMPLATE_ID','').strip()),
        'background':bool(os.environ.get('CHECKR_API_KEY','').strip() and os.environ.get('CHECKR_PACKAGE','').strip()),
        'secure_cookie':os.environ.get('LOCALLOOP_COOKIE_SECURE','0')=='1',
    }
    return page(request,'infrastructure.html',checks=checks,finix_env=FINIX_ENV)
