import os
import re
import time
import zipfile
import io
from concurrent.futures import ThreadPoolExecutor
import requests
import streamlit as st
import streamlit.components.v1 as components

# =====================================================================
# CONFIGURATION & SECRETS
# =====================================================================
def get_secret(key: str, default: str = "") -> str:
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
MIN_IMAGE_SIZE_KB = 100.0
MAX_IMAGE_SIZE_MB = 10.0

GLOBAL_USER_AGENT = "BrollStudioAutomation/1.0 (documentary_research_tool; contact@studio.local)"
GRAMMAR_FILLERS = {"a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with", "between"}

# =====================================================================
# TOOL METADATA & REPOSITORIES
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
        "desc": "Direct client-side YouTube clipper: trims exact timestamp segments directly to your computer without cloud datacenter restrictions.",
        "type": "browser_cutter",
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
# QUERY ENGINE & SANITIZATION
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
# API ENGINES (RETURN DIRECT CDN URL + DOWNLOAD STREAM)
# =====================================================================
def fetch_stock_video(query: str, out_path: str) -> tuple[bool, str, str | None]:
    if not PEXELS_API_KEY:
        return False, "PEXELS_API_KEY missing from secrets", None
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "orientation": "landscape", "size": "large", "per_page": 5}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        videos = r.json().get("videos", [])
        if not videos:
            return False, "No matching clips found", None
        files = videos[0].get("video_files", [])
        stream = next((s for s in files if s.get("height") == 1080 or s.get("width") == 1920), files[0] if files else None)
        if not stream:
            return False, "No valid video stream", None
        cdn_url = stream["link"]
        ok, msg = download_stream(cdn_url, out_path, max_size_mb=MAX_VIDEO_SIZE_MB)
        return ok, msg, cdn_url
    except Exception as e:
        return False, str(e), None


def fetch_stock_photo(query: str, out_path: str) -> tuple[bool, str, str | None]:
    if not PEXELS_API_KEY:
        return False, "PEXELS_API_KEY missing from secrets", None
    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "orientation": "landscape", "per_page": 5}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        photos = r.json().get("photos", [])
        if not photos:
            return False, "No photos found", None
        src = photos[0].get("src", {})
        img_url = src.get("original") or src.get("large2x")
        ok, msg = download_stream(img_url, out_path, max_size_mb=MAX_IMAGE_SIZE_MB, min_size_kb=MIN_IMAGE_SIZE_KB)
        if not ok and src.get("large2x") and img_url != src.get("large2x"):
            img_url = src.get("large2x")
            ok, msg = download_stream(img_url, out_path, max_size_mb=MAX_IMAGE_SIZE_MB, min_size_kb=MIN_IMAGE_SIZE_KB)
        return ok, msg, img_url
    except Exception as e:
        return False, str(e), None


def fetch_pixabay_video(query: str, out_path: str) -> tuple[bool, str, str | None]:
    if not PIXABAY_API_KEY:
        return False, "PIXABAY_API_KEY missing from secrets", None
    url = "https://pixabay.com/api/videos/"
    params = {"key": PIXABAY_API_KEY, "q": query, "per_page": 5}
    try:
        r = requests.get(url, params=params, timeout=15)
        hits = r.json().get("hits", [])
        if not hits:
            return False, "No clips found", None
        streams = hits[0].get("videos", {})
        chosen = streams.get("large") or streams.get("medium") or streams.get("small")
        if not chosen or not chosen.get("url"):
            return False, "No downloadable stream", None
        cdn_url = chosen["url"]
        ok, msg = download_stream(cdn_url, out_path, max_size_mb=MAX_VIDEO_SIZE_MB)
        return ok, msg, cdn_url
    except Exception as e:
        return False, str(e), None


def fetch_pixabay_photo(query: str, out_path: str) -> tuple[bool, str, str | None]:
    if not PIXABAY_API_KEY:
        return False, "PIXABAY_API_KEY missing from secrets", None
    url = "https://pixabay.com/api/"
    params = {"key": PIXABAY_API_KEY, "q": query, "image_type": "photo", "orientation": "horizontal", "per_page": 5}
    try:
        r = requests.get(url, params=params, timeout=15)
        hits = r.json().get("hits", [])
        if not hits:
            return False, "No photos found", None
        img_url = hits[0].get("largeImageURL") or hits[0].get("imageURL")
        ok, msg = download_stream(img_url, out_path, max_size_mb=MAX_IMAGE_SIZE_MB, min_size_kb=MIN_IMAGE_SIZE_KB)
        return ok, msg, img_url
    except Exception as e:
        return False, str(e), None


