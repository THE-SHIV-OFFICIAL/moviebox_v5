"""Deliver videos to users with a TMDB poster cover carrying our branding.

Works for already-indexed (old) files too: the file itself is reused by file_id,
only Telegram's video cover is replaced. Any failure falls back to a normal send.
"""
import html
import logging
import re
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# title -> (raw.types.InputPhoto, created_at). Avoids TMDB + upload on every send.
_COVER_CACHE = {}
_MISS_CACHE = {}
_CACHE_TTL = 6 * 3600


def _plain(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return html.unescape(text).split("\n")[0].strip()


async def _build_cover_file(query, workdir):
    from utils import get_tmdb_poster
    from util.thumbnail_branding import _download_url, build_tmdb_cover

    movie = await get_tmdb_poster(query, query)
    if not movie or not movie.get("poster"):
        return None
    poster = await _download_url(movie["poster"], str(Path(workdir) / "poster.jpg"))
    if not poster:
        return None
    backdrop = None
    if movie.get("backdrop"):
        try:
            backdrop = await _download_url(movie["backdrop"], str(Path(workdir) / "backdrop.jpg"))
        except Exception:
            backdrop = None
    out = str(Path(workdir) / "cover.jpg")
    build_tmdb_cover(poster, out, backdrop, 16 / 9, max_side=1280)
    return out


async def _get_cover(client, peer, query):
    from pyrogram import raw
    from util.movie_display import clean_title

    key = clean_title(query)[0].casefold()
    if not key:
        return None
    now = time.time()
    cached = _COVER_CACHE.get(key)
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]
    if now - _MISS_CACHE.get(key, 0) < 1800:
        return None
    with tempfile.TemporaryDirectory(prefix="betabot_cover_") as workdir:
        path = await _build_cover_file(query, workdir)
        if not path:
            _MISS_CACHE[key] = now
            return None
        uploaded = await client.invoke(raw.functions.messages.UploadMedia(
            peer=peer, media=raw.types.InputMediaUploadedPhoto(file=await client.save_file(path))))
    photo = raw.types.InputPhoto(id=uploaded.photo.id, access_hash=uploaded.photo.access_hash,
                                 file_reference=uploaded.photo.file_reference)
    _COVER_CACHE[key] = (photo, now)
    return photo


async def send_with_cover(client, chat_id, file_id, caption=None, protect_content=False,
                          reply_markup=None, file_name=None):
    """Drop-in replacement for client.send_cached_media()."""
    try:
        from pyrogram import raw, types, utils
        from pyrogram.file_id import FileId, FileType

        if FileId.decode(file_id).file_type != FileType.VIDEO:
            raise LookupError("not a video")
        query = file_name or _plain(caption)
        if not query:
            try:
                from database.ia_filterdb import get_file_details
                details = await get_file_details(file_id)
                if details:
                    query = getattr(details[0], "file_name", None) or ""
            except Exception:
                query = ""
        if not query:
            raise LookupError("no title")
        peer = await client.resolve_peer(chat_id)
        cover = await _get_cover(client, peer, query)
        if cover is None:
            raise LookupError("no TMDB cover")
        media = utils.get_input_media_from_file_id(file_id, FileType.VIDEO)
        media.video_cover = cover
        r = await client.invoke(raw.functions.messages.SendMedia(
            peer=peer, media=media, random_id=client.rnd_id(),
            noforwards=protect_content or None,
            reply_markup=await reply_markup.write(client) if reply_markup else None,
            **await utils.parse_text_entities(client, caption or "", None, None),
        ))
        for update in r.updates:
            if isinstance(update, (raw.types.UpdateNewMessage, raw.types.UpdateNewChannelMessage)):
                return await types.Message._parse(
                    client, update.message,
                    {u.id: u for u in r.users}, {c.id: c for c in r.chats})
    except LookupError:
        pass
    except Exception as e:
        if "FILE_REFERENCE" in str(e) or "PHOTO" in str(e):
            _COVER_CACHE.clear()
        logger.warning("Cover send failed, sending normally: %s", e)
    return await client.send_cached_media(
        chat_id=chat_id, file_id=file_id, caption=caption,
        protect_content=protect_content, reply_markup=reply_markup)
