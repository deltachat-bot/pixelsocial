"""utilities"""

import base64
import json
import time
from pathlib import Path

from deltachat2 import Bot, MessageViewtype, SpecialContactId
from sqlalchemy import delete, select

from .cli import cli
from .orm import Post, session_scope

APP_VERSION = "0.15.0"
XDC_PATH = str(Path(__file__).parent / "app.xdc")


def upgrade_app(
    bot: Bot, accid: int, admin_chatid: int, chatid: int, msgid: int
) -> int:
    enc_data = bot.rpc.get_webxdc_blob(accid, msgid, "manifest.toml")
    version = ""
    for line in decode_base64(enc_data).decode().splitlines():
        if line.startswith("version"):
            version = line.replace('"', "").split("=")[1].strip()
            break
    if version != APP_VERSION:
        return send_app(bot, accid, admin_chatid, chatid)
    return 0


def send_app(bot: Bot, accid: int, admin_chatid: int, chatid: int) -> int:
    msgids = bot.rpc.get_chat_media(accid, chatid, MessageViewtype.WEBXDC, None, None)
    for msgid in msgids:
        msg = bot.rpc.get_message(accid, msgid)
        if msg.from_id == SpecialContactId.SELF:
            bot.rpc.delete_messages_for_all(accid, [msgid])

    text = "To log out, send /stop"
    bot.rpc.misc_set_draft(accid, chatid, text, XDC_PATH, None, None, None)
    msgid = bot.rpc.get_draft(accid, chatid).id
    mode = {"selfId": str(chatid), "isAdmin": False}
    if chatid == admin_chatid:
        mode["isAdmin"] = True
        chat = bot.rpc.get_basic_chat_info(accid, admin_chatid)
        mode["selfName"] = chat.name
    else:
        contid = bot.rpc.get_chat_contacts(accid, chatid)[0]
        mode["isAdmin"] = cli.is_admin(bot.rpc, accid, contid)
        contact = bot.rpc.get_contact(accid, contid)
        if contact.auth_name != contact.display_name:
            mode["selfName"] = contact.display_name
    send_update(bot, accid, msgid, {"botMode": mode}, APP_VERSION)

    stmt = select(Post).order_by(Post.active.desc()).limit(100)
    with session_scope() as session:
        for post in session.execute(stmt).scalars():
            data = {
                "id": post.id,
                "authorName": post.authorname,
                "authorId": post.authorid,
                "isAdmin": post.isadmin,
                "date": post.date,
                "active": post.active,
                "text": post.text,
                "file": post.image,
                "filename": post.filename or "",
                "style": post.style,
                "likes": 0,
                "replies": 0,
            }
            send_update(bot, accid, msgid, {"post": data})

            for reply in post.replies[-100:]:
                data = {
                    "id": reply.id,
                    "postId": reply.postid,
                    "authorName": reply.authorname,
                    "authorId": reply.authorid,
                    "isAdmin": reply.isadmin,
                    "date": reply.date,
                    "text": reply.text,
                    "file": reply.image or "",
                    "filename": reply.filename or "",
                    "style": reply.style or 0,
                }
                send_update(bot, accid, msgid, {"reply": data})

            for like in post.likes:
                data = {
                    "postId": like.postid,
                    "userId": like.userid,
                }
                send_update(bot, accid, msgid, {"like": data})

    bot.rpc.misc_send_draft(accid, chatid)
    return msgid


def send_update(bot: Bot, accid: int, msgid: int, payload: dict, summary="") -> None:
    payload["is_bot"] = True
    update = {"payload": payload}
    if summary:
        update["summary"] = summary
    bot.rpc.send_webxdc_status_update(accid, msgid, json.dumps(update), "")


def decode_base64(input_string: str) -> bytes:
    """Decode an unpadded standard or urlsafe base64 string to bytes."""

    input_bytes = input_string.encode("ascii")
    input_len = len(input_bytes)
    padding = b"=" * (3 - ((input_len + 3) % 4))

    # Passing altchars here allows decoding both standard and urlsafe base64
    output_bytes = base64.b64decode(input_bytes + padding, altchars=b"-_")
    return output_bytes


def normalize_url(url: str) -> str:
    if not url.startswith("http"):
        url = "https://" + url
    return url.rstrip("/")


def delete_old(bot: Bot) -> None:
    bot.logger.info("[CLEANER] Deleting old posts")
    olddate = (time.time() - (60 * 60 * 24 * 360 * 2)) * 1000
    while True:
        try:
            stmt = delete(Post).where(Post.active < olddate)
            with session_scope() as session:
                count = session.execute(stmt).rowcount
            bot.logger.info(f"[CLEANER] Old posts deleted: {count}")
        except Exception as err:
            bot.logger.exception(err)
        time.sleep(60 * 60 * 24)
