import os
import re
import time
import zipfile
import io
import subprocess
import tempfile
import requests
import shutil
import streamlit as st

# =====================================================================
# CONFIGURATION & API KEYS (STREAMLIT SECRETS VAULT + LOCAL FALLBACKS)
# =====================================================================
def get_secret(key: str, default: str = "") -> str:
    """Safely retrieves keys from Streamlit Secrets, environment, or default."""
    try:
        if key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    return os.environ.get(key, default).strip()


PEXELS_API_KEY = get_secret("PEXELS_API_KEY", "")
PIXABAY_API_KEY = get_secret("PIXABAY_API_KEY", "")
COVERR_API_KEY = get_secret("COVERR_API_KEY", "")
UNSPLASH_ACCESS_KEY = get_secret("UNSPLASH_ACCESS_KEY", "")

OUTPUT_DIR = "downloaded_broll"
os.makedirs(OUTPUT_DIR, exist_ok=True)

MAX_VIDEO_SIZE_MB = 100.0
MIN_IMAGE_SIZE_KB = 100.0   # Quality floor: 100 KB
MAX_IMAGE_SIZE_MB = 10.0    # Quality cap: 10 MB

GLOBAL_USER_AGENT = "BrollStudioAutomation/1.0 (documentary_research_tool; contact@studio.local)"
GRAMMAR_FILLERS = {"a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with", "between"}

# =====================================================================
# TOOL METADATA & DESCRIPTIONS
# =====================================================================
TOOLS = {
    "Stock Video Footage": {
        "tag": "stock_video",
        "ext": "mp4",
        "desc": "Modern cinematic drone aerials, highways, city traffic, laboratories, and everyday visual metaphors in 1080p Full HD.",
        "type": "search",
        "auth_key": "PEXELS_API_KEY"
    },
    "Stock Photos": {
        "tag": "stock_photo",
        "ext": "jpg",
        "desc": "Crisp high-resolution modern photography and portraits (100KB to 10MB) for Ken Burns documentary animation.",
        "type": "search",
        "auth_key": "PEXELS_API_KEY"
    },
    "Pixabay Video Footage": {
        "tag": "pixabay_video",
        "ext": "mp4",
        "desc": "Commercial stock video, time-lapses, natural scenery, and macro clips hosted on fast AWS edge servers.",
        "type": "search",
        "auth_key": "PIXABAY_API_KEY"
    },
    "Pixabay Stock Photos": {
        "tag": "pixabay_photo",
        "ext": "jpg",
        "desc": "High-resolution commercial stock stills, landscapes, architecture, and environmental details (100KB to 10MB).",
        "type": "search",
        "auth_key": "PIXABAY_API_KEY"
    },
    "Coverr Cinematic Video": {
        "tag": "coverr_video",
        "ext": "mp4",
        "desc": "Curated filmmaker footage, moody establishing shots, modern workspaces, and smooth documentary transitions.",
        "type": "search",
        "auth_key": "COVERR_API_KEY"
    },
    "Unsplash Editorial Photos": {
        "tag": "unsplash_photo",
        "ext": "jpg",
        "desc": "Award-winning photographic lighting, premium character portraits, architecture, and fine-art editorial stills (100KB to 10MB).",
        "type": "search",
        "auth_key": "UNSPLASH_ACCESS_KEY"
    },
    "Universal Video Trimmer": {
        "tag": "universal_clip",
        "ext": "mp4",
        "desc": "Extracts precise 5s to 60s silent B-roll segments from any direct video link (Google Drive, Archive.org, Dropbox, or MP4 URLs).",
        "type": "universal_trim",
        "auth_key": None
    },
    "YouTube Clip Downloader": {
        "tag": "youtube_clip",
        "ext": "mp4",
        "desc": "Download an authorized YouTube video section by timestamp, with up to 4K quality when available.",
        "type": "youtube_clip",
        "auth_key": None
    },
    "NASA Science & Earth": {
        "tag": "nasa_video",
        "ext": "mp4",
        "desc": "Satellite views of Earth, tectonic rift simulations, topography, geology radar scans, space, and planetary science.",
        "type": "search",
        "auth_key": None
    },
    "Wikimedia Commons Stills": {
        "tag": "wikimedia_still",
        "ext": "jpg",
        "desc": "High-resolution public domain archival scans: vintage cartography maps, historical diagrams, ancient manuscripts, and seismograms (100KB to 10MB).",
        "type": "search",
        "auth_key": None
    },
    "Library of Congress Film": {
        "tag": "loc_film",
        "ext": "mp4",
        "desc": "Authentic early 20th-century historical film reels: WWI trenches, WWII combat, 1900s–1940s urban street life, and early aviation.",
        "type": "search",
        "auth_key": None
    }
}

