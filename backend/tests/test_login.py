"""Test login directly."""
import sys, asyncio, traceback
sys.path.insert(0, r'D:\AI\Autoclaw\ERAG\erag\backend')

async def test():
    from app.database import async_session
    from sqlalchemy import select
    from app.models.user import User
    from app.services.auth import verify_password, create_access_token

    async with async_session() as db:
        r = await db.execute(select(User).where(User.username == 'admin'))
        u = r.scalar_one_or_none()
        if not u:
            print("User 'admin' not found!")
            return

        print(f"User: {u.username}")
        print(f"Role: {u.role}")
        print(f"Active: {u.is_active}")
        print(f"Password hash: {u.hashed_password[:30]}...")

        # Test password
        try:
            ok = verify_password('admin', u.hashed_password)
            print(f"Password verify: {ok}")
        except Exception as e:
            print(f"Password verify ERROR: {e}")
            traceback.print_exc()

        # Test create token
        try:
            token = create_access_token(u.id, u.username, u.role.value, u.tenant_id)
            print(f"Token: {token[:50]}...")
        except Exception as e:
            print(f"Token ERROR: {e}")
            traceback.print_exc()

asyncio.run(test())
