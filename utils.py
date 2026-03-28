import base64
import datetime
import mimetypes
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import filedate
import requests
from rich.console import Console
from rich.progress import (
    Progress, BarColumn, DownloadColumn,
    TransferSpeedColumn, TaskProgressColumn, TextColumn,
)

console = Console()


def edit_creation_date(file_path, new_date: datetime.datetime):
    if new_date is None:
        return
    f = filedate.File(file_path)
    f.created  = new_date
    f.modified = new_date
    f.accessed = new_date

def download_file(
    file_url: str,
    file_path,
    date=None,
    skip_exists: bool = True,
    timeout: int = 30,
) -> bool:
    file_path = Path(file_path)

    if list(file_path.parent.glob(f"{file_path.name}.*")):
        return False

    file_path.parent.mkdir(parents=True, exist_ok=True)

    retries = 3
    for attempt in range(retries):
        try:
            response = requests.get(file_url, stream=True, timeout=timeout)

            content_type = response.headers.get("content-type", "")
            extension    = mimetypes.guess_extension(content_type.split(";")[0]) or ".bin"
            final_path   = Path(f"{file_path}{extension}")

            if skip_exists and final_path.exists():
                return False

            total_size = int(response.headers.get("content-length", 0))

            fname = final_path.name
            if len(fname) > 50:
                fname = fname[:47] + "..."

            if response.status_code == 200:
                with Progress(
                    TextColumn("[bold cyan]{task.description}"),
                    BarColumn(bar_width=35),
                    TaskProgressColumn(),
                    DownloadColumn(),
                    TransferSpeedColumn(),
                    transient=True,
                    console=console,
                ) as prog:
                    task = prog.add_task(fname, total=total_size or None)
                    with open(final_path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=65536):
                            if chunk:
                                f.write(chunk)
                                prog.update(task, advance=len(chunk))

                console.print(f"  [green]✓[/green] {final_path.name}")

                if date:
                    edit_creation_date(str(final_path), date)
                return True
            else:
                if attempt < retries - 1:
                    time.sleep(3)
                else:
                    console.print(f"  [red][Error][/red] Download failed ({response.status_code}): {fname}")

        except Exception as e:
            if attempt < retries - 1:
                time.sleep(3)
            else:
                console.print(f"  [red][Error][/red] Download error: {e}")

    return False

def stop_progress():
    pass


def isotime(date_str: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(date_str)


def timestamp(ts) -> datetime.datetime:
    from config import TIMEZONE
    ts_seconds = ts / 1000
    try:
        tz = ZoneInfo(TIMEZONE)
    except Exception:
        tz = ZoneInfo("Asia/Seoul")
    return datetime.datetime.fromtimestamp(ts_seconds, tz=tz)

def get_date_from_url(url: str) -> datetime.datetime | None:
    """
    Expecting a url in the format. The date can be gathered from the
    https://phinf.wevpstatic.net/MjAyMjA3MTZfODQg/MDAxNjU3OTAxNTA3OTYw.XicWQ6eh1gk6nIC4GFtqWKCDiFZQCMLPvQ2lUqOjjxwg.6tnIZEYqlfnbR03YaBitEi1SxQldnjVGcnlTpMK37oAg.JPEG/aab3aeaf86d149b2aa73f9a793eebfea888.jpg
    """
    prefix = 'https://phinf.wevpstatic.net/'
    if not url.startswith(prefix):
        return None

    parts = url.removeprefix(prefix).split('/')
    date_part_encoded = parts[0]

    print(date_part_encoded)

    try:
        # Decode it and take the first 8 characters (YYYYMMDD)
        date_str = base64.b64decode(date_part_encoded).decode('utf-8')[:8]
        return datetime.datetime.strptime(date_str, "%Y%m%d")
    except Exception:
        return None