# =====================================================================
# FILENAME SANITIZATION & QUERY ENGINE
# =====================================================================
def prompt_to_clean_filename(prompt: str, ext: str, max_chars: int = 50) -> str:
    clean = re.sub(r'[\\/*?:"<>|]', "", prompt)
    clean = re.sub(r"[^\w\s-]", "", clean).strip()
    clean = re.sub(r"[\s-]+", "_", clean).lower()
    base_name = clean[:max_chars].strip("_") or "scene_asset"

    candidate = f"{base_name}.{ext}"
    full_path = os.path.join(OUTPUT_DIR, candidate)

    counter = 1
    while os.path.exists(full_path):
        candidate = f"{base_name}_{counter}.{ext}"
        full_path = os.path.join(OUTPUT_DIR, candidate)
        counter += 1

    return candidate


def get_search_queries(raw_prompt: str) -> tuple[str, str | None]:
    clean = re.sub(r"[^\w\s-]", " ", raw_prompt).strip()
    clean = re.sub(r"\s+", " ", clean)
    primary = clean
    words = [w for w in clean.split() if w.lower() not in GRAMMAR_FILLERS]
    fallback = " ".join(words) if words and len(words) < len(clean.split()) else None
    return primary, fallback


def download_stream(url: str, output_path: str, max_size_mb: float = MAX_VIDEO_SIZE_MB, min_size_kb: float = 0.0) -> tuple[bool, str]:
    headers = {"User-Agent": GLOBAL_USER_AGENT}
    try:
        with requests.get(url, headers=headers, stream=True, timeout=35) as r:
            r.raise_for_status()
            total_bytes = int(r.headers.get("content-length", 0))
            total_mb = total_bytes / (1024 * 1024) if total_bytes else 0
            total_kb = total_bytes / 1024 if total_bytes else 0

            if total_mb > max_size_mb:
                return False, f"File exceeded size cap ({total_mb:.1f} MB > {max_size_mb:.0f} MB)"
            if 0 < total_kb < min_size_kb:
                return False, f"File below quality floor ({total_kb:.1f} KB < {min_size_kb:.0f} KB)"

            with open(output_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
            return True, f"{total_mb:.1f} MB" if total_mb >= 1.0 else f"{total_kb:.1f} KB"
    except Exception as e:
        if os.path.exists(output_path):
            os.remove(output_path)
        return False, str(e)


# =====================================================================
# MEDIA DOWNLOAD ENGINES
# =====================================================================
def fetch_stock_video(query: str, out_path: str) -> tuple[bool, str]:
    if not PEXELS_API_KEY:
        return False, "PEXELS_API_KEY missing from secrets/environment"
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "orientation": "landscape", "size": "large", "per_page": 5}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        videos = r.json().get("videos", [])
        if not videos:
            return False, "No matching clips found"
        files = videos[0].get("video_files", [])
        stream = next((s for s in files if s.get("height") == 1080 or s.get("width") == 1920), files[0] if files else None)
        if not stream:
            return False, "No valid video stream"
        return download_stream(stream["link"], out_path, max_size_mb=MAX_VIDEO_SIZE_MB)
    except Exception as e:
        return False, str(e)


def fetch_stock_photo(query: str, out_path: str) -> tuple[bool, str]:
    if not PEXELS_API_KEY:
        return False, "PEXELS_API_KEY missing from secrets/environment"
    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "orientation": "landscape", "per_page": 5}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        photos = r.json().get("photos", [])
        if not photos:
            return False, "No photos found"
        src = photos[0].get("src", {})

        img_url = src.get("original") or src.get("large2x")
        success, detail = download_stream(img_url, out_path, max_size_mb=MAX_IMAGE_SIZE_MB, min_size_kb=MIN_IMAGE_SIZE_KB)

        if not success and src.get("large2x") and img_url != src.get("large2x"):
            img_url = src.get("large2x")
            success, detail = download_stream(img_url, out_path, max_size_mb=MAX_IMAGE_SIZE_MB, min_size_kb=MIN_IMAGE_SIZE_KB)

        return success, detail
    except Exception as e:
        return False, str(e)


