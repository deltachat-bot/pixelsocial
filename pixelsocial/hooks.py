"""Event handlers and hooks"""

import json
from argparse import Namespace
from pathlib import Path
from threading import Thread

from deltabot_cli import BotCli
from deltachat2 import (
    Bot,
    ChatType,
    CoreEvent,
    EventType,
    MessageViewtype,
    MsgData,
    NewMsgEvent,
    SpecialContactId,
    events,
)
from rich.logging import RichHandler
from sqlalchemy import select

from ._version import __version__
from .api import process_update
from .feeds import check_feeds, parse_feed
from .orm import Feed, init, session_scope
from .util import delete_old, normalize_url, send_app, upgrade_app

cli = BotCli("pixelsocial")
cli.add_generic_option("-v", "--version", action="version", version=__version__)
cli.add_generic_option(
    "--interval",
    type=int,
    default=60 * 5,
    help="how many seconds to sleep before checking the feeds again (default: %(default)s)",
)
cli.add_generic_option(
    "--parallel",
    type=int,
    default=10,
    help="how many feeds to check in parallel (default: %(default)s)",
)
cli.add_generic_option(
    "--no-time",
    help="do not display date timestamp in log messages",
    action="store_false",
)

HELP = (
    "I am a bot that allows you to interact in PixelSocial"
    " social network.\n\nSource code: "
    "https://github.com/deltachat-bot/pixelsocial"
)


@cli.on_init
def on_init(bot: Bot, args: Namespace) -> None:
    bot.logger.handlers = [
        RichHandler(show_path=False, omit_repeated_times=False, show_time=args.no_time)
    ]
    for accid in bot.rpc.get_all_account_ids():
        if not bot.rpc.get_config(accid, "displayname"):
            bot.rpc.set_config(accid, "displayname", "PixelSocial")
            bot.rpc.set_config(accid, "selfstatus", HELP)


@cli.on_start
def on_start(bot: Bot, args: Namespace) -> None:
    config_dir = Path(args.config_dir)
    init(f"sqlite:///{config_dir / 'sqlite.db'}")
    Thread(
        target=check_feeds,
        args=(cli, bot, args.interval, args.parallel, config_dir),
        daemon=True,
    ).start()
    Thread(target=delete_old, args=(bot,), daemon=True).start()


@cli.on(events.RawEvent)
def log_event(bot: Bot, accid: int, event: CoreEvent) -> None:
    if event.kind == EventType.INFO:
        bot.logger.debug(event.msg)
    elif event.kind == EventType.WARNING:
        bot.logger.warning(event.msg)
    elif event.kind == EventType.ERROR:
        bot.logger.error(event.msg)
    elif event.kind == EventType.WEBXDC_STATUS_UPDATE:
        msgid = event.msg_id
        serial = event.status_update_serial
        on_status_update(bot, accid, msgid, serial)
    elif event.kind == EventType.SECUREJOIN_INVITER_PROGRESS:
        if event.progress == 1000:
            if not bot.rpc.get_contact(accid, event.contact_id).is_bot:
                bot.logger.debug("QR scanned by contact id=%s", event.contact_id)
                chatid = bot.rpc.create_chat_by_contact_id(accid, event.contact_id)
                admin_chatid = cli.get_admin_chat(bot.rpc, accid)
                send_app(bot, accid, admin_chatid, chatid)


@cli.after(events.NewMessage)
def delete_msgs(bot: Bot, accid: int, event: NewMsgEvent) -> None:
    bot.rpc.delete_messages(accid, [event.msg.id])


@cli.on(events.NewMessage(is_info=False))
def on_msg(bot: Bot, accid: int, event: NewMsgEvent) -> None:
    """send the webxdc app on every 1:1 (private) message"""
    if bot.has_command(event.command):
        return

    msg = event.msg
    chatid = msg.chat_id
    chat = bot.rpc.get_basic_chat_info(accid, chatid)
    if chat.chat_type != ChatType.SINGLE:
        return
    bot.rpc.markseen_msgs(accid, [msg.id])
    admin_chatid = cli.get_admin_chat(bot.rpc, accid)
    send_app(bot, accid, admin_chatid, chatid)


@cli.on(events.NewMessage(command="/help"))
def _help(bot: Bot, accid: int, event: NewMsgEvent) -> None:
    msg = event.msg
    bot.rpc.markseen_msgs(accid, [msg.id])
    text = HELP + (
        "\n\n**Available commands**\n\n"
        "/start - Join the social network\n\n"
        "/stop  - Log out of the social network, stop receiving updates\n\n"
    )
    if msg.chat_id == cli.get_admin_chat(bot.rpc, accid):
        text += (
            "/sub URL [filter] - Subscribe to the given feed."
            " If a filter is provided, post that don't match the filter will be ignored\n\n"
            "/unsub URL - Unsubscribe from the given feed."
        )
    bot.rpc.send_msg(accid, msg.chat_id, MsgData(text=text))


