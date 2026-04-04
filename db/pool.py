import asyncpg
import os

db_pool = None

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(os.getenv("DATABASE_URL"))

async def get_pool():
    return db_pool