def fetch_pixabay_video(query: str, out_path: str) -> tuple[bool, str]:
    if not PIXABAY_API_KEY:
        return False, "PIXABAY_API_KEY missing from secrets/environment"
    url = "https://pixabay.com/api/videos/"
    params = {"key": PIXABAY_API_KEY, "q": query, "per_page": 5}
    try:
        r = requests.get(url, params=params, timeout=15)
        hits = r.json().get("hits", [])
        if not hits:
            return False, "No clips found"
        streams = hits[0].get("videos", {})
        chosen = streams.get("large") or streams.get("medium") or streams.get("small")
        if not chosen or not chosen.get("url"):
            return False, "No downloadable stream"
        return download_stream(chosen["url"], out_path, max_size_mb=MAX_VIDEO_SIZE_MB)
    except Exception as e:
        return False, str(e)


def fetch_pixabay_photo(query: str, out_path: str) -> tuple[bool, str]:
    if not PIXABAY_API_KEY:
        return False, "PIXABAY_API_KEY missing from secrets/environment"
    url = "https://pixabay.com/api/"
    params = {"key": PIXABAY_API_KEY, "q": query, "image_type": "photo", "orientation": "horizontal", "per_page": 5}
    try:
        r = requests.get(url, params=params, timeout=15)
        hits = r.json().get("hits", [])
        if not hits:
            return False, "No photos found"
        img_url = hits[0].get("largeImageURL") or hits[0].get("imageURL")
        return download_stream(img_url, out_path, max_size_mb=MAX_IMAGE_SIZE_MB, min_size_kb=MIN_IMAGE_SIZE_KB)
    except Exception as e:
        return False, str(e)


def fetch_coverr_video(query: str, out_path: str) -> tuple[bool, str]:
    if not COVERR_API_KEY:
        return False, "COVERR_API_KEY missing from secrets/environment"
    url = "https://api.coverr.co/videos"
    headers = {"Authorization": f"Bearer {COVERR_API_KEY}"}
    params = {"query": query, "urls": "true", "page_size": 5}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        hits = r.json().get("hits") or r.json().get("videos") or []
        if not hits:
            return False, "No Coverr video found"
        urls_obj = hits[0].get("urls", {})
        v_url = urls_obj.get("mp4_download") or urls_obj.get("mp4")
        return download_stream(v_url, out_path, max_size_mb=MAX_VIDEO_SIZE_MB)
    except Exception as e:
        return False, str(e)


