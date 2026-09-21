# ============================================================
# PREMIUM USERBOT HOSTING SERVICE
# ============================================================
# Commandes :
# /start
# /host       -> affiche un bouton pour partager le numéro Telegram
# /otp 12345  -> valide le code OTP
# /password   -> valide le mot de passe 2FA
# /status     -> statut des Userbots
# /verify     -> vérifie les comptes
# /logout     -> déconnecte un compte
# /cancel     -> annule la connexion en cours
# /help       -> aide
#
# IMPORTANT :
# - Le bouton "📱 Utiliser mon numéro" partage le numéro du compte
#   Telegram de l'utilisateur avec le bot.
# - Telegram n'autorise pas un KeyboardButton à faire saisir
#   directement un numéro arbitraire : request_contact partage le
#   numéro du compte Telegram qui appuie sur le bouton.
# - Les OTP et mots de passe 2FA ne sont pas enregistrés dans les logs.
# - Les sessions Telethon sont stockées dans ./sessions.
#
# VARIABLES D'ENVIRONNEMENT :
# BOT_TOKEN = token BotFather
# API_ID    = API ID Telegram
# API_HASH  = API Hash Telegram
#
# Installation :
# pip install -r requirements.txt
# python userbot_hosting.py
# ============================================================

import asyncio
import hashlib
import os
import re
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

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant dans les variables d'environnement.")

if not API_ID_RAW or not API_ID_RAW.isdigit():
    raise RuntimeError("API_ID manquant ou invalide.")

if not API_HASH:
    raise RuntimeError("API_HASH manquant dans les variables d'environnement.")

API_ID = int(API_ID_RAW)

BASE_DIR = Path(__file__).resolve().parent
SESSION_DIR = BASE_DIR / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)

# Comptes actuellement chargés en mémoire.
# La prochaine étape pourra ajouter une base de données persistante.
accounts: Dict[int, Dict[str, Any]] = {}

# Connexions OTP temporaires.
pending: Dict[int, Dict[str, Any]] = {}

# Verrous par utilisateur.
user_locks: Dict[int, asyncio.Lock] = {}


# ---------------- SERVEUR WEB RENDER ----------------

web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "Userbot Hosting Service - OK", 200


@web_app.route("/health")
def health():
    return "ONLINE", 200


def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)


# ---------------- OUTILS ----------------

def get_lock(user_id: int) -> asyncio.Lock:
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


def safe_phone(phone: str) -> str:
    phone = (
        phone.strip()
        .replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
    )

    if not re.fullmatch(r"\+[1-9]\d{6,14}", phone):
        raise ValueError(
            "Numéro invalide. Utilisez le format international, "
            "par exemple +22890123456."
        )

    return phone


def account_key(phone: str) -> str:
    return hashlib.sha256(phone.encode()).hexdigest()[:20]


def session_path(user_id: int, phone: str) -> str:
    return str(SESSION_DIR / f"{user_id}_{account_key(phone)}")


async def delete_message(update: Update):
    try:
        if update.message:
            await update.message.delete()
    except Exception:
        pass


