import aiohttp
import jinja2
import urllib.parse
from FileStream.config import Telegram, Server
from FileStream.utils.database import Database
from FileStream.utils.human_readable import humanbytes

db = Database(Telegram.DATABASE_URL, Telegram.SESSION_NAME)


async def render_page(db_id):
    """Render the full watch page (with navbar, file info, embed section)."""
    file_data = await db.get_file(db_id)
    src        = urllib.parse.urljoin(Server.URL, f'dl/{file_data["_id"]}')
    hls_url    = urllib.parse.urljoin(Server.URL, f'hls/{file_data["_id"]}/playlist.m3u8')
    embed_url  = urllib.parse.urljoin(Server.URL, f'eml/{file_data["_id"]}')
    file_size  = humanbytes(file_data['file_size'])
    file_name  = file_data['file_name'].replace("_", " ")

    if str((file_data['mime_type']).split('/')[0].strip()) == 'video':
        template_file = "FileStream/template/play.html"
    else:
        template_file = "FileStream/template/dl.html"
        async with aiohttp.ClientSession() as s:
            async with s.get(src) as u:
                file_size = humanbytes(int(u.headers.get('Content-Length')))

    with open(template_file) as f:
        template = jinja2.Template(f.read())

    return template.render(
        file_name=file_name,
        file_url=src,
        file_size=file_size,
        hls_url=hls_url,
        embed_url=embed_url,
    )


async def render_embed_page(db_id):
    """Render the minimal embed-only player page (for /eml/ route)."""
    file_data = await db.get_file(db_id)
    src       = urllib.parse.urljoin(Server.URL, f'dl/{file_data["_id"]}')
    hls_url   = urllib.parse.urljoin(Server.URL, f'hls/{file_data["_id"]}/playlist.m3u8')
    file_name = file_data['file_name'].replace("_", " ")

    with open("FileStream/template/embed.html") as f:
        template = jinja2.Template(f.read())

    return template.render(
        file_name=file_name,
        file_url=src,
        hls_url=hls_url,
    )