def fetch_unsplash_photo(query: str, out_path: str) -> tuple[bool, str]:
    if not UNSPLASH_ACCESS_KEY:
        return False, "UNSPLASH_ACCESS_KEY missing from secrets/environment"
    url = "https://api.unsplash.com/search/photos"
    headers = {"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}
    params = {"query": query, "orientation": "landscape", "per_page": 5}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        results = r.json().get("results", [])
        if not results:
            return False, "No photos found"
        img_url = results[0]["urls"].get("full") or results[0]["urls"].get("regular")
        return download_stream(img_url, out_path, max_size_mb=MAX_IMAGE_SIZE_MB, min_size_kb=MIN_IMAGE_SIZE_KB)
    except Exception as e:
        return False, str(e)


def fetch_nasa_broll(query: str, out_path: str) -> tuple[bool, str]:
    url = "https://images-api.nasa.gov/search"
    params = {"q": query, "media_type": "video"}
    try:
        r = requests.get(url, params=params, timeout=15)
        items = r.json().get("collection", {}).get("items", [])
        if not items:
            return False, "No NASA records found"
        for item in items[:3]:
            manifest = item.get("href")
            if not manifest:
                continue
            m_res = requests.get(manifest, timeout=12)
            if m_res.status_code != 200:
                continue
            mp4s = [u for u in m_res.json() if u.endswith(".mp4")]
            chosen = next((u for u in mp4s if "~1080p.mp4" in u or "1080p" in u), mp4s[0] if mp4s else None)
            if chosen:
                return download_stream(chosen, out_path, max_size_mb=MAX_VIDEO_SIZE_MB)
        return False, "No downloadable MP4 asset found"
    except Exception as e:
        return False, str(e)


def fetch_wikimedia_image(query: str, out_path: str) -> tuple[bool, str]:
    url = "https://commons.wikimedia.org/w/api.php"
    headers = {"User-Agent": GLOBAL_USER_AGENT}
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap -filetype:audio",
        "gsrnamespace": "6",
        "gsrlimit": "6",
        "prop": "imageinfo",
        "iiprop": "url|mime|size",
        "iiurlwidth": "2560"
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        pages = r.json().get("query", {}).get("pages", {})
        for _, page in pages.items():
            info = (page.get("imageinfo") or [{}])[0]
            img_url = info.get("thumburl") or info.get("url")
            if img_url:
                return download_stream(img_url, out_path, max_size_mb=MAX_IMAGE_SIZE_MB, min_size_kb=MIN_IMAGE_SIZE_KB)
        return False, "No archival stills found"
    except Exception as e:
        return False, str(e)


def fetch_loc_broll(query: str, out_path: str) -> tuple[bool, str]:
    url = "https://www.loc.gov/film-and-videos/"
    headers = {"User-Agent": GLOBAL_USER_AGENT}
    words = [w for w in query.split() if len(w) > 2]
    params = {"q": " ".join(words), "fo": "json", "fa": "online-format:video", "c": 4}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        results = r.json().get("results", [])
        for item in results:
            item_id = item.get("id")
            if not item_id:
                continue
            m_res = requests.get(f"{item_id}?fo=json", headers=headers, timeout=12)
            if m_res.status_code != 200:
                continue
            for res in m_res.json().get("resources", []):
                for grp in res.get("files", []):
                    for f in grp:
                        if f.get("url", "").endswith(".mp4"):
                            return download_stream(f["url"], out_path, max_size_mb=MAX_VIDEO_SIZE_MB)
        return False, "No progressive MP4 found"
    except Exception as e:
        return False, str(e)


def time_to_sec(t_str: str) -> float | None:
    parts = t_str.strip().split(":")
    try:
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None
    return None


# =====================================================================
# YOUTUBE CLIP DOWNLOADER
# =====================================================================
YOUTUBE_QUALITY_OPTIONS = {
    "Best Available": None,
    "4K (2160p)": 2160,
    "2K (1440p)": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}


def youtube_time_to_seconds(value: str) -> int:
    value = str(value).strip()
    if not value:
        raise ValueError("Time cannot be empty.")

    parts = value.split(":")

    try:
        if len(parts) == 1:
            return int(parts[0])

        if len(parts) == 2:
            minutes = int(parts[0])
            seconds = int(parts[1])
            if seconds >= 60:
                raise ValueError
            return minutes * 60 + seconds

        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = int(parts[2])
            if minutes >= 60 or seconds >= 60:
                raise ValueError
            return hours * 3600 + minutes * 60 + seconds

        raise ValueError
    except ValueError:
        raise ValueError("Invalid time format. Use HH:MM:SS or MM:SS.")


def youtube_format_time(seconds: int) -> str:
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def youtube_safe_filename(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*]', "", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return (text[:120] or "YouTube Clip")


def get_youtube_video_info(url: str):
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError(
            "yt-dlp is not installed. Add yt-dlp to requirements.txt "
            "or run: python -m pip install -U yt-dlp"
        )

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def get_youtube_format(height):
    if height is None:
        return "bestvideo+bestaudio/best"

    return (
        f"bestvideo[height<={height}][ext=mp4][vcodec^=avc1]+"
        f"bestaudio[ext=m4a]/"
        f"bestvideo[height<={height}]+bestaudio/"
        f"best[height<={height}]"
    )


def download_youtube_clip(
    url: str,
    start_seconds: int,
    end_seconds: int,
    quality: str,
    output_directory: str,
    title: str,
) -> str:
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError(
            "yt-dlp is not installed. Add yt-dlp to requirements.txt "
            "or run: python -m pip install -U yt-dlp"
        )

    os.makedirs(output_directory, exist_ok=True)

    max_height = YOUTUBE_QUALITY_OPTIONS[quality]
    format_selector = get_youtube_format(max_height)

    download_ranges = yt_dlp.utils.download_range_func(
        None,
        [(start_seconds, end_seconds)],
    )

    output_template = os.path.join(
        output_directory,
        "youtube_clip.%(ext)s"
    )

    options = {
        "format": format_selector,
        "noplaylist": True,
        "outtmpl": output_template,
        "download_ranges": download_ranges,
        "force_keyframes_at_cuts": False,
        "merge_output_format": "mp4",
        "nopart": False,
        "quiet": True,
        "no_warnings": True,
        "writethumbnail": False,
        "writesubtitles": False,
        "writeautomaticsub": False,
        "writeinfojson": False,
        "writedescription": False,
        "writeannotations": False,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])

    media_files = []
    for file in Path(output_directory).iterdir():
        if file.is_file() and file.suffix.lower() in {
            ".mp4", ".mkv", ".webm", ".mov", ".m4v"
        }:
            media_files.append(file)

    if not media_files:
        raise RuntimeError("yt-dlp did not create a video clip.")

    media_files.sort(
        key=lambda x: x.stat().st_mtime,
        reverse=True
    )
    source_file = media_files[0]

    clean_title = youtube_safe_filename(title)
    start_name = youtube_format_time(start_seconds).replace(":", "-")
    end_name = youtube_format_time(end_seconds).replace(":", "-")

    final_name = (
        f"{clean_title}_{start_name}_{end_name}.mp4"
    )
    final_path = Path(OUTPUT_DIR) / final_name

    if source_file.suffix.lower() == ".mp4":
        if final_path.exists():
            final_path.unlink()
        shutil.move(str(source_file), str(final_path))
    else:
        command = [
            "ffmpeg",
            "-y",
            "-i", str(source_file),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(final_path),
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "FFmpeg conversion failed:\n\n"
                + result.stderr[-5000:]
            )

        source_file.unlink(missing_ok=True)

    if not final_path.exists():
        raise RuntimeError("Final MP4 was not created.")

    return str(final_path)


