"""RSS/Atom feeds handling"""

import datetime
import functools
import mimetypes
import re
import time
from collections.abc import Iterator
from multiprocessing.pool import ThreadPool
from pathlib import Path
from typing import Optional

import bs4
import feedparser
import requests
from deltachat2 import Bot
from feedparser.datetimes import _parse_date
from feedparser.exceptions import CharacterEncodingOverride
from sqlalchemy import select, update

from .api import process_update
from .cli import cli
from .orm import Feed, session_scope

www = requests.Session()
www.headers.update(
    {
        "user-agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:60.0) Gecko/20100101 Firefox/60.0"
    }
)
www.request = functools.partial(www.request, timeout=15)  # type: ignore


def check_feeds(bot: Bot, interval: int, pool_size: int, app_dir: Path) -> None:
    lastcheck_path = app_dir / "lastcheck.txt"
    lastcheck = 0.0
    if lastcheck_path.exists():
        with lastcheck_path.open(encoding="utf-8") as lastcheck_file:
            try:
                lastcheck = float(lastcheck_file.read())
            except (ValueError, TypeError):
                pass
    took = max(time.time() - lastcheck, 0)

    with ThreadPool(pool_size) as pool:
        while True:
            delay = interval - took
            if delay > 0:
                bot.logger.info(f"[FEEDS] Sleeping for {delay:.1f} seconds")
                time.sleep(delay)
            bot.logger.info("[FEEDS] Starting to check feeds")
            lastcheck = time.time()
            with lastcheck_path.open("w", encoding="utf-8") as lastcheck_file:
                lastcheck_file.write(str(lastcheck))
            with session_scope() as session:
                feeds = session.execute(select(Feed)).scalars().all()
                session.expunge_all()
            bot.logger.info(f"[FEEDS] There are {len(feeds)} feeds to check")
            accid = bot.rpc.get_all_account_ids()[0]
            for _ in pool.imap_unordered(
                lambda f: _check_feed_task(bot, accid, f), feeds
            ):
                pass
            took = time.time() - lastcheck
            bot.logger.info(
                f"[FEEDS] Done checking {len(feeds)} feeds after {took:.1f} seconds"
            )


def _check_feed_task(bot: Bot, accid: int, feed: Feed):
    bot.logger.debug(f"Checking feed: {feed.url}")
    try:
        _check_feed(bot, accid, feed)
    except Exception as err:
        bot.logger.exception(err)
    bot.logger.debug(f"Done checking feed: {feed.url}")


def _check_feed(bot: Bot, accid: int, feed: Feed) -> None:
    d = parse_feed(feed.url, etag=feed.etag, modified=feed.modified)

    if d.entries and feed.latest:
        d.entries = get_new_entries(d.entries, tuple(map(int, feed.latest.split())))
    if not d.entries:
        return

    name = d.feed.get("title") or feed.url
    parsed_entries = parse_entries(d.entries[:100], feed.filter)
    admin_chatid = cli.get_admin_chat(bot.rpc, accid)
    for postid, pubdate, text in parsed_entries:
        data = {
            "id": postid,
            "authorName": name,
            "date": pubdate,
            "active": pubdate,
            "text": text,
            "file": "",
            "filename": "",
            "style": 0,
            "likes": 0,
            "replies": 0,
        }
        upd = {"payload": {"post": data}, "info": f"{name} created a post"}
        process_update(bot, accid, False, admin_chatid, feed.url, upd)

    latest = get_latest_date(d.entries) or feed.latest
    modified = d.get("modified") or d.get("updated")
    with session_scope() as session:
        stmt = update(Feed).where(Feed.url == feed.url)
        session.execute(
            stmt.values(etag=d.get("etag"), modified=modified, latest=latest)
        )


def parse_entries(entries: list, filter_: str) -> Iterator[tuple[str, int, str]]:
    for e in entries:
        postid, pubdate, text = _parse_entry(e)
        if filter_ not in text:
            continue
        if text:
            yield postid, pubdate, text


def _parse_entry(entry) -> tuple:
    title = entry.get("title") or ""
    desc = ""
    if entry.get("content"):
        for c in entry.get("content"):
            if c.get("type") == "text/html":
                desc += c["value"]
    if not desc:
        desc = entry.get("description") or ""

    desc_soup = bs4.BeautifulSoup(desc, "html5lib")
    for tag in desc_soup("br"):
        tag.replace_with("\n")
    for tag in desc_soup("p"):
        tag.replace_with(tag.get_text() + "\n\n")
    desc = desc_soup.get_text().strip()

    if title:
        title_soup = bs4.BeautifulSoup(title.rstrip("."), "html5lib")
        title = " ".join(title_soup.get_text().split())
        if not " ".join(desc.split()).startswith(title):
            desc = (title_soup.get_text().strip() + "\n\n" + desc).strip()

    desc = "\n".join(line.strip() for line in desc.split("\n"))
    desc = re.sub(r"\n{3,}", "\n\n", desc)
    desc = (desc + "\n\n" + entry.get("link")).strip()

    if entry.get("published_parsed"):
        pubdate = time.mktime(entry.get("published_parsed"))
    else:
        pubdate = time.time()

    postid = entry.get("id") or entry.link
    return postid, pubdate * 1000, desc


def get_new_entries(entries: list, date: tuple) -> list:
    new_entries = []
    for e in entries:
        d = e.get("published_parsed") or e.get("updated_parsed")
        if d is not None and d > date:
            new_entries.append(e)
    return new_entries


def get_old_entries(entries: list, date: tuple) -> list:
    old_entries = []
    for e in entries:
        d = e.get("published_parsed") or e.get("updated_parsed")
        if d is not None and d <= date:
            old_entries.append(e)
    return old_entries


def get_latest_date(entries: list) -> Optional[str]:
    dates = []
    for e in entries:
        d = e.get("published_parsed") or e.get("updated_parsed")
        if d:
            dates.append(d)
    return " ".join(map(str, max(dates))) if dates else None


def parse_feed(
    url: str, etag: Optional[str] = None, modified: Optional[tuple] = None
) -> feedparser.FeedParserDict:
    headers = {"A-IM": "feed", "Accept-encoding": "gzip, deflate"}
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        if isinstance(modified, str):
            modified = _parse_date(modified)
        elif isinstance(modified, datetime.datetime):
            modified = modified.utctimetuple()
        short_weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        months = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        headers["If-Modified-Since"] = "%s, %02d %s %04d %02d:%02d:%02d GMT" % (  # noqa
            short_weekdays[modified[6]],
            modified[2],
            months[modified[1] - 1],
            modified[0],
            modified[3],
            modified[4],
            modified[5],
        )
    with www.get(url, headers=headers) as resp:
        resp.raise_for_status()
        dict_ = feedparser.parse(resp.text)
    bozo_exception = dict_.get("bozo_exception", ValueError("Invalid feed"))
    if (
        dict_.get("bozo")
        and not isinstance(bozo_exception, CharacterEncodingOverride)
        and not dict_.get("entries")
    ):
        raise bozo_exception
    return dict_


def get_img_ext(resp: requests.Response) -> str:
    disp = resp.headers.get("content-disposition")
    if disp is not None and re.findall("filename=(.+)", disp):
        fname = re.findall("filename=(.+)", disp)[0].strip('"')
    else:
        fname = resp.url.split("/")[-1].split("?")[0].split("#")[0]
    if "." in fname:
        ext = "." + fname.rsplit(".", maxsplit=1)[-1]
    else:
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype == "image/jpeg":
            ext = ".jpg"
        else:
            ext = mimetypes.guess_extension(ctype)
    return ext
