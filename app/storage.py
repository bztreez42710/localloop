from __future__ import annotations
import os, base64, uuid

OBJECT_STORAGE_ENDPOINT=os.environ.get('OBJECT_STORAGE_ENDPOINT','').strip()
OBJECT_STORAGE_BUCKET=os.environ.get('OBJECT_STORAGE_BUCKET','').strip()
OBJECT_STORAGE_ACCESS_KEY=os.environ.get('OBJECT_STORAGE_ACCESS_KEY','').strip()
OBJECT_STORAGE_SECRET_KEY=os.environ.get('OBJECT_STORAGE_SECRET_KEY','').strip()
OBJECT_STORAGE_REGION=os.environ.get('OBJECT_STORAGE_REGION','auto').strip() or 'auto'
OBJECT_STORAGE_REQUIRED=os.environ.get('OBJECT_STORAGE_REQUIRED','0')=='1'


def configured()->bool:
    return bool(OBJECT_STORAGE_ENDPOINT and OBJECT_STORAGE_BUCKET and OBJECT_STORAGE_ACCESS_KEY and OBJECT_STORAGE_SECRET_KEY)


def _client():
    if not configured(): return None
    import boto3
    return boto3.client('s3',endpoint_url=OBJECT_STORAGE_ENDPOINT,aws_access_key_id=OBJECT_STORAGE_ACCESS_KEY,aws_secret_access_key=OBJECT_STORAGE_SECRET_KEY,region_name=OBJECT_STORAGE_REGION)


def store_data_uri(data:str,prefix:str='uploads',max_bytes:int=1_750_000)->str:
    if not data: return ''
    if not data.startswith('data:image/'): raise ValueError('Only image uploads are supported.')
    header,payload=data.split(',',1)
    mime=header.split(';',1)[0].split(':',1)[1].lower()
    ext={'image/jpeg':'jpg','image/png':'png','image/webp':'webp','image/gif':'gif'}.get(mime)
    if not ext: raise ValueError('Unsupported image format.')
    raw=base64.b64decode(payload,validate=False)
    if len(raw)>max_bytes: raise ValueError('Image is too large.')
    if not configured():
        if OBJECT_STORAGE_REQUIRED: raise RuntimeError('Production object storage is required but not configured.')
        return data
    key=f'{prefix.strip("/")}/{uuid.uuid4().hex}.{ext}'
    _client().put_object(Bucket=OBJECT_STORAGE_BUCKET,Key=key,Body=raw,ContentType=mime,ServerSideEncryption='AES256')
    return 's3:'+key


def get_bytes(ref:str):
    if not ref.startswith('s3:'): return None
    if not configured(): raise RuntimeError('Object storage is not configured.')
    key=ref[3:]
    obj=_client().get_object(Bucket=OBJECT_STORAGE_BUCKET,Key=key)
    return obj['Body'].read(),obj.get('ContentType') or 'application/octet-stream'
