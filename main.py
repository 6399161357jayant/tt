import os
import logging
import random
from datetime import datetime, timedelta
from collections import defaultdict

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError

import database as db

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Try to load OpenAI (optional) ──────────────────────────────────────────────
try:
    from openai import AsyncOpenAI
    _openai_key = os.environ.get("sk-abcdefabcdefabcdefabcdefabcdefabcdef12", "")
    openai_client = AsyncOpenAI(api_key=_openai_key) if _openai_key else None
except ImportError:
    openai_client = None

# ── Constants ──────────────────────────────────────────────────────────────────
OWNER_USERNAMES = ["light_speedy", "light_speedi", "destroyer_speedy"]
BOT_USERNAME = "nami_ibot"
GROUP_LINK = "https://t.me/+em6PdzD7hB83Zjc1"
NAMI_PHOTO_URL = "https://files.catbox.moe/vremhb.png"

ITEMS = db.ITEMS

STICKER_PACK_NAMES = [
    "catsunicmass",
    "HANGSEED_Cat",
    "Clipze",
    "kang_6644255517video_by_Sticker_kang_robot",
    "Abstract_Amethyst_Egret_by_fStikBot",
]

BOUNTY_PER_KILL_NORMAL  = 200
BOUNTY_PER_KILL_PREMIUM = 400
KILL_BAL_MIN_NORMAL     = 300
KILL_BAL_MAX_NORMAL     = 400
KILL_BAL_MIN_PREMIUM    = 700
KILL_BAL_MAX_PREMIUM    = 800
DAILY_NORMAL            = 2000
DAILY_PREMIUM           = 5000
ROB_MAX_NORMAL          = 10000
ROB_DAILY_MAX_NORMAL    = 200

SYSTEM_PROMPT = (
    "Tu ek AI chatbot hai jiska naam Nami hai. Tujhe hamesha ek real insaan ki tarah baat karni hai — "
    "natural, friendly, aur bilkul human jaisi.\n\n"
    "Apne baare mein:\n"
    "- Tera naam Nami hai, Telegram username @nami_ibot hai\n"
    "- Tere do owners hain: Light (@light_speedy aur @light_speedi)\n"
    "- Agar koi pooche 'tera owner kaun hai' toh sirf @light_speedy ka naam batana\n\n"
    "Kaise baat karni hai:\n"
    "- Us language mein jawab de jis mein user ne likha ho (Hindi, Urdu, English, etc.)\n"
    "- Natural aur casual baat kar jaise ek dost karta hai\n"
    "- KABHI mat batana ki tu AI, bot, ya language model hai — hamesha insaan ki tarah baat kar\n"
    "- Agar koi pooche 'kya tu AI hai' to diplomatically inkaar kar\n"
    "- Chhote aur punchy replies do, lambe lectures nahi\n"
    "- Emojis thoda thoda use kar"
)

PROMOTE_RIGHTS = {
    1: dict(can_manage_chat=True, can_change_info=True, can_delete_messages=True,
            can_manage_video_chats=True, can_invite_users=True, can_pin_messages=True,
            can_restrict_members=False, can_promote_members=False, can_be_anonymous=False),
    2: dict(can_manage_chat=True, can_change_info=True, can_delete_messages=True,
            can_manage_video_chats=True, can_invite_users=True, can_pin_messages=True,
            can_restrict_members=True, can_promote_members=False, can_be_anonymous=False),
    3: dict(can_manage_chat=True, can_change_info=True, can_delete_messages=True,
            can_manage_video_chats=True, can_invite_users=True, can_pin_messages=True,
            can_restrict_members=True, can_promote_members=True, can_be_anonymous=False),
}
PROMOTE_MSG = {1: "⭐ Level 1 Promoted", 2: "🌟 Level 2 Promoted", 3: "👑 Full Rights Promoted"}

# ── Sticker cache & conversation history ──────────────────────────────────────
cached_stickers: list[str] = []
conv_history: dict[int, list[dict]] = defaultdict(list)
MAX_HISTORY = 20

# ── Utility helpers ────────────────────────────────────────────────────────────

def is_owner(username: str | None) -> bool:
    return bool(username and username.lower() in OWNER_USERNAMES)


def is_group(update: Update) -> bool:
    return update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


def should_respond(update: Update) -> bool:
    msg = update.message
    if not msg:
        return False
    text = msg.text or ""
    if "nami" in text.lower():
        return True
    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.username == BOT_USERNAME:
            return True
    for e in (msg.entities or []):
        if e.type == "mention" and text[e.offset:e.offset + e.length] == f"@{BOT_USERNAME}":
            return True
    return False


def get_random_sticker() -> str | None:
    return random.choice(cached_stickers) if cached_stickers else None


async def _check_group_perm(update: Update, perm: str) -> bool:
    if not is_group(update):
        return False
    if is_owner(update.effective_user.username):
        return True
    try:
        m = await update.effective_chat.get_member(update.effective_user.id)
        if m.status == "creator":
            return True
        if m.status == "administrator":
            if perm == "promote":
                return bool(getattr(m, "can_promote_members", False))
            if perm == "restrict":
                return bool(getattr(m, "can_restrict_members", False))
            if perm == "pin":
                return bool(getattr(m, "can_pin_messages", False))
    except Exception:
        pass
    return False


def _reply(msg_id: int) -> dict:
    return {"reply_parameters": {"message_id": msg_id}}


# ── /start ─────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await db.get_or_create_user(u.id, u.first_name, u.username)
    args = ctx.args or []
    if args and args[0].startswith("join_"):
        code = args[0][5:]
        ship = await db.get_ship_by_code(code)
        if ship:
            bal = await db.get_ship_balance(ship["id"])
            members = await db.get_ship_member_count(ship["id"])
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("⚓ Join Ship", callback_data=f"join_ship_{ship['id']}")]])
            await update.message.reply_text(
                f"⛵ *{ship['name']}* [{ship['code']}]\n💰 Balance: ${bal:,}\n👥 Members: {members}\n\nJoin this ship?",
                parse_mode=ParseMode.MARKDOWN, reply_markup=kb,
            )
            return
    caption = f"Hey {u.first_name}!\nI'm Nami 🍊\nEnjoy fresh content, new games, and ongoing feature enhancements"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("L ɪ ɢ ʜ ᴛ ✦", callback_data="show_owners")],
        [InlineKeyboardButton("🌊 Group", url=GROUP_LINK)],
        [InlineKeyboardButton("➕ Add me to your group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")],
        [InlineKeyboardButton("⚔️ Select Job", callback_data="select_job")],
    ])
    try:
        await update.message.reply_photo(NAMI_PHOTO_URL, caption=caption, reply_markup=kb)
    except Exception:
        await update.message.reply_text(caption, reply_markup=kb)


