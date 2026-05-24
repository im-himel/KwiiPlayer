import time
import math
import logging
import mimetypes
import traceback
from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine
from FileStream.bot import multi_clients, work_loads, FileStream
from FileStream.config import Telegram, Server
from FileStream.server.exceptions import FIleNotFound, InvalidHash
from FileStream import utils, StartTime, __version__
from FileStream.utils.render_template import render_page, render_embed_page

routes = web.RouteTableDef()

@routes.get("/status", allow_head=True)
async def root_route_handler(_):
    return web.json_response(
        {
            "server_status": "running",
            "uptime": utils.get_readable_time(time.time() - StartTime),
            "telegram_bot": "@" + FileStream.username,
            "connected_bots": len(multi_clients),
            "loads": dict(
                ("bot" + str(c + 1), l)
                for c, (_, l) in enumerate(
                    sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
                )
            ),
            "version": __version__,
        }
    )

@routes.get("/watch/{path}", allow_head=True)
async def watch_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        return web.Response(text=await render_page(path), content_type='text/html')
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except (AttributeError, BadStatusLine, ConnectionResetError):
        pass



@routes.get("/eml/{path}", allow_head=True)
async def embed_handler(request: web.Request):
    """
    Minimal full-screen Video.js embed player for iframe use.
    URL:  GET /eml/{path}
    """
    try:
        path = request.match_info["path"]
        html = await render_embed_page(path)
        return web.Response(
            text=html,
            content_type="text/html",
            headers={
                "X-Frame-Options": "ALLOWALL",
                "Content-Security-Policy": "frame-ancestors *",
                "Access-Control-Allow-Origin": "*",
            }
        )
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except (AttributeError, BadStatusLine, ConnectionResetError):
        pass


@routes.get("/dl/{path}", allow_head=True)
async def dl_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        return await media_streamer(request, path)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except (AttributeError, BadStatusLine, ConnectionResetError):
        pass
    except Exception as e:
        traceback.print_exc()
        logging.critical(e.with_traceback(None))
        logging.debug(traceback.format_exc())
        raise web.HTTPInternalServerError(text=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# HLS ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@routes.get("/hls/{db_id}/playlist.m3u8", allow_head=True)
async def hls_playlist_handler(request: web.Request):
    """
    Returns an HLS Master Playlist (.m3u8) that points back to this server's
    /dl/ endpoint.  ExoPlayer / Video.js / any HLS player can use this URL
    directly — it behaves like a real HLS stream with proper byte-range support.

    URL:  GET /hls/{db_id}/playlist.m3u8
    """
    try:
        db_id = request.match_info["db_id"]

        # Validate the file exists and fetch its size / mime
        index = min(work_loads, key=work_loads.get)
        faster_client = multi_clients[index]

        tg_connect = utils.ByteStreamer(faster_client)
        file_id = await tg_connect.get_file_properties(db_id, multi_clients)

        mime_type = file_id.mime_type or "video/mp4"
        file_size = file_id.file_size
        file_name = utils.get_name(file_id)

        if "video" not in mime_type and "audio" not in mime_type:
            raise web.HTTPBadRequest(text="File is not a video/audio — HLS not supported.")

        # Build the direct stream URL pointing to /dl/
        stream_url = f"{Server.URL}dl/{db_id}"

        # ── Parse optional query params for declared audio/subtitle tracks ──
        # Callers can hint at embedded tracks via query string so the
        # HLS Master Playlist declares them explicitly.
        # Example: /hls/{id}/playlist.m3u8?audio=jpn,eng&sub=eng,ben
        #
        # Format:  ?audio=lang1,lang2,...   (ISO 639-2/3 codes)
        #          ?sub=lang1,lang2,...
        #          ?audiolabels=Japanese,English  (optional human labels)
        #          ?sublabels=English,Bengali
        #
        # When these params are absent the playlist is a simple single-rendition
        # VOD list — Video.js VHS will still surface whatever tracks the
        # demuxer finds inside the MP4/MKV container.
        qparams   = request.rel_url.query
        audio_raw = qparams.get("audio", "")
        sub_raw   = qparams.get("sub", "")
        audio_label_raw = qparams.get("audiolabels", "")
        sub_label_raw   = qparams.get("sublabels", "")

        audio_langs  = [l.strip() for l in audio_raw.split(",")  if l.strip()] if audio_raw  else []
        sub_langs    = [l.strip() for l in sub_raw.split(",")    if l.strip()] if sub_raw    else []
        audio_labels = [l.strip() for l in audio_label_raw.split(",") if l.strip()] if audio_label_raw else []
        sub_labels   = [l.strip() for l in sub_label_raw.split(",")   if l.strip()] if sub_label_raw   else []

        # ── Build #EXT-X-MEDIA lines ─────────────────────────────────────────
        media_lines = ""

        for i, lang in enumerate(audio_langs):
            label   = audio_labels[i] if i < len(audio_labels) else lang.upper()
            default = "YES" if i == 0 else "NO"
            media_lines += (
                f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",'
                f'LANGUAGE="{lang}",NAME="{label}",'
                f'DEFAULT={default},AUTOSELECT={default},'
                f'URI="{stream_url}"\n'
            )

        for i, lang in enumerate(sub_langs):
            label   = sub_labels[i] if i < len(sub_labels) else lang.upper()
            default = "NO"
            media_lines += (
                f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",'
                f'LANGUAGE="{lang}",NAME="{label}",'
                f'DEFAULT={default},AUTOSELECT={default},'
                f'FORCED=NO,URI="{stream_url}"\n'
            )

        # ── Stream-inf line — include GROUP references if tracks declared ──
        stream_inf_attrs = "BANDWIDTH=8000000,RESOLUTION=1920x1080"
        if audio_langs:
            stream_inf_attrs += ',AUDIO="audio"'
        if sub_langs:
            stream_inf_attrs += ',SUBTITLES="subs"'

        # ── Assemble playlist ─────────────────────────────────────────────────
        playlist = (
            "#EXTM3U\n"
            "#EXT-X-VERSION:6\n"
            + media_lines +
            f"#EXT-X-STREAM-INF:{stream_inf_attrs}\n"
            f"{stream_url}\n"
        )

        return web.Response(
            text=playlist,
            content_type="application/vnd.apple.mpegurl",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-cache",
                "Content-Disposition": f'inline; filename="{db_id}.m3u8"',
            },
        )

    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except web.HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise web.HTTPInternalServerError(text=str(e))


