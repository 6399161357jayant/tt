import sqlite3, random, string
from datetime import datetime

DB_NAME = "nami.db"

ITEMS = [
    {"name":"sword","emoji":"🗡️","price":5000},
    {"name":"shield","emoji":"🛡️","price":4000},
    {"name":"crown","emoji":"👑","price":10000},
]

def _conn():
    con = sqlite3.connect(DB_NAME)
    con.row_factory = sqlite3.Row
    return con

async def init_db():
    con = _conn()
    cur = con.cursor()

    cur.execute("""CREATE TABLE IF NOT EXISTS game_users(
        telegram_id INTEGER PRIMARY KEY,
        first_name TEXT,
        username TEXT,
        balance INTEGER DEFAULT 0,
        kills INTEGER DEFAULT 0,
        bounty_amount INTEGER DEFAULT 0,
        premium INTEGER DEFAULT 0,
        premium_expires TEXT,
        protection_until TEXT,
        daily_last TEXT,
        job TEXT,
        ship_id INTEGER,
        custom_emoji TEXT,
        rob_count_today INTEGER DEFAULT 0,
        rob_date TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS ships(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        code TEXT UNIQUE,
        captain_id INTEGER
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS ship_members(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ship_id INTEGER,
        user_id INTEGER,
        role TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS user_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        item_name TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS warns(
        chat_id INTEGER,
        user_id INTEGER,
        warns INTEGER DEFAULT 0,
        PRIMARY KEY(chat_id,user_id)
    )""")

    con.commit()
    con.close()

def dict_row(row):
    return dict(row) if row else None

async def get_or_create_user(uid, first_name="", username=""):
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT * FROM game_users WHERE telegram_id=?", (uid,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO game_users(telegram_id,first_name,username) VALUES(?,?,?)",
                    (uid, first_name, username))
        con.commit()
        cur.execute("SELECT * FROM game_users WHERE telegram_id=?", (uid,))
        row = cur.fetchone()
    con.close()
    return dict(row)

async def update_user(uid, **kwargs):
    if not kwargs:
        return
    con = _conn()
    cur = con.cursor()
    q = ", ".join(f"{k}=?" for k in kwargs)
    cur.execute(f"UPDATE game_users SET {q} WHERE telegram_id=?",
                (*kwargs.values(), uid))
    con.commit()
    con.close()

async def execute_raw(query, params=(), fetch=None):
    q = query.replace("%s", "?").replace("RETURNING id", "")
    con = _conn()
    cur = con.cursor()
    cur.execute(q, params)
    con.commit()
    if fetch:
        r = cur.fetchone()
        con.close()
        return dict_row(r)
    rid = cur.lastrowid
    con.close()
    return {"id": rid}

def rand(a,b): return random.randint(a,b)
def today_date(): return datetime.utcnow().date().isoformat()

def is_premium_active(user):
    return bool(user.get("premium"))

def is_protected(user):
    return False

def get_kill_tag(kills, rank): return ""