def fetch_coverr_video(query: str, out_path: str) -> tuple[bool, str, str | None]:
    if not COVERR_API_KEY:
        return False, "COVERR_API_KEY missing from secrets", None
    url = "https://api.coverr.co/videos"
    headers = {"Authorization": f"Bearer {COVERR_API_KEY}"}
    params = {"query": query, "urls": "true", "page_size": 5}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        hits = r.json().get("hits") or r.json().get("videos") or []
        if not hits:
            return False, "No Coverr video found", None
        urls_obj = hits[0].get("urls", {})
        v_url = urls_obj.get("mp4_download") or urls_obj.get("mp4")
        ok, msg = download_stream(v_url, out_path, max_size_mb=MAX_VIDEO_SIZE_MB)
        return ok, msg, v_url
    except Exception as e:
        return False, str(e), None


def fetch_unsplash_photo(query: str, out_path: str) -> tuple[bool, str, str | None]:
    if not UNSPLASH_ACCESS_KEY:
        return False, "UNSPLASH_ACCESS_KEY missing from secrets", None
    url = "https://api.unsplash.com/search/photos"
    headers = {"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}
    params = {"query": query, "orientation": "landscape", "per_page": 5}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        results = r.json().get("results", [])
        if not results:
            return False, "No photos found", None
        img_url = results[0]["urls"].get("full") or results[0]["urls"].get("regular")
        ok, msg = download_stream(img_url, out_path, max_size_mb=MAX_IMAGE_SIZE_MB, min_size_kb=MIN_IMAGE_SIZE_KB)
        return ok, msg, img_url
    except Exception as e:
        return False, str(e), None


def fetch_nasa_broll(query: str, out_path: str) -> tuple[bool, str, str | None]:
    url = "https://images-api.nasa.gov/search"
    params = {"q": query, "media_type": "video"}
    try:
        r = requests.get(url, params=params, timeout=15)
        items = r.json().get("collection", {}).get("items", [])
        if not items:
            return False, "No NASA records found", None
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
                ok, msg = download_stream(chosen, out_path, max_size_mb=MAX_VIDEO_SIZE_MB)
                return ok, msg, chosen
        return False, "No downloadable MP4 asset found", None
    except Exception as e:
        return False, str(e), None


def fetch_wikimedia_image(query: str, out_path: str) -> tuple[bool, str, str | None]:
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
                ok, msg = download_stream(img_url, out_path, max_size_mb=MAX_IMAGE_SIZE_MB, min_size_kb=MIN_IMAGE_SIZE_KB)
                return ok, msg, img_url
        return False, "No archival stills found", None
    except Exception as e:
        return False, str(e), None


def fetch_loc_broll(query: str, out_path: str) -> tuple[bool, str, str | None]:
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
                            cdn_url = f["url"]
                            ok, msg = download_stream(cdn_url, out_path, max_size_mb=MAX_VIDEO_SIZE_MB)
                            return ok, msg, cdn_url
        return False, "No progressive MP4 found", None
    except Exception as e:
        return False, str(e), None


# Worker function for parallel multi-threading
def process_single_prompt(prompt: str, tool_name: str, ext: str):
    filename = prompt_to_clean_filename(prompt, ext)
    out_path = os.path.join(OUTPUT_DIR, filename)
    primary_q, fallback_q = get_search_queries(prompt)
    fetch_func = ENGINE_DISPATCH[tool_name]
    
    t0 = time.time()
    ok, detail, cdn_url = fetch_func(primary_q, out_path)
    if not ok and fallback_q and fallback_q != primary_q:
        ok, detail, cdn_url = fetch_func(fallback_q, out_path)
    elapsed = time.time() - t0
    
    return {
        "prompt": prompt,
        "filename": filename,
        "path": out_path,
        "ext": ext,
        "ok": ok,
        "detail": detail,
        "cdn_url": cdn_url,
        "elapsed": elapsed
    }


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
# STREAMLIT UI SETUP & STYLING
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

# Initialize session caches for download persistence
if "batch_results" not in st.session_state:
    st.session_state.batch_results = []
if "batch_zip_data" not in st.session_state:
    st.session_state.batch_zip_data = None
if "last_tool_used" not in st.session_state:
    st.session_state.last_tool_used = ""

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
        st.session_state.batch_results = []
        st.session_state.batch_zip_data = None
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

# Clear prior tool cache if switching repositories
if st.session_state.last_tool_used != selected_tool_name:
    st.session_state.batch_results = []
    st.session_state.batch_zip_data = None
    st.session_state.last_tool_used = selected_tool_name

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
            height=160,
            placeholder="cinematic drone flight over misty mountains\nbusy neon city street night traffic\nmodern corporate boardroom meeting"
        )
        
        col_btn1, col_btn2 = st.columns([1.2, 1])
        with col_btn1:
            start_btn = st.button(f"⚡ Start Fast Parallel Sourcing", type="primary", use_container_width=True)
        with col_btn2:
            if st.session_state.batch_results:
                if st.button("Clear Results", use_container_width=True):
                    st.session_state.batch_results = []
                    st.session_state.batch_zip_data = None
                    st.rerun()

        if start_btn:
            lines = [line.strip() for line in prompt_input.splitlines() if line.strip()]
            if not lines:
                st.warning("Please enter at least one visual prompt.")
            else:
                ext = tool_info["ext"]
                st.session_state.batch_results = []
                st.session_state.batch_zip_data = None

                with st.spinner(f"Downloading {len(lines)} asset(s) simultaneously at datacenter speed..."):
                    t_all = time.time()
                    
                    # Fetch all prompts in parallel
                    with ThreadPoolExecutor(max_workers=min(len(lines), 6)) as executor:
                        futures = [
                            executor.submit(process_single_prompt, line, selected_tool_name, ext)
                            for line in lines
                        ]
                        results = [f.result() for f in futures]
                    
                    st.session_state.batch_results = results
                    
                    # Pre-build instant ZIP once in session state
                    valid_paths = [r["path"] for r in results if r["ok"] and os.path.exists(r["path"])]
                    if valid_paths:
                        st.session_state.batch_zip_data = create_zip_bytes(valid_paths)

                st.success(f"✓ All {len(lines)} assets sourced in {time.time() - t_all:.1f}s total!")
                st.rerun()

        # Render results from session state (survives clicks without reloading)
        if st.session_state.batch_results:
            results = st.session_state.batch_results
            successful = [r for r in results if r["ok"]]
            failed = [r for r in results if not r["ok"]]

            for r in failed:
                st.error(f"✖ **Failed:** \"{r['prompt']}\" — {r['detail']}")

            for r in successful:
                st.success(f"✓ **Retrieved:** `{r['filename']}` ({r['detail']} in {r['elapsed']:.1f}s)")

            if successful and st.session_state.batch_zip_data:
                st.markdown("### 📥 Save Assets to Your Computer")
                
                # Master 1-Click ZIP button (instant: data is already held in RAM)
                st.download_button(
                    label="⬇️ Download All Files to Computer (.ZIP)",
                    data=st.session_state.batch_zip_data,
                    file_name="broll_assets.zip",
                    mime="application/zip",
                    type="primary",
                    use_container_width=True
                )

                st.divider()

                st.markdown("#### Individual Asset Previews & Direct CDN Links")
                for r in successful:
                    col_preview, col_info = st.columns([1.5, 1.2])
                    with col_preview:
                        if r["ext"] == "mp4":
                            st.video(r["path"])
                        else:
                            st.image(r["path"])
                    with col_info:
                        st.write(f"**Prompt:** {r['prompt']}")
                        st.write(f"**Filename:** `{r['filename']}`")
                        
                        # Direct CDN button: Maximum internet bandwidth straight to user
                        if r["cdn_url"]:
                            st.link_button(
                                label="⚡ Instant Direct CDN Download (Fastest)",
                                url=r["cdn_url"],
                                use_container_width=True
                            )
                        
                        with open(r["path"], "rb") as item_f:
                            st.download_button(
                                label=f"⬇️ Download {r['filename']} from Server",
                                data=item_f.read(),
                                file_name=r["filename"],
                                mime="video/mp4" if r["ext"] == "mp4" else "image/jpeg",
                                key=f"dl_{r['filename']}",
                                use_container_width=True
                            )
                    st.write("---")

    elif tool_info["type"] == "browser_cutter":
        st.markdown("#### 🎬 YouTube Precision Cutter")
        st.caption("Paste your YouTube link below, drag the start/end handles to set your cut timestamps, and download the trimmed MP4 directly to your PC:")
        components.iframe("https://yt-clipper.com/", height=780, scrolling=True)

        st.divider()
        st.markdown("##### Alternative Precision Tools")
        c1, c2 = st.columns(2)
        with c1:
            st.link_button("⚡ Open Cobalt (Full MP4 Downloader)", "https://cobalt.tools/", use_container_width=True)
        with c2:
            st.link_button("🌐 Open YT-Clipper in New Window", "https://yt-clipper.com/", use_container_width=True)
