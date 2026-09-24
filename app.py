import os
import re
import time
import zipfile
import io
import subprocess
from concurrent.futures import ThreadPoolExecutor
import requests
import streamlit as st
import streamlit.components.v1 as components

# Locate FFmpeg
try:
    import imageio_ffmpeg
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_EXE = "ffmpeg"

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
UNSPLASH_ACCESS_KEY = get_secret("UNSPLASH_ACCESS_KEY", "")

OUTPUT_DIR = "downloaded_broll"
os.makedirs(OUTPUT_DIR, exist_ok=True)

MAX_WIKIMEDIA_SIZE_MB = 10.0
UNLIMITED_MEDIA_SIZE_MB = 350.0

GLOBAL_USER_AGENT = "BrollStudioArchive/2.0 (documentary_research_tool; contact@studio.local)"
GRAMMAR_FILLERS = {"a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with", "between"}

# =====================================================================
# CORE REPOSITORIES
# =====================================================================
TOOLS = {
    "Stock Video Footage (Pexels)": {
        "tag": "pexels_video",
        "ext": "mp4",
        "desc": "Cinematic modern stock footage from Pexels edge servers.",
        "type": "video",
        "auth_key": "PEXELS_API_KEY"
    },
    "Stock Photos (Pexels)": {
        "tag": "pexels_photo",
        "ext": "jpg",
        "desc": "High-resolution modern photography and portraits. No size restriction.",
        "type": "photo",
        "auth_key": "PEXELS_API_KEY"
    },
    "Pixabay Video Footage": {
        "tag": "pixabay_video",
        "ext": "mp4",
        "desc": "Commercial stock video and natural scenery clips.",
        "type": "video",
        "auth_key": "PIXABAY_API_KEY"
    },
    "Pixabay Stock Photos": {
        "tag": "pixabay_photo",
        "ext": "jpg",
        "desc": "Commercial stock stills, landscapes, and architecture. No size restriction.",
        "type": "photo",
        "auth_key": "PIXABAY_API_KEY"
    },
    "Unsplash Editorial Photos": {
        "tag": "unsplash_photo",
        "ext": "jpg",
        "desc": "Editorial character portraits and fine-art documentary stills. No size restriction.",
        "type": "photo",
        "auth_key": "UNSPLASH_ACCESS_KEY"
    },
    "Wikimedia Commons Stills": {
        "tag": "wikimedia_still",
        "ext": "jpg",
        "desc": "Public domain historical maps, archival illustrations, and manuscripts (Strictly ≤ 10MB).",
        "type": "photo",
        "auth_key": None
    }
}

# =====================================================================
# FILENAME & QUERY ENGINE
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