# =====================================================================
# UNIVERSAL VIDEO STREAM TRIMMER (ZERO DATACENTER 403 ISSUES)
# =====================================================================
def cut_universal_video(source_url: str, start_str: str, end_str: str, out_path: str) -> tuple[bool, str]:
    s_sec = time_to_sec(start_str)
    e_sec = time_to_sec(end_str)
    if s_sec is None or e_sec is None:
        return False, "Invalid time format (Use MM:SS)"
    duration = e_sec - s_sec
    if duration < 5.0 or duration > 60.0:
        return False, f"Duration must be between 5s and 60s (Requested: {duration:.1f}s)"

    # Convert common cloud share links (Dropbox/Google Drive) to direct download streams
    clean_url = source_url.strip()
    if "dropbox.com" in clean_url and "dl=0" in clean_url:
        clean_url = clean_url.replace("dl=0", "dl=1")
    elif "drive.google.com/file/d/" in clean_url:
        file_id = clean_url.split("/d/")[1].split("/")[0]
        clean_url = f"https://drive.google.com/uc?export=download&id={file_id}"

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(s_sec),
        "-i", clean_url,
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "22",
        "-an",
        "-movflags", "+faststart",
        out_path
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            sz = os.path.getsize(out_path) / (1024 * 1024)
            return True, f"{sz:.1f} MB (Silent B-roll)"
        return False, "Trimming produced an empty file"
    except Exception as e:
        return False, f"Video trimming error: {e}"


# =====================================================================
# FAST ZERO-COMPRESSION ZIP BUNDLER
# =====================================================================
def create_zip_bytes(file_list: list[str]) -> bytes:
    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for f in file_list:
            if os.path.exists(f):
                zf.write(f, arcname=os.path.basename(f))
    mem_zip.seek(0)
    return mem_zip.read()


ENGINE_DISPATCH = {
    "Stock Video Footage": fetch_stock_video,
    "Stock Photos": fetch_stock_photo,
    "Pixabay Video Footage": fetch_pixabay_video,
    "Pixabay Stock Photos": fetch_pixabay_photo,
    "Coverr Cinematic Video": fetch_coverr_video,
    "Unsplash Editorial Photos": fetch_unsplash_photo,
    "NASA Science & Earth": fetch_nasa_broll,
    "Wikimedia Commons Stills": fetch_wikimedia_image,
    "Library of Congress Film": fetch_loc_broll,
}

