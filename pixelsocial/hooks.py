"""Event handlers and hooks"""

from argparse import Namespace
from pathlib import Path

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

from ._version import __version__
from .api import process_update
from .orm import init
from .util import send_app

cli = BotCli("pixelsocial")
cli.add_generic_option("-v", "--version", action="version", version=__version__)
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
def _on_start(_bot: Bot, args: Namespace) -> None:
    path = Path(args.config_dir) / "sqlite.db"
    init(f"sqlite:///{path}")


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
        admin = cli.get_admin_chat(bot.rpc, accid)
        process_update(bot, accid, admin, msgid, serial)
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
    admin_chatid = cli.get_admin_chat(bot.rpc, accid)
    if chat.chat_type != ChatType.SINGLE:
        if chat.id != admin_chatid:
            return
    bot.rpc.markseen_msgs(accid, [msg.id])
    send_app(bot, accid, admin_chatid, chatid)


@cli.on(events.NewMessage(command="/help"))
def _help(bot: Bot, accid: int, event: NewMsgEvent) -> None:
    msg = event.msg
    bot.rpc.markseen_msgs(accid, [msg.id])
    text = (
        HELP
        + "\n\nCommands:\n\n/start join the social network\n\n"
        + "/stop log out of the social network, stop receiving updates"
    )
    bot.rpc.send_msg(accid, msg.chat_id, MsgData(text=HELP))


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