def download_stream(url: str, output_path: str, max_size_mb: float = UNLIMITED_MEDIA_SIZE_MB) -> tuple[bool, str]:
    headers = {"User-Agent": GLOBAL_USER_AGENT}
    try:
        with requests.get(url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            total_bytes = int(r.headers.get("content-length", 0))
            total_mb = total_bytes / (1024 * 1024) if total_bytes else 0

            if max_size_mb and total_mb > max_size_mb:
                return False, f"Exceeded size limit ({total_mb:.1f} MB > {max_size_mb:.0f} MB)"

            with open(output_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=131072):
                    if chunk:
                        f.write(chunk)

            final_mb = os.path.getsize(output_path) / (1024 * 1024)
            return True, f"{final_mb:.1f} MB"
    except Exception as e:
        if os.path.exists(output_path):
            os.remove(output_path)
        return False, str(e)


def trim_video_stream(cdn_url: str, output_path: str, duration_sec: int) -> tuple[bool, str]:
    cmd_copy = [
        FFMPEG_EXE, "-y",
        "-ss", "00:00:00",
        "-i", cdn_url,
        "-t", str(duration_sec),
        "-c", "copy",
        "-movflags", "+faststart",
        output_path
    ]
    try:
        subprocess.run(cmd_copy, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            sz_mb = os.path.getsize(output_path) / (1024 * 1024)
            return True, f"{sz_mb:.1f} MB ({duration_sec}s clip)"
    except Exception:
        pass

    return download_stream(cdn_url, output_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)


# =====================================================================
# API ENGINES
# =====================================================================
def fetch_pexels_video(query: str, out_path: str, quality_choice: str, clip_seconds: int | None) -> tuple[bool, str, str | None]:
    if not PEXELS_API_KEY:
        return False, "PEXELS_API_KEY missing from secrets", None
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "orientation": "landscape", "per_page": 6}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=12)
        videos = r.json().get("videos", [])
        if not videos:
            return False, "No matching clips found", None

        files = videos[0].get("video_files", [])
        valid_files = [f for f in files if f.get("link")]
        valid_files.sort(key=lambda x: (x.get("height") or 0), reverse=True)

        chosen = None
        if quality_choice == "4K UHD (2160p)":
            chosen = next((f for f in valid_files if (f.get("height") or 0) >= 2160 or (f.get("width") or 0) >= 3840), None)
        elif quality_choice == "720p HD":
            chosen = next((f for f in valid_files if (f.get("height") or 0) == 720 or (f.get("width") or 0) == 1280), None)

        if not chosen:
            chosen = next((f for f in valid_files if (f.get("height") or 0) == 1080 or (f.get("width") or 0) == 1920), None)
        if not chosen:
            chosen = valid_files[0]

        cdn_url = chosen["link"]
        if clip_seconds:
            ok, msg = trim_video_stream(cdn_url, out_path, clip_seconds)
        else:
            ok, msg = download_stream(cdn_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
        return ok, msg, cdn_url
    except Exception as e:
        return False, str(e), None


def fetch_pixabay_video(query: str, out_path: str, quality_choice: str, clip_seconds: int | None) -> tuple[bool, str, str | None]:
    if not PIXABAY_API_KEY:
        return False, "PIXABAY_API_KEY missing from secrets", None
    url = "https://pixabay.com/api/videos/"
    params = {"key": PIXABAY_API_KEY, "q": query, "per_page": 6}
    try:
        r = requests.get(url, params=params, timeout=12)
        hits = r.json().get("hits", [])
        if not hits:
            return False, "No clips found", None

        streams = hits[0].get("videos", {})
        chosen = None

        if quality_choice == "4K UHD (2160p)":
            large = streams.get("large", {})
            if (large.get("height") or 0) >= 1440 or (large.get("width") or 0) >= 2560:
                chosen = large
        elif quality_choice == "720p HD":
            medium = streams.get("medium", {})
            if (medium.get("height") or 0) == 720 or (medium.get("width") or 0) == 1280:
                chosen = medium

        if not chosen or not chosen.get("url"):
            chosen = streams.get("large") or streams.get("medium") or streams.get("small")

        if not chosen or not chosen.get("url"):
            return False, "No downloadable stream found", None

        cdn_url = chosen["url"]
        if clip_seconds:
            ok, msg = trim_video_stream(cdn_url, out_path, clip_seconds)
        else:
            ok, msg = download_stream(cdn_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
        return ok, msg, cdn_url
    except Exception as e:
        return False, str(e), None


def fetch_pexels_photo(query: str, out_path: str, _q: str = "", _c: int | None = None) -> tuple[bool, str, str | None]:
    if not PEXELS_API_KEY:
        return False, "PEXELS_API_KEY missing from secrets", None
    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "orientation": "landscape", "per_page": 5}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=12)
        photos = r.json().get("photos", [])
        if not photos:
            return False, "No photos found", None
        src = photos[0].get("src", {})
        img_url = src.get("original") or src.get("large2x") or src.get("large")
        ok, msg = download_stream(img_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
        return ok, msg, img_url
    except Exception as e:
        return False, str(e), None


def fetch_pixabay_photo(query: str, out_path: str, _q: str = "", _c: int | None = None) -> tuple[bool, str, str | None]:
    if not PIXABAY_API_KEY:
        return False, "PIXABAY_API_KEY missing from secrets", None
    url = "https://pixabay.com/api/"
    params = {"key": PIXABAY_API_KEY, "q": query, "image_type": "photo", "orientation": "horizontal", "per_page": 5}
    try:
        r = requests.get(url, params=params, timeout=12)
        hits = r.json().get("hits", [])
        if not hits:
            return False, "No photos found", None
        img_url = hits[0].get("largeImageURL") or hits[0].get("imageURL")
        ok, msg = download_stream(img_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
        return ok, msg, img_url
    except Exception as e:
        return False, str(e), None


def fetch_unsplash_photo(query: str, out_path: str, _q: str = "", _c: int | None = None) -> tuple[bool, str, str | None]:
    if not UNSPLASH_ACCESS_KEY:
        return False, "UNSPLASH_ACCESS_KEY missing from secrets", None
    url = "https://api.unsplash.com/search/photos"
    headers = {"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}
    params = {"query": query, "orientation": "landscape", "per_page": 5}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=12)
        results = r.json().get("results", [])
        if not results:
            return False, "No photos found", None
        img_url = results[0]["urls"].get("full") or results[0]["urls"].get("regular")
        ok, msg = download_stream(img_url, out_path, max_size_mb=UNLIMITED_MEDIA_SIZE_MB)
        return ok, msg, img_url
    except Exception as e:
        return False, str(e), None


def fetch_wikimedia_stills(query: str, out_path: str, _q: str = "", _c: int | None = None) -> tuple[bool, str, str | None]:
    url = "https://commons.wikimedia.org/w/api.php"
    headers = {"User-Agent": GLOBAL_USER_AGENT}
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap -filetype:pdf -filetype:audio",
        "gsrnamespace": "6",
        "gsrlimit": "10",
        "prop": "imageinfo",
        "iiprop": "url|mime|size",
        "iiurlwidth": "2560"
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        pages = r.json().get("query", {}).get("pages", {})
        if not pages:
            return False, "No matching archival records found", None

        valid_mimes = {"image/jpeg", "image/png", "image/webp"}

        for _, page in pages.items():
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]

            if info.get("mime", "").lower() not in valid_mimes:
                continue

            raw_url = info.get("url")
            thumb_url = info.get("thumburl")
            raw_mb = info.get("size", 0) / (1024 * 1024)

            chosen_url = raw_url
            if raw_mb > MAX_WIKIMEDIA_SIZE_MB:
                if thumb_url:
                    chosen_url = thumb_url
                else:
                    continue

            ok, detail = download_stream(chosen_url, out_path, max_size_mb=MAX_WIKIMEDIA_SIZE_MB)
            if ok:
                return True, f"{detail} (Archival Stills <= 10MB)", chosen_url

        return False, "No archival image found within 10MB limit", None
    except Exception as e:
        return False, str(e), None


# =====================================================================
# THREADED DISPATCH & MEMORY ZIP BUILDING
# =====================================================================
ENGINE_MAP = {
    "Stock Video Footage (Pexels)": fetch_pexels_video,
    "Stock Photos (Pexels)": fetch_pexels_photo,
    "Pixabay Video Footage": fetch_pixabay_video,
    "Pixabay Stock Photos": fetch_pixabay_photo,
    "Unsplash Editorial Photos": fetch_unsplash_photo,
    "Wikimedia Commons Stills": fetch_wikimedia_stills
}


def process_single_prompt(prompt: str, tool_name: str, ext: str, quality_choice: str, clip_seconds: int | None):
    filename = prompt_to_clean_filename(prompt, ext)
    out_path = os.path.join(OUTPUT_DIR, filename)
    primary_q, fallback_q = get_search_queries(prompt)
    fetch_func = ENGINE_MAP[tool_name]

    t0 = time.time()
    ok, detail, cdn_url = fetch_func(primary_q, out_path, quality_choice, clip_seconds)
    if not ok and fallback_q and fallback_q != primary_q:
        ok, detail, cdn_url = fetch_func(fallback_q, out_path, quality_choice, clip_seconds)
    elapsed = time.time() - t0

    return {
        "prompt": prompt,
        "filename": filename,
        "path": out_path,
        "ok": ok,
        "detail": detail,
        "cdn_url": cdn_url,
        "elapsed": elapsed
    }


def create_in_memory_zip(file_list: list[str]) -> bytes:
    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for f in file_list:
            if os.path.exists(f):
                zf.write(f, arcname=os.path.basename(f))
    mem_zip.seek(0)
    return mem_zip.read()


# =====================================================================
# STREAMLIT UI SETUP
# =====================================================================
st.set_page_config(page_title="Automation Tools By Shoaib Malik", page_icon="🎬", layout="wide")

st.markdown("""
<style>
    div[data-testid="stRadio"] label p {
        font-size: 1.15rem !important;
        font-weight: 600 !important;
        line-height: 1.8 !important;
    }
    div[data-testid="stRadio"] [data-baseweb="radio"] div:first-child {
        transform: scale(1.35);
        margin-right: 0.6rem !important;
    }
    h3, h4 {
        font-weight: 700 !important;
    }
</style>
""", unsafe_allow_html=True)

# =====================================================================
# SECURITY LOGIN GATEKEEPER
# =====================================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.markdown("# 🎬 **Automation Tools By Shoaib Malik**")
    st.caption("High-speed B-roll and archival pipeline for documentary editing.")
    st.divider()

    _, col_login, _ = st.columns([1, 1.2, 1])
    with col_login:
        st.markdown("### 🔒 **Security Verification**")
        st.caption("Please log in with your authorized credentials to access this tool.")
        with st.form("login_form"):
            input_username = st.text_input("Username")
            input_password = st.text_input("Password", type="password")
            submit_login = st.form_submit_button("Unlock Studio", type="primary", use_container_width=True)

            if submit_login:
                if input_username == "Malik" and input_password == "Shoaib@10":
                    st.session_state.authenticated = True
                    st.success("Access Granted!")
                    st.rerun()
                else:
                    st.error("Incorrect Username or Password. Access Denied.")

    st.stop()

# Initialize session caches
if "batch_results" not in st.session_state:
    st.session_state.batch_results = []
if "zip_bytes" not in st.session_state:
    st.session_state.zip_bytes = None
if "last_tool_used" not in st.session_state:
    st.session_state.last_tool_used = ""

# =====================================================================
# AUTHENTICATED WORKSPACE
# =====================================================================
col_header, col_logout = st.columns([4, 1])
with col_header:
    st.markdown("# 🎬 **Automation Tools By Shoaib Malik**")
    st.caption("⚡ Direct stream sourcing | Pre-cached ZIP delivery | 10s default cuts")
with col_logout:
    st.write("")
    if st.button("🔒 **Log Out**", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.batch_results = []
        st.session_state.zip_bytes = None
        st.rerun()

st.divider()

col_nav, col_main = st.columns([1, 2.3])

with col_nav:
    st.markdown("### **Select Source Tool**")
    selected_tool_name = st.radio(
        "Available Repositories:",
        list(TOOLS.keys()),
        index=0,
        label_visibility="collapsed"
    )

tool_info = TOOLS[selected_tool_name]

if st.session_state.last_tool_used != selected_tool_name:
    st.session_state.batch_results = []
    st.session_state.zip_bytes = None
    st.session_state.last_tool_used = selected_tool_name

with col_main:
    st.markdown(f"### **Tool: {selected_tool_name}**")
    st.info(tool_info["desc"])

    auth_key_name = tool_info.get("auth_key")
    if auth_key_name:
        current_key = globals().get(auth_key_name, "")
        if not current_key:
            st.warning(f"⚠️ `{auth_key_name}` is not configured in your Streamlit Secrets vault.")

    quality_choice = "1080p Full HD"
    clip_seconds = 10

    if tool_info["type"] == "video":
        col_q1, col_q2 = st.columns(2)
        with col_q1:
            st.markdown("**Clip Length**")
            clip_label = st.selectbox(
                "Clip Length",
                ["10 Seconds (Default)", "15 Seconds", "Full Video Length"],
                index=0,
                label_visibility="collapsed"
            )
            if clip_label == "10 Seconds (Default)":
                clip_seconds = 10
            elif clip_label == "15 Seconds":
                clip_seconds = 15
            else:
                clip_seconds = None

        with col_q2:
            st.markdown("**Quality**")
            quality_choice = st.selectbox(
                "Quality",
                ["1080p Full HD", "4K UHD (2160p)", "720p HD"],
                index=0,
                label_visibility="collapsed"
            )

    st.markdown("**Visual Prompts (one prompt per line)**")
    prompt_input = st.text_area(
        "Visual Prompts",
        height=160,
        placeholder="cinematic drone flight over misty mountains\nbusy neon city street night traffic\nmodern corporate boardroom meeting",
        label_visibility="collapsed"
    )

    col_btn1, col_btn2 = st.columns([1.3, 1])
    with col_btn1:
        start_btn = st.button("⚡ **Start Fast Parallel Sourcing**", type="primary", use_container_width=True)
    with col_btn2:
        if st.session_state.batch_results:
            if st.button("**Clear Results**", use_container_width=True):
                st.session_state.batch_results = []
                st.session_state.zip_bytes = None
                st.rerun()

    if start_btn:
        lines = [line.strip() for line in prompt_input.splitlines() if line.strip()]
        if not lines:
            st.warning("Please enter at least one visual prompt.")
        else:
            ext = tool_info["ext"]
            st.session_state.batch_results = []
            st.session_state.zip_bytes = None

            with st.spinner(f"Fetching {len(lines)} asset(s) simultaneously from edge servers..."):
                t_all = time.time()

                with ThreadPoolExecutor(max_workers=min(len(lines), 8)) as executor:
                    futures = [
                        executor.submit(process_single_prompt, line, selected_tool_name, ext, quality_choice, clip_seconds)
                        for line in lines
                    ]
                    results = [f.result() for f in futures]

                st.session_state.batch_results = results

                valid_paths = [r["path"] for r in results if r["ok"] and os.path.exists(r["path"])]
                if valid_paths:
                    st.session_state.zip_bytes = create_in_memory_zip(valid_paths)

            st.success(f"✓ Retrieved {len(valid_paths)} of {len(lines)} items in {time.time() - t_all:.1f}s total!")
            st.rerun()

    # Results View
    if st.session_state.batch_results:
        results = st.session_state.batch_results
        successful = [r for r in results if r["ok"]]
        failed = [r for r in results if not r["ok"]]

        if successful:
            st.markdown("### **Download Your Sourced Assets**")

            # Sequential Multi-Downloader via Blob Conversion (Forces real downloads for Photos & Videos)
            cdn_links = [{"url": r["cdn_url"], "name": r["filename"]} for r in successful if r.get("cdn_url")]

            if cdn_links:
                js_code = """
                <script>
                async function downloadOneByOne() {
                    const links = """ + str(cdn_links) + """;
                    const btn = document.getElementById('seqBtn');
                    btn.disabled = true;
                    btn.style.opacity = '0.6';

                    for (let i = 0; i < links.length; i++) {
                        const item = links[i];
                        btn.innerText = '⚡ Downloading (' + (i + 1) + '/' + links.length + ')...';
                        try {
                            const res = await fetch(item.url);
                            const blob = await res.blob();
                            const blobUrl = window.URL.createObjectURL(blob);
                            const a = document.createElement('a');
                            a.style.display = 'none';
                            a.href = blobUrl;
                            a.download = item.name;
                            document.body.appendChild(a);
                            a.click();
                            window.URL.revokeObjectURL(blobUrl);
                            document.body.removeChild(a);
                        } catch (err) {
                            // Fallback if CORS blocks client-side fetch: open direct download stream
                            const a = document.createElement('a');
                            a.href = item.url;
                            a.download = item.name;
                            a.target = '_blank';
                            document.body.appendChild(a);
                            a.click();
                            document.body.removeChild(a);
                        }
                        if (i < links.length - 1) {
                            await new Promise(r => setTimeout(r, 2000));
                        }
                    }

                    btn.disabled = false;
                    btn.style.opacity = '1';
                    btn.innerText = '✓ All Files Downloaded!';
                }
                </script>
                <div style="padding: 2px 0;">
                    <button id="seqBtn" onclick="downloadOneByOne()" style="
                        background: linear-gradient(135deg, #00C853 0%, #009624 100%);
                        color: white;
                        border: none;
                        padding: 13px 20px;
                        font-size: 15px;
                        font-weight: 700;
                        border-radius: 8px;
                        cursor: pointer;
                        width: 100%;
                        margin-bottom: 6px;
                        box-shadow: 0 4px 6px rgba(0,0,0,0.12);
                    ">
                        ⚡ Download One-by-One (2s Gap - Max Regional Speed)
                    </button>
                </div>
                """
                components.html(js_code, height=65)

            # Master ZIP Archive
            if st.session_state.zip_bytes:
                zip_mb = len(st.session_state.zip_bytes) / (1024 * 1024)
                st.download_button(
                    label=f"📦 **Download All as Single Archive (.ZIP) — [{zip_mb:.1f} MB]**",
                    data=st.session_state.zip_bytes,
                    file_name="broll_assets.zip",
                    mime="application/zip",
                    type="primary",
                    use_container_width=True
                )

        st.divider()
        st.markdown("#### **Sourced File Status**")

        for r in failed:
            st.error(f"✖ **Failed:** \"{r['prompt']}\" — {r['detail']}")

        for r in successful:
            st.success(f"✓ **Saved:** `{r['filename']}` — {r['detail']} (Fetched in {r['elapsed']:.1f}s)")
