import os
import re
import time
import zipfile
import io
import subprocess
import requests
import yt_dlp
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
    "YouTube Precision Cutter": {
        "tag": "youtube_clip",
        "ext": "mp4",
        "desc": "Extracts video-only documentary scenes (silent B-roll, 5s to 60s) directly from any YouTube link without downloading full videos.",
        "type": "youtube",
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
# YOUTUBE CUTTER (RESOLVES CODE 8 MOOV ATOM ERROR)
# =====================================================================
def cut_youtube(video_url: str, start_str: str, end_str: str, out_path: str) -> tuple[bool, str]:
    s_sec = time_to_sec(start_str)
    e_sec = time_to_sec(end_str)
    if s_sec is None or e_sec is None:
        return False, "Invalid time format (Use MM:SS)"
    duration = e_sec - s_sec
    if duration < 5.0 or duration > 60.0:
        return False, f"Duration must be between 5s and 60s (Requested: {duration:.1f}s)"

    # Clean URL down to base video ID
    clean_url = video_url.strip()
    if "youtu.be/" in clean_url:
        vid_id = clean_url.split("youtu.be/")[1].split("?")[0].split("&")[0]
        clean_url = f"https://www.youtube.com/watch?v={vid_id}"
    elif "watch?v=" in clean_url:
        vid_id = clean_url.split("watch?v=")[1].split("&")[0]
        clean_url = f"https://www.youtube.com/watch?v={vid_id}"

    # Extract authenticated stream URL and headers via Android player client
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestvideo[height<=1080][ext=mp4]/bestvideo[height<=1080]/best[ext=mp4]/best",
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"]
            }
        }
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False)
    except Exception as e:
        return False, f"YouTube Extraction Error: {e}"

    stream_url = info.get("url")
    http_headers = info.get("http_headers", {})

    if not stream_url:
        formats = info.get("formats", [])
        v_formats = [f for f in formats if f.get("vcodec") != "none" and f.get("url")]
        if v_formats:
            v_formats.sort(key=lambda x: (x.get("height") or 0), reverse=True)
            stream_url = v_formats[0]["url"]
            http_headers = v_formats[0].get("http_headers", {})
        else:
            return False, "Could not obtain video stream from link"

    # Build HTTP headers for FFmpeg to satisfy Google Video's token check
    header_str = "".join(f"{k}: {v}\r\n" for k, v in http_headers.items())

    # Fast stream copy (-i before -ss so FFmpeg reads moov atom header at byte 0)
    cmd_copy = [
        "ffmpeg", "-y",
        "-headers", header_str,
        "-i", stream_url,
        "-ss", str(s_sec),
        "-t", str(duration),
        "-c:v", "copy",
        "-an",
        "-movflags", "+faststart",
        out_path
    ]
    res_copy = subprocess.run(cmd_copy, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if os.path.exists(out_path) and os.path.getsize(out_path) > 10000:
        sz = os.path.getsize(out_path) / (1024 * 1024)
        return True, f"{sz:.1f} MB (Silent B-roll)"

    # Fallback: ultrafast video-only re-encode if stream copy lands between keyframes
    cmd_transcode = [
        "ffmpeg", "-y",
        "-headers", header_str,
        "-i", stream_url,
        "-ss", str(s_sec),
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "22",
        "-an",
        "-movflags", "+faststart",
        out_path
    ]
    res_transcode = subprocess.run(cmd_transcode, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if os.path.exists(out_path) and os.path.getsize(out_path) > 10000:
        sz = os.path.getsize(out_path) / (1024 * 1024)
        return True, f"{sz:.1f} MB (Silent B-roll)"

    err_detail = res_transcode.stderr[-200:] if res_transcode.stderr else res_copy.stderr[-200:]
    return False, f"FFmpeg failed: {err_detail.strip()}"


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

    elif tool_info["type"] == "youtube":
        st.markdown("#### Precision Clip Trimmer")
        yt_url = st.text_input("YouTube Video URL:", placeholder="https://www.youtube.com/watch?v=...")
        col_t1, col_t2 = st.columns(2)
        with col_t1:
            start_time = st.text_input("Start Timestamp (MM:SS):", value="03:15")
        with col_t2:
            end_time = st.text_input("End Timestamp (MM:SS):", value="03:35")

        custom_label = st.text_input("Target File Title (Prompt/Label):", value="youtube_documentary_scene")
        yt_btn = st.button("Extract Clip (5s to 60s)", type="primary")

        if yt_btn:
            if not yt_url:
                st.warning("Please provide a valid YouTube URL.")
            else:
                filename = prompt_to_clean_filename(custom_label, "mp4")
                out_path = os.path.join(OUTPUT_DIR, filename)
                t0 = time.time()

                with st.spinner(f"Extracting silent video segment from {start_time} to {end_time}..."):
                    success, detail = cut_youtube(yt_url, start_time, end_time, out_path)

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
