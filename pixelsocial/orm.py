"""database"""

from contextlib import contextmanager
from threading import Lock
from typing import Any

from sqlalchemy import Column, ForeignKey, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

Base: Any = declarative_base()
_session = sessionmaker()
_lock = Lock()


class Feed(Base):
    """An RSS/Atom feed"""

    __tablename__ = "feed"
    url = Column(String, primary_key=True)
    etag = Column(String)
    modified = Column(String)
    latest = Column(String)
    filter = Column(String)


class Post(Base):
    """An user post."""

    __tablename__ = "post"
    id = Column(String, primary_key=True)
    authorname = Column(String)
    authorid = Column(String)
    isadmin = Column(Integer)
    date = Column(Integer)
    active = Column(Integer)
    text = Column(String)
    image = Column(String)
    filename = Column(String)
    style = Column(Integer)
    likes = relationship("Like", backref="post", cascade="all, delete, delete-orphan")
    replies = relationship(
        "Reply", backref="post", cascade="all, delete, delete-orphan"
    )


class Like(Base):
    """An user like to post."""

    __tablename__ = "like"
    postid = Column(String, ForeignKey("post.id"), primary_key=True)
    userid = Column(String, primary_key=True)


class Reply(Base):
    """An user reply to post."""

    __tablename__ = "reply"
    id = Column(String, primary_key=True)
    postid = Column(String, ForeignKey("post.id"))
    authorname = Column(String)
    authorid = Column(String)
    isadmin = Column(Integer)
    date = Column(Integer)
    text = Column(String)
    image = Column(String)
    filename = Column(String)
    style = Column(Integer)


@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations."""
    with _lock:
        session = _session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def init(path: str, debug: bool = False) -> None:
    """Initialize engine."""
    engine = create_engine(path, echo=debug)
    Base.metadata.create_all(engine)
    _session.configure(bind=engine)