@routes.get("/hls/{db_id}/info", allow_head=True)
async def hls_info_handler(request: web.Request):
    """
    Returns JSON metadata for a file — useful for your Android app to display
    file info before building the HLS URL.

    URL:  GET /hls/{db_id}/info
    Response:
    {
        "db_id": "...",
        "file_name": "...",
        "file_size": 123456,
        "mime_type": "video/mp4",
        "hls_url": "https://yourserver.com/hls/{db_id}/playlist.m3u8",
        "dl_url":  "https://yourserver.com/dl/{db_id}",
        "watch_url": "https://yourserver.com/watch/{db_id}"
    }
    """
    try:
        db_id = request.match_info["db_id"]

        index = min(work_loads, key=work_loads.get)
        faster_client = multi_clients[index]
        tg_connect = utils.ByteStreamer(faster_client)
        file_id = await tg_connect.get_file_properties(db_id, multi_clients)

        return web.json_response({
            "db_id": db_id,
            "file_name": utils.get_name(file_id),
            "file_size": file_id.file_size,
            "mime_type": file_id.mime_type or "application/octet-stream",
            "hls_url": f"{Server.URL}hls/{db_id}/playlist.m3u8",
            "hls_url_tracks_example": (
                f"{Server.URL}hls/{db_id}/playlist.m3u8"
                "?audio=jpn,eng&audiolabels=Japanese,English"
                "&sub=eng,ben&sublabels=English,Bengali"
            ),
            "dl_url":    f"{Server.URL}dl/{db_id}",
            "watch_url": f"{Server.URL}watch/{db_id}",
            "embed_url": f"{Server.URL}eml/{db_id}",
            "track_hint_note": (
                "Add ?audio=jpn,eng&audiolabels=Japanese,English"
                "&sub=eng,ben&sublabels=English,Bengali to hls_url "
                "to declare embedded tracks in the HLS playlist."
            ),
        }, headers={"Access-Control-Allow-Origin": "*"})

    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except Exception as e:
        traceback.print_exc()
        raise web.HTTPInternalServerError(text=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# CORE MEDIA STREAMER (unchanged, used by /dl/ and referenced by HLS)
# ─────────────────────────────────────────────────────────────────────────────

class_cache = {}

async def media_streamer(request: web.Request, db_id: str):
    range_header = request.headers.get("Range", 0)

    index = min(work_loads, key=work_loads.get)
    faster_client = multi_clients[index]

    if Telegram.MULTI_CLIENT:
        logging.info(f"Client {index} is now serving {request.headers.get('X-FORWARDED-FOR', request.remote)}")

    if faster_client in class_cache:
        tg_connect = class_cache[faster_client]
        logging.debug(f"Using cached ByteStreamer object for client {index}")
    else:
        logging.debug(f"Creating new ByteStreamer object for client {index}")
        tg_connect = utils.ByteStreamer(faster_client)
        class_cache[faster_client] = tg_connect

    logging.debug("before calling get_file_properties")
    file_id = await tg_connect.get_file_properties(db_id, multi_clients)
    logging.debug("after calling get_file_properties")

    file_size = file_id.file_size

    if range_header:
        from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
        from_bytes = int(from_bytes)
        until_bytes = int(until_bytes) if until_bytes else file_size - 1
    else:
        from_bytes = request.http_range.start or 0
        until_bytes = (request.http_range.stop or file_size) - 1

    if (until_bytes > file_size) or (from_bytes < 0) or (until_bytes < from_bytes):
        return web.Response(
            status=416,
            body="416: Range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    chunk_size = 1024 * 1024
    until_bytes = min(until_bytes, file_size - 1)

    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = until_bytes % chunk_size + 1

    req_length = until_bytes - from_bytes + 1
    part_count = math.ceil(until_bytes / chunk_size) - math.floor(offset / chunk_size)
    body = tg_connect.yield_file(
        file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size
    )

    mime_type = file_id.mime_type
    file_name = utils.get_name(file_id)
    disposition = "attachment"

    if not mime_type:
        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    if "video/" in mime_type or "audio/" in mime_type:
        disposition = "inline"

    return web.Response(
        status=206 if range_header else 200,
        body=body,
        headers={
            "Content-Type": f"{mime_type}",
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Length": str(req_length),
            "Content-Disposition": f'{disposition}; filename="{file_name}"',
            "Accept-Ranges": "bytes",
            "Access-Control-Allow-Origin": "*",
        },
    )