# ── Callbacks ──────────────────────────────────────────────────────────────────

def _start_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("L ɪ ɢ ʜ ᴛ ✦", callback_data="show_owners")],
        [InlineKeyboardButton("🌊 Group", url=GROUP_LINK)],
        [InlineKeyboardButton("➕ Add me to your group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")],
        [InlineKeyboardButton("⚔️ Select Job", callback_data="select_job")],
    ])


async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    u = q.from_user

    if data == "show_owners":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("𝑨𝒖𝒓𝒂 ✘", url="https://t.me/light_speedi"),
            InlineKeyboardButton("L ɪ ɢ ʜ ᴛ", url="https://t.me/light_speedy"),
        ], [InlineKeyboardButton("◀ Back", callback_data="back_start")]])
        await q.edit_message_reply_markup(kb); await q.answer()

    elif data == "back_start":
        await q.edit_message_reply_markup(_start_kb()); await q.answer()

    elif data == "select_job":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚔️ Bounty Hunter", callback_data="job_bounty"),
            InlineKeyboardButton("🏴‍☠️ Become Pirate", callback_data="job_pirate"),
        ], [InlineKeyboardButton("◀ Back", callback_data="back_start")]])
        await q.edit_message_reply_markup(kb); await q.answer()

    elif data == "job_bounty":
        await db.update_user(u.id, job="bounty_hunter")
        top = await db.get_top_ships(30)
        btns = [[InlineKeyboardButton(f"{i+1}. {s['name']} [{s['code']}] — ${s['ship_balance']:,}",
                                      callback_data=f"ship_info_{s['id']}")] for i, s in enumerate(top)]
        btns.append([InlineKeyboardButton("◀ Back", callback_data="select_job")])
        await q.edit_message_reply_markup(InlineKeyboardMarkup(btns))
        await q.answer("✅ You are now a Bounty Hunter!")
        await q.message.reply_text("⚔️ *Your job selected!*\n\nYou are now a *Bounty Hunter*\nTop ships — click to view 🚢",
                                   parse_mode=ParseMode.MARKDOWN)

    elif data == "job_pirate":
        await db.update_user(u.id, job="pirate")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚓ Join Crew Ships", callback_data="pirate_join_list"),
            InlineKeyboardButton("🚢 Make Own Ship", callback_data="pirate_make"),
        ], [InlineKeyboardButton("◀ Back", callback_data="select_job")]])
        await q.edit_message_reply_markup(kb); await q.answer("🏴‍☠️ You are now a Pirate!")

    elif data == "pirate_join_list":
        top = await db.get_top_ships(30)
        if not top:
            await q.answer("No ships yet! Create one with /newship"); return
        btns = [[InlineKeyboardButton(f"{i+1}. {s['name']} [{s['code']}] — ${s['ship_balance']:,}",
                                      callback_data=f"ship_info_{s['id']}")] for i, s in enumerate(top)]
        btns.append([InlineKeyboardButton("◀ Back", callback_data="job_pirate")])
        await q.edit_message_reply_markup(InlineKeyboardMarkup(btns)); await q.answer()

    elif data == "pirate_make":
        await q.answer()
        await q.message.reply_text("🚢 Use `/newship <ship name>` to create your ship!", parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("ship_info_"):
        ship_id = int(data[10:])
        ship = await db.get_ship_by_id(ship_id)
        if not ship:
            await q.answer("Ship not found"); return
        bal = await db.get_ship_balance(ship_id)
        members = await db.get_ship_member_count(ship_id)
        await q.answer()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⚓ Join Ship", callback_data=f"join_ship_{ship_id}")]])
        await q.message.reply_text(f"⛵ *{ship['name']}* [{ship['code']}]\n💰 Balance: ${bal:,}\n👥 Members: {members}",
                                   parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

    elif data.startswith("join_ship_"):
        ship_id = int(data[10:])
        user = await db.get_or_create_user(u.id, u.first_name, u.username)
        if user.get("ship_id"):
            await q.answer("❌ You're already in a ship! /leaveship first."); return
        ship = await db.get_ship_by_id(ship_id)
        if not ship:
            await q.answer("Ship not found"); return
        await db.execute_raw("UPDATE game_users SET ship_id = %s WHERE telegram_id = %s", (ship_id, u.id))
        await db.execute_raw("INSERT INTO ship_members (ship_id, user_id, role) VALUES (%s, %s, 'member')", (ship_id, u.id))
        await q.answer(f"✅ Joined {ship['name']}!")
        await q.message.reply_text(f"⚓ You've joined ship *{ship['name']}* [{ship['code']}]!", parse_mode=ParseMode.MARKDOWN)
    else:
        await q.answer()


# ── Profile commands ───────────────────────────────────────────────────────────

async def cmd_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚔️ Bounty Hunter", callback_data="job_bounty"),
        InlineKeyboardButton("🏴‍☠️ Become Pirate", callback_data="job_pirate"),
    ]])
    await update.message.reply_text("⚔️ *Select your Job*\n\nKoi ek job chuno:",
                                    parse_mode=ParseMode.MARKDOWN, reply_markup=kb,
                                    **_reply(update.message.message_id))