def phone_keyboard():
    """
    Bouton Telegram natif permettant à l'utilisateur de partager
    le numéro associé à son propre compte Telegram.
    """
    button = KeyboardButton(
        text="📱 Utiliser mon numéro",
        request_contact=True,
    )

    return ReplyKeyboardMarkup(
        [[button]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ---------------- COMMANDES ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔥 <b>PREMIUM USERBOT HOSTING</b>\n\n"
        "Hébergez vos comptes Telegram avec notre service.\n\n"
        "🔐 Connexion OTP\n"
        "👥 Multi-compte\n"
        "📊 Statut\n"
        "🚪 Déconnexion\n\n"
        "Utilisez /help pour voir les commandes."
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📚 <b>COMMANDES</b>\n\n"
        "🚀 <code>/host</code>\n"
        "Commencer l'hébergement d'un compte.\n\n"
        "🔐 <code>/verify</code>\n"
        "Vérifier votre compte.\n\n"
        "📊 <code>/status</code>\n"
        "Voir le statut de vos Userbots.\n\n"
        "🚪 <code>/logout</code>\n"
        "Déconnecter un Userbot.\n\n"
        "❌ <code>/cancel</code>\n"
        "Annuler la demande en cours.\n\n"
        "🔑 <code>/password MOT_DE_PASSE</code>\n"
        "Entrer le mot de passe 2FA lorsqu'il est demandé.\n\n"
        "📲 Pendant /host, un bouton vous permet de partager "
        "le numéro de votre compte Telegram."
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )


async def host_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id in pending:
        await update.message.reply_text(
            "⚠️ Une connexion est déjà en cours.\n"
            "Partagez votre numéro ou utilisez /cancel."
        )
        return

    await update.message.reply_text(
        "📱 <b>Numéro Telegram</b>\n\n"
        "Appuyez sur le bouton ci-dessous pour partager "
        "le numéro du compte que vous souhaitez héberger.\n\n"
        "🔒 Votre numéro est utilisé uniquement pour démarrer "
        "la connexion Telegram.",
        parse_mode=ParseMode.HTML,
        reply_markup=phone_keyboard(),
    )


async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Reçoit le numéro partagé avec le bouton Telegram.
    On vérifie que le contact partagé appartient bien à l'utilisateur
    qui appuie sur le bouton.
    """
    user_id = update.effective_user.id
    contact = update.message.contact

    if not contact:
        return

    if contact.user_id is not None and contact.user_id != user_id:
        await update.message.reply_text(
            "❌ Veuillez utiliser le bouton pour partager votre propre numéro.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    phone = contact.phone_number

    if not phone.startswith("+"):
        phone = "+" + phone

    try:
        phone = safe_phone(phone)
    except ValueError:
        await update.message.reply_text(
            "❌ Le numéro reçu est invalide.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await update.message.reply_text(
        "📲 Numéro reçu.\n\n"
        "⏳ Envoi du code OTP...",
        reply_markup=ReplyKeyboardRemove(),
    )

    await start_host_with_phone(update, user_id, phone)


async def start_host_with_phone(
    update: Update,
    user_id: int,
    phone: str,
):
    async with get_lock(user_id):
        if user_id in pending:
            await update.effective_chat.send_message(
                "⚠️ Une connexion est déjà en cours."
            )
            return

        # Évite les doublons.
        for data in accounts.get(user_id, {}).values():
            if data.get("phone") == phone:
                await update.effective_chat.send_message(
                    "ℹ️ Ce compte est déjà hébergé."
                )
                return

        client = TelegramClient(
            session_path(user_id, phone),
            API_ID,
            API_HASH,
        )

        try:
            await client.connect()

            result = await client.send_code_request(phone)

            pending[user_id] = {
                "client": client,
                "phone": phone,
                "phone_code_hash": result.phone_code_hash,
            }

            await update.effective_chat.send_message(
                "📲 <b>Code OTP envoyé.</b>\n\n"
                "Entrez le code reçu par Telegram avec :\n\n"
                "<code>/otp 12345</code>\n\n"
                "🔐 Si la 2FA est activée, le bot vous demandera "
                "ensuite votre mot de passe.\n\n"
                "❌ Pour annuler : /cancel",
                parse_mode=ParseMode.HTML,
            )

        except Exception as e:
            try:
                await client.disconnect()
            except Exception:
                pass

            await update.effective_chat.send_message(
                "❌ Impossible d'envoyer le code Telegram.\n"
                "Vérifiez votre numéro et réessayez.\n\n"
                f"<code>{type(e).__name__}</code>",
                parse_mode=ParseMode.HTML,
            )


async def host_with_argument(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Compatibilité avec l'ancienne syntaxe /host +XXXXXXXX.
    Le nouveau parcours recommandé est /host puis le bouton.
    """
    if not context.args:
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
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "❌ Utilisation : <code>/otp 12345</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    code = "".join(context.args).replace(" ", "")

    # Suppression immédiate du message OTP.
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
            "✅ <b>Compte connecté avec succès !</b>\n\n"
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
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "❌ Utilisation : <code>/password VOTRE_MOT_DE_PASSE</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    password = " ".join(context.args)

    # Supprime immédiatement le message contenant le mot de passe.
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
            "✅ <b>Compte connecté avec succès !</b>\n\n"
            "Votre Userbot est maintenant hébergé.\n\n"
            "📊 Utilisez /status.",
            parse_mode=ParseMode.HTML,
        )

    except PasswordHashInvalidError:
        await update.effective_chat.send_message(
            "❌ Mot de passe 2FA incorrect."
        )

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
                " ".join(
                    x for x in [me.first_name, me.last_name] if x
                )
                or me.username
                or str(me.id)
            ),
            "username": me.username,
            "telegram_id": me.id,
            "client": client,
        }

        pending.pop(user_id, None)

        try:
            await client.send_message(
                "me",
                "✅ Votre compte est maintenant connecté au service Userbot Hosting."
            )
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
    user_id = update.effective_user.id
    user_accounts = accounts.get(user_id, {})

    if not user_accounts:
        await update.message.reply_text(
            "📊 <b>STATUT</b>\n\n"
            "Aucun Userbot actuellement connecté.\n\n"
            "Utilisez /host pour commencer.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = ["📊 <b>STATUT DE VOS USERBOTS</b>\n"]

    for index, data in enumerate(user_accounts.values(), 1):
        client: TelegramClient = data["client"]

        try:
            connected = client.is_connected()
            authorized = (
                connected
                and await client.is_user_authorized()
            )
        except Exception:
            authorized = False

        state = "🟢 EN LIGNE" if authorized else "🔴 HORS LIGNE"

        username = (
            f"@{data['username']}"
            if data.get("username")
            else "sans username"
        )

        lines.append(
            f"{index}. {state}\n"
            f"   👤 {data.get('name', 'Compte')}\n"
            f"   📱 {data['phone']}\n"
            f"   🔗 {username}\n"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


async def verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_accounts = accounts.get(user_id, {})

    if not user_accounts:
        await update.message.reply_text(
            "❌ Aucun compte à vérifier."
        )
        return

    lines = ["🔐 <b>VÉRIFICATION</b>\n"]

    for data in user_accounts.values():
        client: TelegramClient = data["client"]

        try:
            me = await client.get_me()
            authorized = await client.is_user_authorized()

            if authorized:
                lines.append(
                    f"✅ {data['phone']} — connecté\n"
                    f"   ID : <code>{me.id}</code>"
                )
            else:
                lines.append(
                    f"❌ {data['phone']} — session non autorisée"
                )

        except Exception as e:
            lines.append(
                f"⚠️ {data['phone']} — vérification impossible "
                f"({type(e).__name__})"
            )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_accounts = accounts.get(user_id, {})

    if not user_accounts:
        await update.message.reply_text(
            "ℹ️ Aucun Userbot connecté."
        )
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
            await update.message.reply_text(
                "❌ Ce compte n'est pas hébergé."
            )
            return

        await logout_one(user_id, key, data)

        await update.message.reply_text(
            f"🚪 Compte {phone} déconnecté."
        )
        return

    if len(user_accounts) == 1:
        key, data = next(iter(user_accounts.items()))
        phone = data["phone"]

        await logout_one(user_id, key, data)

        await update.message.reply_text(
            f"🚪 Compte {phone} déconnecté."
        )
        return

    text = (
        "🚪 <b>LOGOUT</b>\n\n"
        "Vous avez plusieurs comptes.\n"
        "Choisissez le numéro avec :\n"
        "<code>/logout +XXXXXXXXXXX</code>\n\n"
        + "\n".join(
            f"• {d['phone']}"
            for d in user_accounts.values()
        )
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )


async def logout_one(
    user_id: int,
    key: str,
    data: Dict[str, Any],
):
    client: TelegramClient = data["client"]

    try:
        if client.is_connected():
            await client.log_out()
    except Exception:
        try:
            await client.disconnect()
        except Exception:
            pass

    # Supprime les fichiers de session après logout.
    try:
        session_base = Path(data["session"])

        candidates = [
            session_base,
            Path(str(session_base) + "-journal"),
        ]

        # Telethon peut créer un fichier .session.
        candidates.append(
            Path(str(session_base) + ".session")
        )

        for path in candidates:
            if path.exists():
                path.unlink()

    except Exception:
        pass

    user_accounts = accounts.get(user_id, {})
    user_accounts.pop(key, None)

    if not user_accounts:
        accounts.pop(user_id, None)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in pending:
        await update.message.reply_text(
            "ℹ️ Aucune demande en cours."
        )
        return

    await cleanup_pending(user_id)

    await update.message.reply_text(
        "❌ Demande annulée.\n\n"
        "Vous pouvez recommencer avec /host."
    )


# ---------------- TELEGRAM ----------------

async def post_init(application: Application):
    commands = [
        ("start", "Démarrer"),
        ("host", "Héberger un Userbot"),
        ("status", "Voir le statut"),
        ("verify", "Vérifier le compte"),
        ("logout", "Déconnecter un Userbot"),
        ("cancel", "Annuler la demande"),
        ("help", "Afficher l'aide"),
    ]

    await application.bot.set_my_commands(commands)


async def post_shutdown(application: Application):
    for user_accounts in list(accounts.values()):
        for data in list(user_accounts.values()):
            try:
                await data["client"].disconnect()
            except Exception:
                pass

    for user_id in list(pending.keys()):
        await cleanup_pending(user_id)


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    # /host peut être utilisé seul ou avec un ancien numéro.
    app.add_handler(CommandHandler("host", host_with_argument))

    app.add_handler(CommandHandler("otp", otp_command))
    app.add_handler(CommandHandler("password", password_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("verify", verify_command))
    app.add_handler(CommandHandler("logout", logout_command))
    app.add_handler(CommandHandler("cancel", cancel_command))

    # Réception du numéro via le bouton Telegram.
    app.add_handler(
        MessageHandler(
            filters.CONTACT,
            contact_handler,
        )
    )

    print("🔥 Premium Userbot Hosting Service démarré.")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    Thread(
        target=start_web_server,
        daemon=True,
    ).start()

    main()