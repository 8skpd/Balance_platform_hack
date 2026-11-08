"""Provision isolated test accounts through local bootstrap / admin service, then log in."""
from sqlalchemy import select
from app import auth,admin,store

def account(client,username,password,**extra):
    payload={'username':username,'display_name':username,'password':password,**extra}
    with store.engine.connect() as conn:
        owner=conn.execute(select(store.users).where(store.users.c.role=='admin',store.users.c.blocked==False)).mappings().first()
    if owner:admin.create_user(admin.UserCreate(**payload),user=auth.public_user(owner))
    else:auth.bootstrap_admin(auth.Registration(**payload))
    response=client.post('/api/auth/login',json={'username':username,'password':password})
    assert response.status_code==200,response.text
    return response.json()
