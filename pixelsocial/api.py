"""Interaction with the webxdc status updates"""

import json

from deltachat2 import Bot, ChatType, Message, MessageViewtype, SpecialContactId
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .orm import Like, Post, Reply, session_scope
from .util import upgrade_app


def process_update(
    bot: Bot, accid: int, isadmin: bool, admin_chatid: int, chatid: int, update: dict
) -> None:
    payload = update["payload"]
    size = len(json.dumps(payload))
    if size > 1024**2 * 3:
        bot.logger.info(f"ignoring too big update: {size} Bytes")
        return

    if "setName" in payload:
        name = payload["setName"]["name"]
        if chatid == admin_chatid:
            if name:
                bot.rpc.set_chat_name(accid, chatid, name)
        else:
            contid = bot.rpc.get_chat_contacts(accid, chatid)[0]
            bot.rpc.change_contact_name(accid, contid, name)
        return

    if "post" in payload:
        post = payload["post"]
        post["authorId"] = str(chatid)
        post["isAdmin"] = isadmin
        post["likes"] = 0
        post["replies"] = 0
        try:
            with session_scope() as session:
                session.add(
                    Post(
                        id=post["id"],
                        authorname=post["authorName"],
                        authorid=post["authorId"],
                        isadmin=post["isAdmin"],
                        date=post["date"],
                        active=post["active"],
                        text=post["text"],
                        image=post["file"],
                        filename=post["filename"],
                        style=post["style"],
                    )
                )
        except IntegrityError:
            bot.logger.error(f"got new post with existing id: {post['id']}")
            return
    elif "reply" in payload:
        reply = payload["reply"]
        reply["authorId"] = str(chatid)
        reply["isAdmin"] = isadmin
        try:
            with session_scope() as session:
                reply = Reply(
                    id=reply["id"],
                    postid=reply["postId"],
                    authorname=reply["authorName"],
                    authorid=reply["authorId"],
                    isadmin=reply["isAdmin"],
                    date=reply["date"],
                    text=reply["text"],
                    image=reply["file"],
                    filename=reply["filename"],
                    style=reply["style"],
                )
                session.add(reply)
                session.flush()
                if reply.post.active < reply.date:  # noqa
                    reply.post.active = reply.date
        except IntegrityError:
            bot.logger.error(f"got new reply with existing id: {reply['id']}")
            return
    elif "like" in payload:
        like = payload["like"]
        like["userId"] = str(chatid)
        try:
            with session_scope() as session:
                session.add(
                    Like(
                        postid=like["postId"],
                        userid=like["userId"],
                    )
                )
        except IntegrityError:
            return
    elif "unlike" in payload:
        unlike = payload["unlike"]
        unlike["userId"] = str(chatid)
        stmt = select(Like).filter(
            Like.postid == unlike["postId"], Like.userid == unlike["userId"]
        )
        with session_scope() as session:
            like = session.execute(stmt).scalars().first()
            if like:
                session.delete(like)
            else:
                return  # like doesn't exist, ignore
    elif "deleteAll" in payload:
        userid = payload["deleteAll"]
        if not isadmin and userid != str(chatid):
            return  # user doesn't have right to delete
        pstmt = select(Post).filter(Post.authorid == userid)
        rstmt = select(Reply).filter(Reply.authorid == userid)
        with session_scope() as session:
            for post in session.execute(pstmt).scalars():
                session.delete(post)
            for reply in session.execute(rstmt).scalars():
                post = reply.post
                if post.replies[-1].id == reply.id:
                    if len(post.replies) > 1:
                        date = max(post.replies[-2].date, post.date)
                    else:
                        date = post.date
                    if post.active > date:  # noqa
                        post.active = date
                session.delete(reply)
    elif "deleteP" in payload:
        postid = payload["deleteP"]
        if isadmin:
            stmt = select(Post).filter(Post.id == postid)
        else:
            stmt = select(Post).filter(Post.id == postid, Post.authorid == str(chatid))
        with session_scope() as session:
            post = session.execute(stmt).scalars().first()
            if post:
                session.delete(post)
            else:
                return  # user doesn't have right to delete
    elif "deleteR" in payload:
        replyid = payload["deleteR"]["replyId"]
        if isadmin:
            stmt = select(Reply).filter(Reply.id == replyid)
        else:
            stmt = select(Reply).filter(
                Reply.id == replyid, Reply.authorid == str(chatid)
            )
        with session_scope() as session:
            reply = session.execute(stmt).scalars().first()
            if reply:
                post = reply.post
                if post.replies[-1].id == reply.id:
                    if len(post.replies) > 1:
                        date = max(post.replies[-2].date, post.date)
                    else:
                        date = post.date
                    if post.active > date:  # noqa
                        post.active = date
                session.delete(reply)
            else:
                return  # user doesn't have right to delete
    else:
        bot.logger.info(f"Unknown payload: {payload}")
        return

    payload["is_bot"] = True

    update = {"payload": payload, "info": update.get("info")}
    broadcast(bot, accid, admin_chatid, chatid, json.dumps(update))


def broadcast(
    bot: Bot, accid: int, admin_chatid: int, sender_chat: int, update: str
) -> None:
    chats = bot.rpc.get_chatlist_entries(accid, None, None, None)
    for chatid in chats:
        if chatid == sender_chat:
            continue
        chat = bot.rpc.get_full_chat_by_id(accid, chatid)
        if chat.id != admin_chatid and chat.chat_type != ChatType.SINGLE:
            continue

        msg = get_app_msg(bot, accid, chatid)
        if msg:
            msgid = upgrade_app(bot, accid, admin_chatid, chatid, msg.id)
            bot.rpc.send_webxdc_status_update(accid, msgid or msg.id, update, "")


def get_app_msg(bot: Bot, accid: int, chatid: int) -> Message | None:
    msgids = bot.rpc.get_chat_media(accid, chatid, MessageViewtype.WEBXDC, None, None)
    for msgid in reversed(msgids):
        msg = bot.rpc.get_message(accid, msgid)
        if msg.from_id == SpecialContactId.SELF:
            return msg
        bot.rpc.delete_messages(accid, [msgid])

    return None
