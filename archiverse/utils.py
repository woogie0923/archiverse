import base64
import datetime
import mimetypes
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import filedate
import requests
from rich.console import Console
from rich.markup import escape
from rich.progress import (
    Progress, BarColumn, DownloadColumn,
    TransferSpeedColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn,
)

console = Console()

_OUT_TIME_US_RE = re.compile(r"^out_time_ms=(\d+)$")

# Max characters for the mux progress label (narrow terminal + Rich live region).
_MUX_LABEL_MAX_LEN = 52
# FFmpeg emits many progress lines; throttling avoids flaky terminals printing one line per refresh.
_MUX_PROGRESS_MIN_INTERVAL = 0.12


def _mux_progress_label(description: str) -> str:
    """Shorten and escape text so Rich does not treat filename brackets as markup."""
    s = " ".join((description or "").replace("\n", " ").split())
    if len(s) > _MUX_LABEL_MAX_LEN:
        s = s[: _MUX_LABEL_MAX_LEN - 1] + "…"
    return escape(s)


def ffprobe_duration_seconds(path: Path) -> float | None:
    """Return container duration in seconds, or None if unknown."""
    from .config import BINARIES

    name = BINARIES.get("ffprobe", "ffprobe")
    ffprobe = shutil.which(name) or name
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if r.returncode != 0:
            return None
        s = (r.stdout or "").strip()
        if not s or s.lower() == "n/a":
            return None
        return float(s)
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return None


def run_ffmpeg_with_progress(
    cmd: list[str],
    *,
    duration_source: Path | None = None,
    description: str = "Muxing",
) -> tuple[int, str]:
    """
    Run ffmpeg with a Rich progress bar (uses -progress pipe:1).
    Returns (returncode, stderr text for failures).
    """
    if not cmd:
        return -1, ""

    duration_sec: float | None = None
    if duration_source is not None and duration_source.exists():
        duration_sec = ffprobe_duration_seconds(duration_source.resolve())
        if duration_sec is not None and duration_sec <= 0:
            duration_sec = None

    ffmpeg_exe = cmd[0]
    rest = cmd[1:]
    new_cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
    ] + rest

    creationflags = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

    proc = subprocess.Popen(
        new_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )

    stderr_holder: list[str] = [""]

    def _drain_stderr() -> None:
        if proc.stderr:
            try:
                stderr_holder[0] = proc.stderr.read()
            except Exception:
                pass

    t_err = threading.Thread(target=_drain_stderr, daemon=True)
    t_err.start()

    assert proc.stdout is not None

    if duration_sec:
        safe_desc = _mux_progress_label(description)
        last_completed = -1
        last_update_t = 0.0
        with Progress(
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=35),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            transient=True,
            console=console,
            refresh_per_second=8,
        ) as progress:
            task = progress.add_task(safe_desc, total=1000)
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                m = _OUT_TIME_US_RE.match(line)
                if m:
                    # FFmpeg reports microseconds in out_time_ms despite the name.
                    out_us = int(m.group(1))
                    sec = out_us / 1_000_000.0
                    pct = min(1.0, sec / duration_sec) if duration_sec else 0.0
                    completed = min(1000, int(pct * 1000))
                    completed = max(last_completed, completed)
                    now = time.monotonic()
                    if completed == last_completed:
                        continue
                    if (
                        completed < 1000
                        and (now - last_update_t) < _MUX_PROGRESS_MIN_INTERVAL
                    ):
                        continue
                    progress.update(task, completed=completed)
                    last_completed = completed
                    last_update_t = now
            rc = proc.wait()
            t_err.join(timeout=30)
            progress.update(task, completed=1000)
    else:
        with console.status(f"[bold cyan]{_mux_progress_label(description)}…[/bold cyan]"):
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
            rc = proc.wait()
            t_err.join(timeout=30)

    return (rc if rc is not None else -1), stderr_holder[0]


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
    from .config import TIMEZONE
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