# =====================================================================
# STREAMLIT UI SETUP
# =====================================================================
st.set_page_config(page_title="Automation Tools By Shoaib Malik", page_icon="🎬", layout="wide")

st.markdown("""
<style>
    div[data-testid="stRadio"] label p {
        font-size: 1.15rem !important;
        font-weight: 500 !important;
        line-height: 1.8 !important;
    }
    div[data-testid="stRadio"] [data-baseweb="radio"] div:first-child {
        transform: scale(1.35);
        margin-right: 0.6rem !important;
    }
    h3 {
        font-size: 1.4rem !important;
    }
</style>
""", unsafe_allow_html=True)

# =====================================================================
# SECURITY LOGIN GATEKEEPER
# =====================================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.markdown("# 🎬 Automation Tools By Shoaib Malik")
    st.markdown("Automated archival and stock pipeline for high-retention documentary editing.")
    st.divider()

    _, col_login, _ = st.columns([1, 1.2, 1])
    with col_login:
        st.markdown("### 🔒 Security Verification")
        st.caption("Please log in with your authorized credentials to access this tool.")
        with st.form("login_form"):
            input_username = st.text_input("Username")
            input_password = st.text_input("Password", type="password")
            submit_login = st.form_submit_button("Unlock Tools", type="primary", use_container_width=True)

            if submit_login:
                if input_username == "Malik" and input_password == "Shoaib@10":
                    st.session_state.authenticated = True
                    st.success("Access Granted! Loading studio...")
                    st.rerun()
                else:
                    st.error("Incorrect Username or Password. Access Denied.")

    st.stop()

# =====================================================================
# AUTHENTICATED WORKSPACE
# =====================================================================
col_header, col_logout = st.columns([4, 1])
with col_header:
    st.markdown("# 🎬 Automation Tools By Shoaib Malik")
    st.markdown("Automated archival and stock pipeline for high-retention documentary editing.")
