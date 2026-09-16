from __future__ import annotations
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from .main import app, db, page, require_user

@app.get('/driver/app', response_class=HTMLResponse)
def driver_app(request: Request):
    u=require_user(request)
    if u['role']!='driver': raise HTTPException(403)
    with db() as con:
        profile=con.execute('SELECT * FROM driver_profiles WHERE user_id=?',(u['id'],)).fetchone()
        available=con.execute("SELECT d.*,u.name customer FROM deliveries d JOIN users u ON u.id=d.customer_id WHERE d.status='posted' ORDER BY d.id DESC LIMIT 40").fetchall()
        mine=con.execute("SELECT * FROM deliveries WHERE driver_id=? AND status IN ('accepted','picked_up') ORDER BY id DESC",(u['id'],)).fetchall()
    return page(request,'driver_app.html',profile=profile,available=available,mine=mine)

@app.get('/driver/manifest.webmanifest')
def driver_manifest():
    return JSONResponse({
        'name':'LocalLoop Driver','short_name':'LocalLoop','description':'LocalLoop independent driver delivery app',
        'start_url':'/driver/app','scope':'/','display':'standalone','background_color':'#07111f','theme_color':'#6d5dfc',
        'icons':[]
    }, media_type='application/manifest+json')

@app.get('/driver/sw.js')
def driver_service_worker():
    js="""const C='localloop-driver-v1';self.addEventListener('install',e=>{e.waitUntil(caches.open(C).then(c=>c.addAll(['/driver/app','/static/style.css','/static/portal.css','/static/enhancements.css'])));self.skipWaiting()});self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));self.addEventListener('fetch',e=>{if(e.request.method!=='GET')return;e.respondWith(fetch(e.request).then(r=>{const x=r.clone();caches.open(C).then(c=>c.put(e.request,x));return r}).catch(()=>caches.match(e.request).then(r=>r||caches.match('/driver/app'))))});"""
    return Response(js,media_type='application/javascript',headers={'Service-Worker-Allowed':'/'})