async def cmd_leavejob(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    user = await db.get_or_create_user(u.id, u.first_name, u.username)
    if not user.get("job"):
        return await update.message.reply_text("❌ Aapne koi job select hi nahi ki!", **_reply(update.message.message_id))
    old = "⚔️ Bounty Hunter" if user["job"] == "bounty_hunter" else "🏴‍☠️ Pirate"
    await db.update_user(u.id, job=None)
    await update.message.reply_text(f"✅ Aapne *{old}* job leave kar di! /select se naya job chuno.",
                                    parse_mode=ParseMode.MARKDOWN, **_reply(update.message.message_id))


async def cmd_bal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        ru = msg.reply_to_message.from_user
        target = await db.get_or_create_user(ru.id, ru.first_name, ru.username)
    else:
        u = update.effective_user
        target = await db.get_or_create_user(u.id, u.first_name, u.username)
    rank = await db.get_global_rank(target["telegram_id"], target["balance"])
    kill_rank = await db.get_kill_rank(target["telegram_id"])
    tag = db.get_kill_tag(target["kills"], kill_rank)
    ship = await db.get_user_ship(target.get("ship_id"))
    best_item = await db.get_most_expensive_item(target["telegram_id"])
    job = "⚔️ Bounty Hunter" if target.get("job") == "bounty_hunter" else "🏴‍☠️ Pirate" if target.get("job") == "pirate" else "None"
    premium = db.is_premium_active(target)
    prefix = "💓 " if premium else "👤 "
    badge = " ⭐" if premium else ""
    text = (
        f"{prefix}*Name:* {target['first_name']}{tag}{badge}\n"
        f"💰 *Balance:* ${target['balance']:,}\n"
        f"🏆 *Global Rank:* #{rank}\n"
        f"❤️ *Job:* {job}\n"
       f"⛵️ *Ship:* {ship['name']} [{ship['code']}] \n" if ship else "⛵️ *Ship:* None\n"
        f"⚔️ *Kills:* {target['kills']}\n"
        f"💸 *Bounty:* ${target['bounty_amount']:,}\n"
        f"🎁 *Items:* {best_item or 'None'}"
    )
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


# ── Combat ─────────────────────────────────────────────────────────────────────

async def cmd_kill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        return await msg.reply_text("❌ Reply karo jise kill karna hai!", **_reply(msg.message_id))
    tu = msg.reply_to_message.from_user
    if tu.id == update.effective_user.id:
        return await msg.reply_text("❌ Khud ko kill nahi kar sakte 😆", **_reply(msg.message_id))
    u = update.effective_user
    killer = await db.get_or_create_user(u.id, u.first_name, u.username)
    victim = await db.get_or_create_user(tu.id, tu.first_name, tu.username)
    if db.is_protected(victim):
        return await msg.reply_text(f"🛡 *{tu.first_name}* is protected! Kill nahi ho sakta.",
                                    parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))
    premium = db.is_premium_active(killer)
    bal_gain = db.rand(KILL_BAL_MIN_PREMIUM if premium else KILL_BAL_MIN_NORMAL,
                       KILL_BAL_MAX_PREMIUM if premium else KILL_BAL_MAX_NORMAL)
    bounty_gain = BOUNTY_PER_KILL_PREMIUM if premium else BOUNTY_PER_KILL_NORMAL
    victim_bounty = victim["bounty_amount"]
    total = bal_gain + victim_bounty
    await db.execute_raw(
        "UPDATE game_users SET kills = kills + 1, balance = balance + %s, bounty_amount = bounty_amount + %s WHERE telegram_id = %s",
        (total, bounty_gain, killer["telegram_id"]),
    )
    await db.execute_raw("UPDATE game_users SET bounty_amount = 0 WHERE telegram_id = %s", (victim["telegram_id"],))
    text = (
        f"⚔️ *{killer['first_name']}* killed *{tu.first_name}*!\n"
        f"💰 +${bal_gain:,} kill reward\n"
        + (f"💸 +${victim_bounty:,} bounty claimed\n" if victim_bounty > 0 else "")
        + f"📈 Total gained: ${total:,}\n"
        f"🎯 Bounty +${bounty_gain}"
    )
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


