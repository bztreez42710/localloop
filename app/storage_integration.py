from __future__ import annotations
from fastapi import Request, HTTPException
from fastapi.responses import Response
from .main import app, require_user
from .database import db
from .storage import store_data_uri, get_bytes, configured
from . import production, community_plus


def _safe_image(data:str,max_bytes:int=1_750_000)->str:
    try: return store_data_uri(data,'proofs',max_bytes)
    except ValueError as e: raise HTTPException(400,str(e))
    except RuntimeError as e: raise HTTPException(503,str(e))

production._safe_image=_safe_image
community_plus._safe_image=_safe_image

@app.get('/media/delivery/{did}')
def delivery_proof_media(did:int,request:Request):
    u=require_user(request)
    with db() as con:
        d=con.execute('SELECT customer_id,driver_id,proof_photo FROM deliveries WHERE id=?',(did,)).fetchone()
        if not d or (u['role']!='admin' and u['id'] not in {d['customer_id'],d['driver_id']}): raise HTTPException(404)
        ref=d['proof_photo'] or ''
    if not ref: raise HTTPException(404)
    if ref.startswith('data:'):
        import base64
        header,payload=ref.split(',',1); mime=header.split(';',1)[0].split(':',1)[1]
        return Response(base64.b64decode(payload),media_type=mime,headers={'Cache-Control':'private, max-age=300'})
    data,mime=get_bytes(ref)
    return Response(data,media_type=mime,headers={'Cache-Control':'private, max-age=300'})

@app.get('/media/shopping/{oid}')
def shopping_receipt_media(oid:int,request:Request):
    u=require_user(request)
    with db() as con:
        o=con.execute('SELECT customer_id,driver_id,receipt_photo FROM shopping_orders WHERE id=?',(oid,)).fetchone()
        if not o or (u['role']!='admin' and u['id'] not in {o['customer_id'],o['driver_id']}): raise HTTPException(404)
        ref=o['receipt_photo'] or ''
    if not ref: raise HTTPException(404)
    if ref.startswith('data:'):
        import base64
        header,payload=ref.split(',',1); mime=header.split(';',1)[0].split(':',1)[1]
        return Response(base64.b64decode(payload),media_type=mime,headers={'Cache-Control':'private, max-age=300'})
    data,mime=get_bytes(ref)
    return Response(data,media_type=mime,headers={'Cache-Control':'private, max-age=300'})

@app.get('/admin/storage-status')
def storage_status(request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    return {'object_storage_configured':configured(),'private_media_routes':True}
