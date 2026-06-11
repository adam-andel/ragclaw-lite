"""Test registration locally."""
import sys, asyncio, traceback, uuid
sys.path.insert(0, r'D:\AI\Autoclaw\ERAG\erag\backend')

async def test():
    from app.database import async_session, init_db
    from sqlalchemy import select
    from app.models.user import User
    from app.services.auth import hash_password

    await init_db()

    # Check if table exists
    async with async_session() as db:
        try:
            r = await db.execute(select(User).limit(1))
            users = r.scalars().all()
            print(f"Users table OK, {len(users)} users")
        except Exception as e:
            print(f"Table error: {e}")
            traceback.print_exc()
            return

        # Try inserting
        try:
            u = User(
                id=str(uuid.uuid4()),
                username="testdirect",
                hashed_password=hash_password("123456"),
                display_name="Test",
                role="admin",
                tenant_id=str(uuid.uuid4()),
            )
            db.add(u)
            await db.commit()
            print(f"User created: {u.id[:8]}")
        except Exception as e:
            print(f"Insert error: {e}")
            traceback.print_exc()

asyncio.run(test())
