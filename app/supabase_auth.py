from __future__ import annotations
import os
from typing import Any
import httpx
from fastapi import HTTPException, Request

SUPABASE_URL=os.environ.get('SUPABASE_URL','').rstrip('/')
SUPABASE_PUBLISHABLE_KEY=os.environ.get('SUPABASE_PUBLISHABLE_KEY','')
AUTH_ENABLED=bool(SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY)

def _headers(token=None):
    h={'apikey':SUPABASE_PUBLISHABLE_KEY,'Content-Type':'application/json'}
    if token: h['Authorization']=f'Bearer {token}'
    return h

def _request(method,path,**kwargs)->dict[str,Any]:
    try: response=httpx.request(method,f'{SUPABASE_URL}{path}',headers=kwargs.pop('headers',_headers()),timeout=15,**kwargs)
    except httpx.HTTPError as exc: raise HTTPException(503,'Authentication service is temporarily unavailable.') from exc
    data=response.json() if response.content else {}
    if response.status_code>=400:
        raise HTTPException(400 if response.status_code<500 else 503,data.get('msg') or data.get('message') or data.get('error_description') or 'Authentication failed.')
    return data

def sign_up(email,password,name,role):
    return _request('POST','/auth/v1/signup',json={'email':email,'password':password,'data':{'name':name,'role':role}})

def sign_in(email,password):
    return _request('POST','/auth/v1/token?grant_type=password',json={'email':email,'password':password})

def refresh_session(refresh_token):
    return _request('POST','/auth/v1/token?grant_type=refresh_token',json={'refresh_token':refresh_token})

def verified_identity(request:Request):
    cached=getattr(request.state,'supabase_identity',Ellipsis)
    if cached is not Ellipsis: return cached
    token=request.cookies.get('ll_access_token')
    identity=None
    if token:
        try: identity=_request('GET','/auth/v1/user',headers=_headers(token))
        except HTTPException: pass
    if not identity and request.cookies.get('ll_refresh_token'):
        try:
            refreshed=refresh_session(request.cookies['ll_refresh_token'])
            request.state.supabase_refreshed=refreshed
            identity=refreshed.get('user')
        except HTTPException: pass
    request.state.supabase_identity=identity
    return identity

def set_auth_cookies(response,auth,secure):
    if auth.get('access_token'): response.set_cookie('ll_access_token',auth['access_token'],httponly=True,secure=secure,samesite='lax',max_age=int(auth.get('expires_in',3600)))
    if auth.get('refresh_token'): response.set_cookie('ll_refresh_token',auth['refresh_token'],httponly=True,secure=secure,samesite='strict',max_age=2592000)

def clear_auth_cookies(response):
    for name in ('ll_access_token','ll_refresh_token','ll_session'): response.delete_cookie(name)
