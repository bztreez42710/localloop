from __future__ import annotations
import os
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse
from .main import app, require_user, page
from .payments import configured as payments_configured, STRIPE_SECRET_KEY
from .storage import configured as storage_configured

RENDER_SERVICE_DASHBOARD='https://dashboard.render.com/web/srv-dak31mh5efls73fsc6t0'


def _stripe_live():
    return payments_configured() and STRIPE_SECRET_KEY.startswith('sk_live_')


def _checks():
    database_url=os.environ.get('DATABASE_URL','').strip()
    return {
        'database':bool(database_url.startswith('postgres://') or database_url.startswith('postgresql://')),
        'stripe':payments_configured(),
        'stripe_live':_stripe_live(),
        'email':bool(os.environ.get('RESEND_API_KEY','').strip() and os.environ.get('RESEND_FROM_EMAIL','').strip()),
        'storage':storage_configured(),
        'identity':bool(os.environ.get('PERSONA_API_KEY','').strip() and os.environ.get('PERSONA_TEMPLATE_ID','').strip()),
        'background':bool(os.environ.get('CHECKR_API_KEY','').strip() and os.environ.get('CHECKR_PACKAGE','').strip()),
        'secure_cookie':os.environ.get('LOCALLOOP_COOKIE_SECURE','0')=='1',
    }


def _steps(checks):
    return [
        {
            'key':'database','title':'1. Connect external PostgreSQL','ready':checks['database'],
            'why':'Keeps LocalLoop data durable and portable.',
            'provider_url':'https://supabase.com/dashboard/projects','provider_label':'Open Supabase',
            'instructions':['Create or open the LocalLoop Supabase project.','Copy a PostgreSQL connection string from Project Settings → Database.','Set DATABASE_URL in the LocalLoop Render service and save.'],
            'env':['DATABASE_URL=<PostgreSQL connection string>'],
            'secret_note':'Treat DATABASE_URL as a password and keep it out of chat and GitHub.'
        },
        {
            'key':'stripe_live','title':'2. Turn on Stripe payments','ready':checks['stripe_live'],
            'why':'Enables LocalLoop Pay checkout and processor-backed refunds.',
            'provider_url':'https://dashboard.stripe.com/','provider_label':'Open Stripe Dashboard',
            'instructions':['Use STRIPE_SECRET_KEY with an sk_test_ key while testing.','When Stripe activates live payments for your account, replace it with the sk_live_ secret key.','Create a webhook endpoint for /api/payments/stripe/webhook and add its signing secret to Render.'],
            'env':['STRIPE_SECRET_KEY=<Stripe secret key>','STRIPE_WEBHOOK_SECRET=<Stripe webhook signing secret>'],
            'secret_note':'Keep Stripe secret keys and webhook signing secrets server-side only.'
        },
        {
            'key':'background','title':'3. Credential Checkr','ready':checks['background'],
            'why':'Allows hosted driver background screening without LocalLoop collecting SSNs itself.',
            'provider_url':'https://dashboard.checkr.com/','provider_label':'Open Checkr Dashboard',
            'instructions':['Finish Checkr credentialing.','Create/copy the production Secret API key.','Choose the Checkr package slug and add both values to Render.'],
            'env':['CHECKR_API_KEY=<production secret key>','CHECKR_PACKAGE=<package slug>'],
            'secret_note':'The Checkr secret key can create screening requests. Keep it server-side only.'
        },
        {
            'key':'identity','title':'4. Credential Persona','ready':checks['identity'],
            'why':'Lets drivers complete identity verification in Persona instead of uploading identity documents to LocalLoop.',
            'provider_url':'https://app.withpersona.com/','provider_label':'Open Persona Dashboard',
            'instructions':['Finish Persona organization verification.','Create an Inquiry Template for driver identity verification.','Add the API key and template ID to Render.'],
            'env':['PERSONA_API_KEY=<production API key>','PERSONA_TEMPLATE_ID=<inquiry template id>'],
            'secret_note':'Keep the Persona API key private.'
        },
        {
            'key':'email','title':'5. Verify email and connect Resend','ready':checks['email'],
            'why':'Makes password-reset and account emails actually deliver to users.',
            'provider_url':'https://resend.com/domains','provider_label':'Open Resend Domains',
            'instructions':['Verify a sending domain in Resend.','Create a Sending-access API key.','Add the API key and From address to Render.'],
            'env':['RESEND_API_KEY=<Resend API key>','RESEND_FROM_EMAIL=LocalLoop <no-reply@your-verified-domain.com>'],
            'secret_note':'Keep the Resend API key private.'
        },
        {
            'key':'storage','title':'6. Connect private S3-compatible storage','ready':checks['storage'],
            'why':'Moves proof photos and receipts into private object storage.',
            'provider_url':'https://dash.cloudflare.com/?to=/:account/r2','provider_label':'Open Cloudflare R2',
            'instructions':['Create a private R2 bucket.','Create a bucket-scoped Object Read & Write API token.','Add the endpoint, bucket, access key and secret key to Render.','After testing uploads, set OBJECT_STORAGE_REQUIRED=1.'],
            'env':['OBJECT_STORAGE_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com','OBJECT_STORAGE_BUCKET=<bucket name>','OBJECT_STORAGE_ACCESS_KEY=<access key id>','OBJECT_STORAGE_SECRET_KEY=<secret access key>','OBJECT_STORAGE_REGION=auto','OBJECT_STORAGE_REQUIRED=1'],
            'secret_note':'Keep the object-storage secret key private and the bucket non-public.'
        },
    ]


@app.get('/admin/infrastructure',response_class=HTMLResponse)
def infrastructure_status(request:Request):
    u=require_user(request)
    if u['role']!='admin': raise HTTPException(403)
    checks=_checks()
    ready_count=sum(1 for key in ['database','stripe_live','email','storage','identity','background','secure_cookie'] if checks[key])
    stripe_env='live' if _stripe_live() else ('test' if payments_configured() else 'not connected')
    return page(request,'infrastructure.html',checks=checks,steps=_steps(checks),ready_count=ready_count,total_count=7,stripe_env=stripe_env,render_service_url=RENDER_SERVICE_DASHBOARD)