with col_logout:
    st.write("")
    if st.button("🔒 Log Out", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()

st.divider()

col_nav, col_main = st.columns([1, 2.3])

with col_nav:
    st.subheader("Select Source Tool")
    selected_tool_name = st.radio(
        "Available Repositories:",
        list(TOOLS.keys()),
        index=0
    )

tool_info = TOOLS[selected_tool_name]

with col_main:
    st.subheader(f"Tool: {selected_tool_name}")
    st.info(tool_info["desc"])

    auth_key_name = tool_info.get("auth_key")
    if auth_key_name:
        current_key = globals().get(auth_key_name, "")
        if not current_key:
            st.warning(f"⚠️ `{auth_key_name}` is not configured in your Streamlit Secrets vault. Add it to enable downloads for this tool.")

    if tool_info["type"] == "search":
        prompt_input = st.text_area(
            "Paste Visual Prompts (one prompt per line):",
            height=180,
            placeholder="geologist holding rock sample\nmountain peak sunrise landscape\nancient roman architecture"
        )
        start_btn = st.button(f"Start Sourcing ({selected_tool_name})", type="primary")

        if start_btn:
            lines = [line.strip() for line in prompt_input.splitlines() if line.strip()]
            if not lines:
                st.warning("Please enter at least one prompt before starting.")
            else:
                progress_bar = st.progress(0)
                status_box = st.container()

                fetch_func = ENGINE_DISPATCH[selected_tool_name]
                ext = tool_info["ext"]
                successful_files = []

                with status_box:
                    st.write(f"**Fetching {len(lines)} asset(s)...**")
                    for idx, raw_prompt in enumerate(lines):
                        filename = prompt_to_clean_filename(raw_prompt, ext)
                        out_path = os.path.join(OUTPUT_DIR, filename)

                        primary_q, fallback_q = get_search_queries(raw_prompt)
                        t0 = time.time()

                        with st.spinner(f"[{idx+1}/{len(lines)}] Sourcing: \"{raw_prompt}\"..."):
                            success, detail = fetch_func(primary_q, out_path)
                            if not success and fallback_q and fallback_q != primary_q:
                                success, detail = fetch_func(fallback_q, out_path)

                        elapsed = time.time() - t0
                        if success:
                            st.success(f"✓ **Retrieved:** `{filename}` ({detail} in {elapsed:.1f}s)")
                            successful_files.append((filename, out_path, ext))
                        else:
                            st.error(f"✖ **Failed:** \"{raw_prompt}\" — {detail}")

                        progress_bar.progress((idx + 1) / len(lines))

                st.balloons()

                if successful_files:
                    st.markdown("### 📥 Save Assets to Your Computer")
                    
                    zip_data = create_zip_bytes([path for _, path, _ in successful_files])
                    st.download_button(
                        label="⬇️ Download All Files to Computer (.ZIP)",
                        data=zip_data,
                        file_name="broll_assets.zip",
                        mime="application/zip",
                        type="primary",
                        use_container_width=True
                    )

                    st.divider()

                    st.markdown("#### Individual Asset Previews")
                    for fname, fpath, fext in successful_files:
                        col_preview, col_info = st.columns([1.5, 1])
                        with col_preview:
                            if fext == "mp4":
                                st.video(fpath)
                            else:
                                st.image(fpath)
                        with col_info:
                            st.write(f"**File:** `{fname}`")
                            with open(fpath, "rb") as item_f:
                                st.download_button(
                                    label=f"⬇️ Download {fname}",
                                    data=item_f.read(),
                                    file_name=fname,
                                    mime="video/mp4" if fext == "mp4" else "image/jpeg",
                                    key=f"dl_{fname}"
                                )
                        st.write("---")

    elif tool_info["type"] == "youtube_clip":
        st.markdown("#### YouTube Clip Downloader")
        st.caption(
            "Use this with videos you own or are authorized to download and process."
        )

        youtube_url = st.text_input(
            "YouTube URL:",
            placeholder="https://www.youtube.com/watch?v=..."
        )

        info_key = "youtube_video_info"

        if st.button("🔎 Get Video Information", use_container_width=True):
            if not youtube_url.strip():
                st.warning("Please provide a YouTube URL.")
            else:
                try:
                    with st.spinner("Reading video information..."):
                        info = get_youtube_video_info(youtube_url.strip())
                    st.session_state[info_key] = info
                except Exception as e:
                    st.error(f"Could not read the video: {e}")

        youtube_info = st.session_state.get(info_key)

        if youtube_info:
            youtube_title = youtube_info.get("title", "YouTube Video")
            youtube_duration = youtube_info.get("duration")
            youtube_thumbnail = youtube_info.get("thumbnail")

            st.subheader(youtube_title)

            if youtube_thumbnail:
                st.image(youtube_thumbnail, use_container_width=True)

            if youtube_duration:
                st.info(
                    "Video duration: "
                    + youtube_format_time(youtube_duration)
                )

        col_yt1, col_yt2 = st.columns(2)

        with col_yt1:
            youtube_start = st.text_input(
                "Start Timestamp (HH:MM:SS or MM:SS):",
                value="00:00:00",
                key="youtube_start_time"
            )

        with col_yt2:
            youtube_end = st.text_input(
                "End Timestamp (HH:MM:SS or MM:SS):",
                value="00:00:30",
                key="youtube_end_time"
            )

        youtube_quality = st.selectbox(
            "Maximum Video Quality:",
            list(YOUTUBE_QUALITY_OPTIONS.keys()),
            index=2,
            key="youtube_quality"
        )

        st.caption(
            "If the selected resolution is unavailable, yt-dlp will use "
            "the highest compatible resolution it can obtain up to the selected limit."
        )

        youtube_clip_btn = st.button(
            "🎬 Create YouTube Clip",
            type="primary",
            use_container_width=True
        )

        if youtube_clip_btn:
            if not youtube_url.strip():
                st.warning("Please provide a YouTube URL.")
            else:
                try:
                    start_sec = youtube_time_to_seconds(youtube_start)
                    end_sec = youtube_time_to_seconds(youtube_end)

                    if start_sec < 0:
                        raise ValueError("Start time cannot be negative.")

                    if end_sec <= start_sec:
                        raise ValueError(
                            "End time must be greater than start time."
                        )

                    if youtube_info:
                        duration = youtube_info.get("duration")
                        if duration and end_sec > duration:
                            raise ValueError(
                                "The end time is longer than the video."
                            )

                    with st.spinner("Preparing the YouTube clip..."):
                        if youtube_info:
                            title = youtube_info.get(
                                "title",
                                "YouTube Clip"
                            )
                        else:
                            youtube_info = get_youtube_video_info(
                                youtube_url.strip()
                            )
                            st.session_state[info_key] = youtube_info
                            title = youtube_info.get(
                                "title",
                                "YouTube Clip"
                            )

                    temp_directory = tempfile.mkdtemp(
                        prefix="youtube_clip_"
                    )

                    try:
                        t0 = time.time()

                        with st.spinner(
                            f"Creating {youtube_quality} clip "
                            f"from {youtube_start} to {youtube_end}..."
                        ):
                            output_file = download_youtube_clip(
                                url=youtube_url.strip(),
                                start_seconds=start_sec,
                                end_seconds=end_sec,
                                quality=youtube_quality,
                                output_directory=temp_directory,
                                title=title,
                            )

                        elapsed = time.time() - t0

                        st.success(
                            f"✓ YouTube clip created: "
                            f"`{os.path.basename(output_file)}` "
                            f"({youtube_format_time(end_sec - start_sec)} "
                            f"in {elapsed:.1f}s)"
                        )

                        st.video(output_file)

                        with open(output_file, "rb") as vf:
                            st.download_button(
                                label="⬇️ Download YouTube Clip to PC",
                                data=vf.read(),
                                file_name=os.path.basename(output_file),
                                mime="video/mp4",
                                type="primary",
                                use_container_width=True
                            )

                    finally:
                        shutil.rmtree(
                            temp_directory,
                            ignore_errors=True
                        )

                except Exception as e:
                    st.error(
                        f"✖ YouTube clipping failed: {e}"
                    )

        st.divider()
        st.caption(
            "YouTube downloads depend on the source, available formats, "
            "and the environment running the app."
        )

    elif tool_info["type"] == "universal_trim":
        st.markdown("#### Direct Video Stream Trimmer")
        st.caption("Paste any public `.mp4`, Archive.org, Vimeo, Google Drive, or Dropbox video link:")
        v_url = st.text_input("Source Video URL:", placeholder="https://ia800201.us.archive.org/.../sample.mp4")
        
        col_t1, col_t2 = st.columns(2)
        with col_t1:
            start_time = st.text_input("Start Timestamp (MM:SS):", value="00:15")
        with col_t2:
            end_time = st.text_input("End Timestamp (MM:SS):", value="00:35")

        custom_label = st.text_input("Target File Title (Prompt/Label):", value="documentary_broll_clip")
        trim_btn = st.button("Extract Clip (5s to 60s)", type="primary")

        if trim_btn:
            if not v_url:
                st.warning("Please provide a valid video URL.")
            else:
                filename = prompt_to_clean_filename(custom_label, "mp4")
                out_path = os.path.join(OUTPUT_DIR, filename)
                t0 = time.time()

                with st.spinner(f"Extracting silent video segment from {start_time} to {end_time}..."):
                    success, detail = cut_universal_video(v_url, start_time, end_time, out_path)

                elapsed = time.time() - t0
                if success:
                    st.success(f"✓ **Saved Silent B-Roll Clip:** `{filename}` ({detail} in {elapsed:.1f}s)")
                    st.video(out_path)
                    with open(out_path, "rb") as vf:
                        st.download_button(
                            label=f"⬇️ Download {filename} to PC",
                            data=vf.read(),
                            file_name=filename,
                            mime="video/mp4",
                            type="primary",
                            use_container_width=True
                        )
                else:
                    st.error(f"✖ **Trimming Failed:** {detail}")

        st.divider()
        st.markdown("#### Need to Trim YouTube Specifically?")
        st.write("Cloud servers (AWS) are blocked by YouTube's datacenter firewalls. Use these fast, free browser-side cutters that use your home internet IP:")
        c1, c2 = st.columns(2)
        with c1:
            st.link_button("🌐 Open YT-Clipper (Timestamp Cutter)", "https://www.yt-clipper.com/", use_container_width=True)
        with c2:
            st.link_button("⚡ Open Cobalt (Direct Video Downloader)", "https://cobalt.tools/", use_container_width=True)