@cli.on(events.NewMessage(command="/start"))
def _start(bot: Bot, accid: int, event: NewMsgEvent) -> None:
    msg = event.msg
    chatid = msg.chat_id
    chat = bot.rpc.get_basic_chat_info(accid, chatid)
    admin_chatid = cli.get_admin_chat(bot.rpc, accid)
    if chat.chat_type != ChatType.SINGLE:
        if chat.id != admin_chatid:
            return
    bot.rpc.markseen_msgs(accid, [msg.id])
    send_app(bot, accid, admin_chatid, chatid)


@cli.on(events.NewMessage(command="/stop"))
def _stop(bot: Bot, accid: int, event: NewMsgEvent) -> None:
    msg = event.msg
    chatid = msg.chat_id
    bot.rpc.markseen_msgs(accid, [msg.id])
    msgids = bot.rpc.get_chat_media(accid, chatid, MessageViewtype.WEBXDC, None, None)
    for msgid in msgids:
        msg = bot.rpc.get_message(accid, msgid)
        if msg.from_id == SpecialContactId.SELF:
            bot.rpc.delete_messages_for_all(accid, [msgid])
    text = "Done, you logged out. To log in again send: /start"
    bot.rpc.send_msg(accid, chatid, MsgData(text=text))


@cli.on(events.NewMessage(command="/sub"))
def _sub(bot: Bot, accid: int, event: NewMsgEvent) -> None:
    msg = event.msg
    chatid = msg.chat_id
    bot.rpc.markseen_msgs(accid, [msg.id])

    if chatid != cli.get_admin_chat(bot.rpc, accid):
        reply = MsgData(
            text="❌ That command can only be used in the admin chat.",
            quoted_message_id=msg.id,
        )
        bot.rpc.send_msg(accid, msg.chat_id, reply)
        return

    args = event.payload.split(maxsplit=1)
    url = normalize_url(args[0]) if args else ""
    filter_ = args[1] if len(args) == 2 else ""

    try:
        parse_feed(url)
    except Exception:
        reply = MsgData(
            text="❌ Invalid feed url.",
            quoted_message_id=msg.id,
        )
        bot.rpc.send_msg(accid, msg.chat_id, reply)
        return

    with session_scope() as session:
        feed = session.execute(select(Feed).where(Feed.url == url)).scalar()
        if feed:
            reply = MsgData(
                text="❌ Feed already exists.",
                quoted_message_id=msg.id,
            )
            bot.rpc.send_msg(accid, msg.chat_id, reply)
            return
        feed = Feed(
            url=url,
            etag="",
            modified="",
            latest="",
            filter=filter_,
        )
        session.add(feed)

    reply = MsgData(
        text="✅ Subscribed to feed.",
        quoted_message_id=msg.id,
    )
    bot.rpc.send_msg(accid, msg.chat_id, reply)


@cli.on(events.NewMessage(command="/unsub"))
def _unsub(bot: Bot, accid: int, event: NewMsgEvent) -> None:
    msg = event.msg
    chatid = msg.chat_id
    bot.rpc.markseen_msgs(accid, [msg.id])

    if chatid != cli.get_admin_chat(bot.rpc, accid):
        reply = MsgData(
            text="❌ That command can only be used in the admin chat.",
            quoted_message_id=msg.id,
        )
        bot.rpc.send_msg(accid, msg.chat_id, reply)
        return

    if not event.payload:
        with session_scope() as session:
            feeds = session.execute(select(Feed)).scalars()
            text = "\n\n".join(feed.url for feed in feeds)
        reply = MsgData(text=text or "❌ No feed subscriptions")
        bot.rpc.send_msg(accid, msg.chat_id, reply)
        return

    with session_scope() as session:
        stmt = select(Feed).where(Feed.url == normalize_url(event.payload))
        feed = session.execute(stmt).scalar()
        if feed:
            session.delete(feed)
            reply = MsgData(text=f"Unsubscribed from: {feed.url}")
            bot.rpc.send_msg(accid, msg.chat_id, reply)
        else:
            reply = MsgData(
                text="❌ You are not subscribed to that feed.",
                quoted_message_id=msg.id,
            )
            bot.rpc.send_msg(accid, msg.chat_id, reply)


def on_status_update(bot: Bot, accid: int, msgid: int, serial: int) -> None:
    admin = cli.get_admin_chat(bot.rpc, accid)
    update = json.loads(bot.rpc.get_webxdc_status_updates(accid, msgid, serial - 1))[0]
    payload = update["payload"]
    if not payload.get("is_bot"):
        msg = bot.rpc.get_message(accid, msgid)
        chatid = msg.chat_id
        if msg.from_id == SpecialContactId.SELF and not upgrade_app(
            bot, accid, admin, chatid, msgid
        ):

            isadmin = chatid == admin
            if not isadmin:
                chat = bot.rpc.get_basic_chat_info(accid, chatid)
                if chat.chat_type == ChatType.SINGLE:
                    contactid = bot.rpc.get_chat_contacts(accid, chatid)[0]
                    isadmin = cli.is_admin(bot.rpc, accid, contactid)
            process_update(bot, accid, isadmin, admin, chatid, update)