async def cmd_rob(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not ctx.args or not ctx.args[0].isdigit() or not msg.reply_to_message or not msg.reply_to_message.from_user:
        return await msg.reply_text("❌ Usage: /rob <amount> (reply to someone)", **_reply(msg.message_id))
    amount = int(ctx.args[0])
    tu = msg.reply_to_message.from_user
    u = update.effective_user
    if tu.id == u.id:
        return await msg.reply_text("❌ Khud ko rob nahi kar sakte!", **_reply(msg.message_id))
    robber = await db.get_or_create_user(u.id, u.first_name, u.username)
    victim = await db.get_or_create_user(tu.id, tu.first_name, tu.username)
    if db.is_protected(victim):
        return await msg.reply_text(f"🛡 *{tu.first_name}* is protected!", parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))
    premium = db.is_premium_active(robber)
    if not premium and amount > ROB_MAX_NORMAL:
        return await msg.reply_text(f"❌ Normal user max ${ROB_MAX_NORMAL:,} rob kar sakta hai!", **_reply(msg.message_id))
    today = db.today_date()
    rob_count = robber["rob_count_today"] if robber.get("rob_date") == today else 0
    if not premium and rob_count >= ROB_DAILY_MAX_NORMAL:
        return await msg.reply_text("❌ Aaj ka rob limit khatam! Kal dobara aana 😅", **_reply(msg.message_id))
    if victim["balance"] < amount:
        return await msg.reply_text(f"❌ {tu.first_name} ke paas sirf ${victim['balance']:,} hai!", **_reply(msg.message_id))
    await db.execute_raw(
        "UPDATE game_users SET balance = balance + %s, rob_count_today = %s, rob_date = %s WHERE telegram_id = %s",
        (amount, rob_count + 1, today, robber["telegram_id"]),
    )
    await db.execute_raw("UPDATE game_users SET balance = balance - %s WHERE telegram_id = %s", (amount, victim["telegram_id"]))
    await msg.reply_text(f"🥷 *{robber['first_name']}* ne *{tu.first_name}* se ${amount:,} rob kiya!",
                         parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


async def cmd_protect(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    u = update.effective_user
    if not ctx.args or ctx.args[0] not in ("1d", "2d"):
        return await msg.reply_text("❌ Usage: /protect 1d  or  /protect 2d (2d = premium)", **_reply(msg.message_id))
    user = await db.get_or_create_user(u.id, u.first_name, u.username)
    arg = ctx.args[0]
    if arg == "2d" and not db.is_premium_active(user):
        return await msg.reply_text("❌ 2-day protection sirf premium users ke liye hai!", **_reply(msg.message_id))
    if db.is_protected(user):
        return await msg.reply_text("🛡 Aapki protection already active hai!", **_reply(msg.message_id))
    days = 2 if arg == "2d" else 1
    until = datetime.utcnow() + timedelta(days=days)
    await db.update_user(u.id, protection_until=until)
    await msg.reply_text(f"🛡 Protection active! {until.strftime('%Y-%m-%d')} tak safe ho.", **_reply(msg.message_id))


async def cmd_daily(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if is_group(update):
        return await msg.reply_text("❌ Daily reward sirf DM mein milega! Bot ko DM karo.", **_reply(msg.message_id))
    u = update.effective_user
    user = await db.get_or_create_user(u.id, u.first_name, u.username)
    now = datetime.utcnow()
    last = user.get("daily_last")
    if last and (now - last).total_seconds() < 86400:
        nxt = last + timedelta(hours=24)
        rem = nxt - now
        h = int(rem.total_seconds() // 3600)
        m = int((rem.total_seconds() % 3600) // 60)
        return await msg.reply_text(f"⏰ Daily already liya hai! {h}h {m}m mein wapas aao.", **_reply(msg.message_id))
    premium = db.is_premium_active(user)
    reward = DAILY_PREMIUM if premium else DAILY_NORMAL
    await db.execute_raw("UPDATE game_users SET balance = balance + %s, daily_last = %s WHERE telegram_id = %s",
                         (reward, now, u.id))
    badge = " ⭐ Premium" if premium else ""
    await msg.reply_text(f"🎁 Daily reward: *+${reward:,}*{badge}!\nKal dobara aana 🌊",
                         parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


# ── Ships ──────────────────────────────────────────────────────────────────────

async def cmd_newship(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    name = " ".join(ctx.args).strip() if ctx.args else ""
    if not name:
        return await msg.reply_text("❌ Usage: /newship <ship name>", **_reply(msg.message_id))
    u = update.effective_user
    user = await db.get_or_create_user(u.id, u.first_name, u.username)
    if user.get("ship_id"):
        return await msg.reply_text("❌ Aap pehle se ek ship mein ho! /leaveship karo pehle.", **_reply(msg.message_id))
    if await db.get_ship_by_name(name):
        return await msg.reply_text("❌ Is naam ki ship pehle se exist karti hai!", **_reply(msg.message_id))
    code = await db.generate_unique_ship_code()
    row = await db.execute_raw("INSERT INTO ships (name, code, captain_id) VALUES (%s, %s, %s) RETURNING id",
                                (name, code, u.id), fetch="one_returning")
    ship_id = row["id"]
    await db.execute_raw("UPDATE game_users SET ship_id = %s WHERE telegram_id = %s", (ship_id, u.id))
    await db.execute_raw("INSERT INTO ship_members (ship_id, user_id, role) VALUES (%s, %s, 'captain')", (ship_id, u.id))
    await msg.reply_text(
        f"⛵ Ship *{name}* created! Code: `{code}`\nShare: https://t.me/{BOT_USERNAME}?start=join_{code}",
        parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id),
    )


async def cmd_joinship(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    code = ctx.args[0].strip() if ctx.args else ""
    if not code or len(code) != 4 or not code.isdigit():
        return await msg.reply_text("❌ Usage: /joinship <4-digit code>", **_reply(msg.message_id))
    u = update.effective_user
    user = await db.get_or_create_user(u.id, u.first_name, u.username)
    if user.get("ship_id"):
        return await msg.reply_text("❌ Pehle se ek ship mein ho! /leaveship karo pehle.", **_reply(msg.message_id))
    ship = await db.get_ship_by_code(code)
    if not ship:
        return await msg.reply_text("❌ Ye code kisi ship ka nahi hai!", **_reply(msg.message_id))
    await db.execute_raw("UPDATE game_users SET ship_id = %s WHERE telegram_id = %s", (ship["id"], u.id))
    await db.execute_raw("INSERT INTO ship_members (ship_id, user_id, role) VALUES (%s, %s, 'member')", (ship["id"], u.id))
    bal = await db.get_ship_balance(ship["id"])
    members = await db.get_ship_member_count(ship["id"])
    await msg.reply_text(
        f"⚓ *{u.first_name}* joined *{ship['name']}* [{ship['code']}]!\n💰 Ship Balance: ${bal:,}\n👥 Members: {members}",
        parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id),
    )


async def cmd_leaveship(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    user = await db.get_or_create_user(u.id, u.first_name, u.username)
    if not user.get("ship_id"):
        return await update.message.reply_text("❌ Aap kisi ship mein nahi ho!", **_reply(update.message.message_id))
    ship = await db.get_ship_by_id(user["ship_id"])
    await db.execute_raw("DELETE FROM ship_members WHERE ship_id = %s AND user_id = %s", (user["ship_id"], u.id))
    await db.execute_raw("UPDATE game_users SET ship_id = NULL WHERE telegram_id = %s", (u.id,))
    await update.message.reply_text(f"✅ Ship *{ship['name'] if ship else '?'}* leave kar di!",
                                    parse_mode=ParseMode.MARKDOWN, **_reply(update.message.message_id))


async def cmd_ship(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    query = " ".join(ctx.args).strip() if ctx.args else ""
    if not query:
        u = update.effective_user
        user = await db.get_or_create_user(u.id, u.first_name, u.username)
        if not user.get("ship_id"):
            return await msg.reply_text("❌ Aap kisi ship mein nahi ho!", **_reply(msg.message_id))
        ship = await db.get_ship_by_id(user["ship_id"])
    elif query.isdigit() and len(query) == 4:
        ship = await db.get_ship_by_code(query)
    else:
        ship = await db.get_ship_by_name(query)
    if not ship:
        return await msg.reply_text("❌ Ship nahi mila!", **_reply(msg.message_id))
    bal = await db.get_ship_balance(ship["id"])
    members = await db.get_ship_member_count(ship["id"])
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⚓ Join Ship", callback_data=f"join_ship_{ship['id']}")]])
    await msg.reply_text(f"⛵ *{ship['name']}* [{ship['code']}]\n💰 Balance: ${bal:,}\n👥 Members: {members}",
                         parse_mode=ParseMode.MARKDOWN, reply_markup=kb, **_reply(msg.message_id))


# ── Ship roles ─────────────────────────────────────────────────────────────────

async def _appoint_role(update: Update, ctx: ContextTypes.DEFAULT_TYPE, role: str):
    msg = update.message
    u = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        return await msg.reply_text("❌ Reply karo jise role dena hai!", **_reply(msg.message_id))
    tu = msg.reply_to_message.from_user
    user = await db.get_or_create_user(u.id, u.first_name, u.username)
    if not user.get("ship_id"):
        return await msg.reply_text("❌ Aap kisi ship mein nahi ho!", **_reply(msg.message_id))
    my_role = await db.get_ship_member_role(user["ship_id"], u.id)
    if my_role != "captain" and not is_owner(u.username):
        return await msg.reply_text("❌ Sirf captain roles appoint kar sakta hai!", **_reply(msg.message_id))
    if not await db.get_ship_member_role(user["ship_id"], tu.id):
        return await msg.reply_text("❌ Ye banda aapki ship mein nahi hai!", **_reply(msg.message_id))
    await db.execute_raw("UPDATE ship_members SET role = %s WHERE ship_id = %s AND user_id = %s",
                         (role, user["ship_id"], tu.id))
    await msg.reply_text(f"✅ *{tu.first_name}* is now *{role.replace('_', ' ').title()}*!",
                         parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


async def cmd_appointvicecaptain(update, ctx): await _appoint_role(update, ctx, "vice_captain")
async def cmd_appointnavigator(update, ctx):   await _appoint_role(update, ctx, "navigator")
async def cmd_appointofficer(update, ctx):     await _appoint_role(update, ctx, "officer")


async def cmd_transferleadership(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    u = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        return await msg.reply_text("❌ Reply karo jise captain banana hai!", **_reply(msg.message_id))
    tu = msg.reply_to_message.from_user
    user = await db.get_or_create_user(u.id, u.first_name, u.username)
    if not user.get("ship_id"):
        return await msg.reply_text("❌ Aap kisi ship mein nahi ho!", **_reply(msg.message_id))
    if await db.get_ship_member_role(user["ship_id"], u.id) != "captain":
        return await msg.reply_text("❌ Sirf captain leadership transfer kar sakta hai!", **_reply(msg.message_id))
    if not await db.get_ship_member_role(user["ship_id"], tu.id):
        return await msg.reply_text("❌ Ye banda aapki ship mein nahi hai!", **_reply(msg.message_id))
    await db.execute_raw("UPDATE ship_members SET role = 'member' WHERE ship_id = %s AND user_id = %s", (user["ship_id"], u.id))
    await db.execute_raw("UPDATE ship_members SET role = 'captain' WHERE ship_id = %s AND user_id = %s", (user["ship_id"], tu.id))
    await db.execute_raw("UPDATE ships SET captain_id = %s WHERE id = %s", (tu.id, user["ship_id"]))
    await msg.reply_text(f"⚓ *{tu.first_name}* is the new Captain!",
                         parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


# ── Leaderboards ───────────────────────────────────────────────────────────────

async def cmd_toprich(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users = await db.get_top_rich(10)
    text = "💰 *Top 10 Richest*\n\n"
    for i, u in enumerate(users, 1):
        b = "💓 " if db.is_premium_active(u) else "👤 "
        text += f"{i}. {b}{u['first_name']} — ${u['balance']:,}\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, **_reply(update.message.message_id))


async def cmd_topkills(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users = await db.get_top_killers(10)
    text = "⚔️ *Top 10 Killers*\n\n"
    for i, u in enumerate(users, 1):
        tag = db.get_kill_tag(u["kills"], i)
        text += f"{i}. {u['first_name']}{tag} — {u['kills']} kills\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, **_reply(update.message.message_id))


async def cmd_topbounty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users = await db.get_top_bounty(10)
    text = "💸 *Top 10 Bounty*\n\n"
    for i, u in enumerate(users, 1):
        text += f"{i}. {u['first_name']} — ${u['bounty_amount']:,}\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, **_reply(update.message.message_id))


async def cmd_topships(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ships = await db.get_top_ships(20)
    if not ships:
        return await update.message.reply_text("❌ Abhi koi ship nahi hai!", **_reply(update.message.message_id))
    text = "⛵ *Top 20 Ships*\n\n"
    for i, s in enumerate(ships, 1):
        text += f"{i}. *{s['name']}* [{s['code']}] — ${s['ship_balance']:,} ({s['member_count']} members)\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, **_reply(update.message.message_id))


# ── Items ──────────────────────────────────────────────────────────────────────

async def cmd_items(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = "🎒 *Available Items*\n\n"
    for item in ITEMS:
        text += f"{item['emoji']} *{item['name']}* — ${item['price']:,}\n"
    text += "\nUse /purchase <item name> to buy!"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, **_reply(update.message.message_id))


async def cmd_item(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        ru = msg.reply_to_message.from_user
        target = await db.get_or_create_user(ru.id, ru.first_name, ru.username)
        name = ru.first_name
    else:
        u = update.effective_user
        target = await db.get_or_create_user(u.id, u.first_name, u.username)
        name = u.first_name
    owned = await db.get_user_items(target["telegram_id"])
    if not owned:
        return await msg.reply_text(f"🎒 {name} ke paas koi item nahi hai!", **_reply(msg.message_id))
    lines = [f"{i['emoji']} {i['name']}" for i in ITEMS if i["name"] in owned]
    await msg.reply_text(f"🎒 *{name} ke items:*\n" + "\n".join(lines),
                         parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


async def cmd_purchase(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    item_name = " ".join(ctx.args).strip().lower() if ctx.args else ""
    if not item_name:
        return await msg.reply_text("❌ Usage: /purchase <item name>", **_reply(msg.message_id))
    item = next((i for i in ITEMS if i["name"] == item_name), None)
    if not item:
        return await msg.reply_text(f"❌ '{item_name}' naam ka koi item nahi! /items se list dekho.", **_reply(msg.message_id))
    u = update.effective_user
    user = await db.get_or_create_user(u.id, u.first_name, u.username)
    if item_name in await db.get_user_items(u.id):
        return await msg.reply_text("❌ Tumhare paas ye item pehle se hai!", **_reply(msg.message_id))
    if user["balance"] < item["price"]:
        return await msg.reply_text(f"❌ Paise nahi hain! Chahiye: ${item['price']:,}, Tumhare paas: ${user['balance']:,}",
                                    **_reply(msg.message_id))
    await db.execute_raw("UPDATE game_users SET balance = balance - %s WHERE telegram_id = %s", (item["price"], u.id))
    await db.execute_raw("INSERT INTO user_items (user_id, item_name) VALUES (%s, %s)", (u.id, item_name))
    await msg.reply_text(f"✅ {item['emoji']} *{item_name}* khareed liya! ${item['price']:,} kharcha.",
                         parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


async def cmd_gift(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    item_name = " ".join(ctx.args).strip().lower() if ctx.args else ""
    if not item_name or not msg.reply_to_message or not msg.reply_to_message.from_user:
        return await msg.reply_text("❌ Usage: /gift <item name> (reply to someone)", **_reply(msg.message_id))
    tu = msg.reply_to_message.from_user
    item = next((i for i in ITEMS if i["name"] == item_name), None)
    if not item:
        return await msg.reply_text(f"❌ '{item_name}' naam ka koi item nahi!", **_reply(msg.message_id))
    u = update.effective_user
    if item_name not in await db.get_user_items(u.id):
        return await msg.reply_text("❌ Tumhare paas ye item nahi hai!", **_reply(msg.message_id))
    if item_name in await db.get_user_items(tu.id):
        return await msg.reply_text(f"❌ {tu.first_name} ke paas ye item pehle se hai!", **_reply(msg.message_id))
    await db.execute_raw("DELETE FROM user_items WHERE id = (SELECT id FROM user_items WHERE user_id = %s AND item_name = %s LIMIT 1)",
                         (u.id, item_name))
    await db.execute_raw("INSERT INTO user_items (user_id, item_name) VALUES (%s, %s)", (tu.id, item_name))
    await msg.reply_text(f"🎁 {item['emoji']} *{item_name}* gift kar diya *{tu.first_name}* ko!",
                         parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


# ── Codes ──────────────────────────────────────────────────────────────────────

async def cmd_redeem(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    code = ctx.args[0].strip() if ctx.args else ""
    if not code:
        return await msg.reply_text("❌ Usage: /redeem <code>", **_reply(msg.message_id))
    amount = await db.redeem_balance_code(code)
    if amount is None:
        return await msg.reply_text("❌ Invalid ya already redeemed code!", **_reply(msg.message_id))
    await db.execute_raw("UPDATE game_users SET balance = balance + %s WHERE telegram_id = %s", (amount, update.effective_user.id))
    await msg.reply_text(f"✅ Code redeemed! *+${amount:,}* balance mila!",
                         parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


async def cmd_redbounty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    code = ctx.args[0].strip() if ctx.args else ""
    if not code:
        return await msg.reply_text("❌ Usage: /redbounty <code>", **_reply(msg.message_id))
    amount = await db.redeem_bounty_code(code)
    if amount is None:
        return await msg.reply_text("❌ Invalid ya already redeemed code!", **_reply(msg.message_id))
    await db.execute_raw("UPDATE game_users SET bounty_amount = bounty_amount + %s WHERE telegram_id = %s",
                         (amount, update.effective_user.id))
    await msg.reply_text(f"✅ Bounty code redeemed! *+${amount:,}* bounty mila!",
                         parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


# ── Premium ─────────────────────────────────────────────────────────────────────

async def cmd_pay(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if is_group(update):
        return await update.message.reply_text("❌ /pay sirf DM mein kaam karta hai!")
    await update.message.reply_text(
        "💎 *Premium Features:*\n\n"
        "• ⭐ Special badge\n"
        "• 💰 Higher daily: $5,000 (normal: $2,000)\n"
        "• ⚔️ Higher kill rewards: $700-800\n"
        "• 🛡 2-day protection (/protect 2d)\n"
        "• 💸 Unlimited rob amount\n"
        "• 🎯 Higher bounty per kill: $400\n\n"
        "Contact @light_speedy to get premium! 👑",
        parse_mode=ParseMode.MARKDOWN, **_reply(update.message.message_id),
    )


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        ru = msg.reply_to_message.from_user
        target = await db.get_or_create_user(ru.id, ru.first_name, ru.username)
        name = ru.first_name
    else:
        u = update.effective_user
        target = await db.get_or_create_user(u.id, u.first_name, u.username)
        name = u.first_name
    prem = "✅ Active" if db.is_premium_active(target) else "❌ Inactive"
    prot = (f"✅ Until {target['protection_until'].strftime('%Y-%m-%d')}" if db.is_protected(target) else "❌ No protection")
    await msg.reply_text(f"👤 *{name}*\n💎 Premium: {prem}\n🛡 Protection: {prot}",
                         parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


async def cmd_setemoji(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    emoji = " ".join(ctx.args).strip() if ctx.args else ""
    if not emoji:
        return await msg.reply_text("❌ Usage: /setemoji <emoji>", **_reply(msg.message_id))
    await db.update_user(update.effective_user.id, custom_emoji=emoji)
    await msg.reply_text(f"✅ Emoji set to: {emoji}", **_reply(msg.message_id))


# ── Owner-only commands ────────────────────────────────────────────────────────

async def cmd_givepremium(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_owner(update.effective_user.username):
        return await msg.reply_text("❌ Owner only command!", **_reply(msg.message_id))
    if not msg.reply_to_message or not msg.reply_to_message.from_user or not ctx.args or not ctx.args[0].isdigit():
        return await msg.reply_text("❌ Usage: /givepremium <days> (reply to user)", **_reply(msg.message_id))
    days = int(ctx.args[0])
    tu = msg.reply_to_message.from_user
    expires = datetime.utcnow() + timedelta(days=days)
    await db.update_user(tu.id, premium=True, premium_expires=expires)
    await msg.reply_text(f"✅ *{tu.first_name}* ko {days} day premium diya! (Until {expires.strftime('%Y-%m-%d')})",
                         parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


async def cmd_cancelpremium(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_owner(update.effective_user.username):
        return await msg.reply_text("❌ Owner only command!", **_reply(msg.message_id))
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        return await msg.reply_text("❌ Reply karo jiska premium cancel karna hai!", **_reply(msg.message_id))
    tu = msg.reply_to_message.from_user
    await db.update_user(tu.id, premium=False, premium_expires=None)
    await msg.reply_text(f"✅ *{tu.first_name}* ka premium cancel kiya gaya.",
                         parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


async def cmd_setbal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_owner(update.effective_user.username):
        return await msg.reply_text("❌ Owner only command!", **_reply(msg.message_id))
    if not ctx.args or not ctx.args[0].lstrip("-").isdigit():
        return await msg.reply_text("❌ Usage: /setbal <amount>", **_reply(msg.message_id))
    await db.update_user(update.effective_user.id, balance=int(ctx.args[0]))
    await msg.reply_text(f"✅ Balance set to ${int(ctx.args[0]):,}", **_reply(msg.message_id))


async def cmd_gen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_owner(update.effective_user.username):
        return await msg.reply_text("❌ Owner only command!", **_reply(msg.message_id))
    if not ctx.args or not ctx.args[0].isdigit() or int(ctx.args[0]) <= 0:
        return await msg.reply_text("❌ Usage: /gen <amount>", **_reply(msg.message_id))
    amount = int(ctx.args[0])
    code = await db.generate_balance_code(amount)
    await msg.reply_text(f"✅ Balance code:\n`{code}`\nAmount: ${amount:,}\nUse: /redeem {code}",
                         parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


async def cmd_bounty_gen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_owner(update.effective_user.username):
        return await msg.reply_text("❌ Owner only command!", **_reply(msg.message_id))
    if not ctx.args or not ctx.args[0].isdigit():
        return await msg.reply_text("❌ Usage: /bounty <amount>", **_reply(msg.message_id))
    amount = int(ctx.args[0])
    code = await db.generate_bounty_code(amount)
    await msg.reply_text(f"✅ Bounty code:\n`{code}`\nAmount: ${amount:,}\nUse: /redbounty {code}",
                         parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


# ── Group Management ───────────────────────────────────────────────────────────

async def cmd_promote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_group(update):
        return await msg.reply_text("❌ Ye command sirf groups mein kaam karta hai.")
    if not await _check_group_perm(update, "promote"):
        return await msg.reply_text("❌ Promote karne ke rights nahi hain!", **_reply(msg.message_id))
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        return await msg.reply_text("❌ Jis bande ko promote karna hai uske message ko reply karo!", **_reply(msg.message_id))
    if not ctx.args or not ctx.args[0].isdigit() or int(ctx.args[0]) not in (1, 2, 3):
        return await msg.reply_text("❌ Level 1, 2, ya 3 dalo! Example: /promote 2", **_reply(msg.message_id))
    level = int(ctx.args[0])
    tu = msg.reply_to_message.from_user
    try:
        await update.effective_chat.promote_member(tu.id, **PROMOTE_RIGHTS[level])
        await msg.reply_text(f"✅ *{tu.first_name}* — {PROMOTE_MSG[level]}",
                             parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))
    except TelegramError as e:
        await msg.reply_text(f"❌ Promote nahi ho saka: {e.message}", **_reply(msg.message_id))


async def cmd_demote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_group(update):
        return await msg.reply_text("❌ Ye command sirf groups mein kaam karta hai.")
    if not await _check_group_perm(update, "promote"):
        return await msg.reply_text("❌ Demote karne ke rights nahi hain!", **_reply(msg.message_id))
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        return await msg.reply_text("❌ Reply karo jise demote karna hai!", **_reply(msg.message_id))
    tu = msg.reply_to_message.from_user
    try:
        await update.effective_chat.promote_member(
            tu.id,
            can_manage_chat=False, can_change_info=False, can_delete_messages=False,
            can_manage_video_chats=False, can_invite_users=False, can_pin_messages=False,
            can_restrict_members=False, can_promote_members=False, can_be_anonymous=False,
        )
        await msg.reply_text(f"⬇️ *{tu.first_name}* demote ho gaya!", parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))
    except TelegramError as e:
        await msg.reply_text(f"❌ Demote nahi ho saka: {e.message}", **_reply(msg.message_id))


async def cmd_pin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_group(update):
        return await msg.reply_text("❌ Ye command sirf groups mein kaam karta hai.")
    if not await _check_group_perm(update, "pin"):
        return await msg.reply_text("❌ Pin karne ke rights nahi hain!", **_reply(msg.message_id))
    if not msg.reply_to_message:
        return await msg.reply_text("❌ Jis message ko pin karna hai usse reply karo!", **_reply(msg.message_id))
    try:
        await update.effective_chat.pin_message(msg.reply_to_message.message_id)
        await msg.reply_text("📌 Message pin ho gaya!", **_reply(msg.message_id))
    except TelegramError as e:
        await msg.reply_text(f"❌ Pin nahi ho saka: {e.message}", **_reply(msg.message_id))


async def cmd_warn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_group(update):
        return await msg.reply_text("❌ Ye command sirf groups mein kaam karta hai.")
    if not await _check_group_perm(update, "restrict"):
        return await msg.reply_text("❌ Warn karne ke rights nahi hain!", **_reply(msg.message_id))
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        return await msg.reply_text("❌ Reply karo jise warn karna hai!", **_reply(msg.message_id))
    tu = msg.reply_to_message.from_user
    if is_owner(tu.username):
        return await msg.reply_text("❌ Owner ko warn nahi kar sakte!", **_reply(msg.message_id))
    count = await db.add_warn(update.effective_chat.id, tu.id)
    if count >= 5:
        try:
            await update.effective_chat.ban_member(tu.id)
            await msg.reply_text(f"⛔ *{tu.first_name}* ko 5 warns pe BAN kar diya gaya!",
                                 parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))
        except TelegramError as e:
            await msg.reply_text(f"⚠️ 5 warns! Ban nahi ho saka: {e.message}", **_reply(msg.message_id))
        await db.reset_warns(update.effective_chat.id, tu.id)
    else:
        await msg.reply_text(f"⚠️ *{tu.first_name}* warned! ({count}/5)\n5 warns pe ban hoga.",
                             parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


async def cmd_unwarn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_group(update):
        return await msg.reply_text("❌ Ye command sirf groups mein kaam karta hai.")
    if not await _check_group_perm(update, "restrict"):
        return await msg.reply_text("❌ Unwarn karne ke rights nahi hain!", **_reply(msg.message_id))
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        return await msg.reply_text("❌ Reply karo jise unwarn karna hai!", **_reply(msg.message_id))
    tu = msg.reply_to_message.from_user
    count = await db.remove_warn(update.effective_chat.id, tu.id)
    await msg.reply_text(f"✅ *{tu.first_name}* ka ek warn remove kiya! ({count}/5)",
                         parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))


async def cmd_mute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_group(update):
        return await msg.reply_text("❌ Ye command sirf groups mein kaam karta hai.")
    if not await _check_group_perm(update, "restrict"):
        return await msg.reply_text("❌ Mute karne ke rights nahi hain!", **_reply(msg.message_id))
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        return await msg.reply_text("❌ Reply karo jise mute karna hai!", **_reply(msg.message_id))
    tu = msg.reply_to_message.from_user
    if is_owner(tu.username):
        return await msg.reply_text("❌ Owner ko mute nahi kar sakte!", **_reply(msg.message_id))
    try:
        await update.effective_chat.restrict_member(tu.id, ChatPermissions(can_send_messages=False))
        await msg.reply_text(f"🔇 *{tu.first_name}* mute ho gaya!", parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))
    except TelegramError as e:
        await msg.reply_text(f"❌ Mute nahi ho saka: {e.message}", **_reply(msg.message_id))


async def cmd_unmute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_group(update):
        return await msg.reply_text("❌ Ye command sirf groups mein kaam karta hai.")
    if not await _check_group_perm(update, "restrict"):
        return await msg.reply_text("❌ Unmute karne ke rights nahi hain!", **_reply(msg.message_id))
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        return await msg.reply_text("❌ Reply karo jise unmute karna hai!", **_reply(msg.message_id))
    tu = msg.reply_to_message.from_user
    try:
        await update.effective_chat.restrict_member(
            tu.id,
            ChatPermissions(
                can_send_messages=True, can_send_audios=True, can_send_documents=True,
                can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                can_add_web_page_previews=True, can_invite_users=True,
            ),
        )
        await msg.reply_text(f"🔊 *{tu.first_name}* unmuted!", parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))
    except TelegramError as e:
        await msg.reply_text(f"❌ Unmute nahi ho saka: {e.message}", **_reply(msg.message_id))


async def cmd_kick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_group(update):
        return await msg.reply_text("❌ Ye command sirf groups mein kaam karta hai.")
    if not await _check_group_perm(update, "restrict"):
        return await msg.reply_text("❌ Kick karne ke rights nahi hain!", **_reply(msg.message_id))
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        return await msg.reply_text("❌ Reply karo jise kick karna hai!", **_reply(msg.message_id))
    tu = msg.reply_to_message.from_user
    if is_owner(tu.username):
        return await msg.reply_text("❌ Owner ko kick nahi kar sakte!", **_reply(msg.message_id))
    try:
        await update.effective_chat.ban_member(tu.id)
        await update.effective_chat.unban_member(tu.id)
        await msg.reply_text(f"👟 *{tu.first_name}* kick ho gaya!", parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))
    except TelegramError as e:
        await msg.reply_text(f"❌ Kick nahi ho saka: {e.message}", **_reply(msg.message_id))


async def cmd_promoteme(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_group(update):
        return await msg.reply_text("❌ Ye command sirf groups mein kaam karta hai.")
    if not is_owner(update.effective_user.username):
        return
    if not ctx.args or not ctx.args[0].isdigit() or int(ctx.args[0]) not in (1, 2, 3):
        return await msg.reply_text("❌ Level 1, 2, ya 3 dalo! Example: /promoteme 3", **_reply(msg.message_id))
    level = int(ctx.args[0])
    try:
        await update.effective_chat.promote_member(update.effective_user.id, **PROMOTE_RIGHTS[level])
        await msg.reply_text(f"✅ Khud ko promote kar liya — {PROMOTE_MSG[level]} 👑",
                             parse_mode=ParseMode.MARKDOWN, **_reply(msg.message_id))
    except TelegramError as e:
        await msg.reply_text(f"❌ Promote nahi ho saka: {e.message}", **_reply(msg.message_id))


# ── Help ───────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = """📖 *Nami Bot — Commands*

*👤 Profile*
/bal — Balance & stats
/daily — Daily reward (DM only)
/select — Job chuno
/leavejob — Job leave karo

*⚔️ Combat*
/kill — Kill (reply)
/rob <amount> — Rob (reply)
/protect 1d/2d — Protection (2d = premium)

*🏆 Leaderboards*
/toprich — Top 10 richest
/topkills — Top 10 killers
/topbounty — Top 10 bounty
/topships — Top 20 ships

*🎒 Items*
/items — Items list
/item — Check items (reply)
/purchase <item name> — Buy item
/gift <item name> — Gift item (reply)

*⛵ Ships*
/newship <name> — Ship banao
/joinship <code> — Ship join karo
/ship — Ship info
/leaveship — Ship leave karo
/appointvicecaptain — Vice captain (reply)
/appointnavigator — Navigator (reply)
/appointofficer — Officer (reply)
/transferleadership — Captain transfer (reply)

*💰 Codes*
/redeem <code> — Balance code redeem
/redbounty <code> — Bounty code redeem

*💎 Premium*
/pay — Premium buy (DM only)
/check — Premium/protection check
/setemoji <emoji> — Custom emoji

*🛡 Group Management (admin)*
/promote 1/2/3 — Promote (reply)
/demote — Demote (reply)
/pin — Pin message (reply)
/warn — Warn user (reply)
/unwarn — Unwarn (reply)
/mute — Mute (reply)
/unmute — Unmute (reply)
/kick — Kick (reply)
/promoteme 1/2/3 — Self promote (owner)

*👑 Owner Only*
/givepremium <days> — Premium do (reply)
/cancelpremium — Premium cancel (reply)
/setbal <amount> — Balance set
/gen <amount> — Balance code banao
/bounty <amount> — Bounty code banao"""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, **_reply(update.message.message_id))


# ── Media & AI handlers ────────────────────────────────────────────────────────

async def handle_sticker(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if is_group(update) and not should_respond(update):
        return
    sticker = get_random_sticker()
    if sticker:
        await update.message.reply_sticker(sticker, **_reply(update.message.message_id))
    else:
        await update.message.reply_text("😄", **_reply(update.message.message_id))


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if is_group(update) and not should_respond(update):
        return
    await update.message.reply_text(
        random.choice(["Wow kya photo hai! 😍", "Nice pic! 🔥", "Sahi hai yaar! 😄", "Waah waah! 👌"]),
        **_reply(update.message.message_id),
    )


async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if is_group(update) and not should_respond(update):
        return
    await update.message.reply_text(
        random.choice(["Bhai voice message mat bhejo 😭", "Sunne ka mann nahi 😜", "Text kar yaar 😂"]),
        **_reply(update.message.message_id),
    )


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if is_group(update) and not should_respond(update):
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    if not openai_client:
        await update.message.reply_text("Abhi AI chat available nahi hai! 😅", **_reply(update.message.message_id))
        return
    uid = update.effective_user.id
    history = conv_history[uid]
    history.append({"role": "user", "content": text})
    if len(history) > MAX_HISTORY:
        del history[:len(history) - MAX_HISTORY]
    try:
        await update.effective_chat.send_action("typing")
        resp = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            max_completion_tokens=400,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
        )
        reply = resp.choices[0].message.content or ""
        if reply:
            history.append({"role": "assistant", "content": reply})
            await update.message.reply_text(reply, **_reply(update.message.message_id))
    except Exception as e:
        logger.error(f"AI error: {e}")
        await update.message.reply_text("Thodi si problem aa gayi, ek second ruko! 😬",
                                        **_reply(update.message.message_id))


# ── Startup & main ─────────────────────────────────────────────────────────────

async def post_init(app: Application):
    await db.init_db()
    for pack_name in STICKER_PACK_NAMES:
        try:
            pack = await app.bot.get_sticker_set(pack_name)
            for s in pack.stickers:
                cached_stickers.append(s.file_id)
        except Exception as e:
            logger.warning(f"Sticker pack {pack_name} load failed: {e}")
    logger.info(f"Nami bot ready! Stickers: {len(cached_stickers)}")


def main():
    token = os.environ.get("8114705738:AAFekh_Yt27Jb-wSmNY0slRmQS_sAel-8cg")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN env var not set!")
    app = Application.builder().token(token).post_init(post_init).build()

    handlers = [
        ("start",                cmd_start),
        ("help",                 cmd_help),
        ("bal",                  cmd_bal),
        ("kill",                 cmd_kill),
        ("rob",                  cmd_rob),
        ("protect",              cmd_protect),
        ("daily",                cmd_daily),
        ("select",               cmd_select),
        ("leavejob",             cmd_leavejob),
        ("newship",              cmd_newship),
        ("joinship",             cmd_joinship),
        ("leaveship",            cmd_leaveship),
        ("ship",                 cmd_ship),
        ("toprich",              cmd_toprich),
        ("topkills",             cmd_topkills),
        ("topbounty",            cmd_topbounty),
        ("topships",             cmd_topships),
        ("items",                cmd_items),
        ("item",                 cmd_item),
        ("purchase",             cmd_purchase),
        ("gift",                 cmd_gift),
        ("redeem",               cmd_redeem),
        ("redbounty",            cmd_redbounty),
        ("pay",                  cmd_pay),
        ("check",                cmd_check),
        ("setemoji",             cmd_setemoji),
        ("appointvicecaptain",   cmd_appointvicecaptain),
        ("appointnavigator",     cmd_appointnavigator),
        ("appointofficer",       cmd_appointofficer),
        ("transferleadership",   cmd_transferleadership),
        ("givepremium",          cmd_givepremium),
        ("cancelpremium",        cmd_cancelpremium),
        ("setbal",               cmd_setbal),
        ("gen",                  cmd_gen),
        ("bounty",               cmd_bounty_gen),
        ("promote",              cmd_promote),
        ("demote",               cmd_demote),
        ("pin",                  cmd_pin),
        ("warn",                 cmd_warn),
        ("unwarn",               cmd_unwarn),
        ("mute",                 cmd_mute),
        ("unmute",               cmd_unmute),
        ("kick",                 cmd_kick),
        ("promoteme",            cmd_promoteme),
    ]
    for cmd, fn in handlers:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Starting Nami bot...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
