from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC: the columns are timezone-less, and mixing aware values in
    would break ordering comparisons once SQLite stores the offset suffix."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Track(Base):
    """Metadata snapshot of a YouTube Music track.

    Everything the library exposes (favourites, playlists, history) points here,
    so the library stays browsable even when YouTube Music is unreachable.
    """

    __tablename__ = "tracks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    artist: Mapped[str] = mapped_column(String(512), default="")
    artist_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    album: Mapped[str] = mapped_column(String(512), default="")
    album_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration: Mapped[int] = mapped_column(Integer, default=0)
    thumbnail: Mapped[str | None] = mapped_column(Text, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    track_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disc_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    genre: Mapped[str | None] = mapped_column(String(128), nullable=True)
    explicit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Album(Base):
    __tablename__ = "albums"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), default="")
    artist: Mapped[str] = mapped_column(String(512), default="")
    artist_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    thumbnail: Mapped[str | None] = mapped_column(Text, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    song_count: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[int] = mapped_column(Integer, default=0)
    album_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Artist(Base):
    __tablename__ = "artists"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), default="")
    thumbnail: Mapped[str | None] = mapped_column(Text, nullable=True)
    album_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Star(Base):
    __tablename__ = "stars"
    __table_args__ = (UniqueConstraint("item_id", "item_type", name="uq_star_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(String(64), index=True)
    item_type: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (UniqueConstraint("item_id", "item_type", name="uq_rating_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(String(64), index=True)
    item_type: Mapped[str] = mapped_column(String(16))
    rating: Mapped[int] = mapped_column(Integer, default=0)


class Playlist(Base):
    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(512))
    comment: Mapped[str] = mapped_column(Text, default="")
    public: Mapped[bool] = mapped_column(Boolean, default=False)
    ytm_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    entries: Mapped[list["PlaylistEntry"]] = relationship(
        back_populates="playlist",
        cascade="all, delete-orphan",
        order_by="PlaylistEntry.position",
    )


class PlaylistEntry(Base):
    __tablename__ = "playlist_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer, default=0)

    playlist: Mapped[Playlist] = relationship(back_populates="entries")


class PlayEvent(Base):
    __tablename__ = "play_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[str] = mapped_column(String(64), index=True)
    client: Mapped[str] = mapped_column(String(64), default="")
    played_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    seconds: Mapped[int] = mapped_column(Integer, default=0)


class Setting(Base):
    """User preferences (equaliser, crossfade, sleep timer defaults, …).

    Kept server-side so the same settings apply on iPhone, iPad and desktop.
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class LyricsTranslation(Base):
    __tablename__ = "lyrics_translations"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    video_id: Mapped[str] = mapped_column(String(64), index=True)
    language: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Podcast(Base):
    __tablename__ = "podcasts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    author: Mapped[str] = mapped_column(String(512), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    thumbnail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class StreamCacheEntry(Base):
    __tablename__ = "stream_cache"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    filename: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(64), default="audio/mp4")
    suffix: Mapped[str] = mapped_column(String(16), default="m4a")
    size: Mapped[int] = mapped_column(Integer, default=0)
    bitrate: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_access: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class PlayQueue(Base):
    __tablename__ = "play_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    track_ids: Mapped[str] = mapped_column(Text, default="")
    current: Mapped[str | None] = mapped_column(String(64), nullable=True)
    position: Mapped[float] = mapped_column(Float, default=0)
    changed_by: Mapped[str] = mapped_column(String(64), default="")
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
