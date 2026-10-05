import os
import re
import io
import subprocess
from concurrent.futures import ThreadPoolExecutor
import requests
import streamlit as st
import streamlit.components.v1 as components

# Locate FFmpeg reliably
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

GLOBAL_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
GRAMMAR_FILLERS = {"a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with", "between", "from", "by", "that", "this", "these", "those", "draws", "creates", "shows", "about"}

# =====================================================================
# QUERY SIMPLIFIER
# =====================================================================
def get_query_candidates(raw_prompt: str) -> list[str]:
    clean = re.sub(r"[^\w\s-]", " ", raw_prompt).strip()
    clean = re.sub(r"\s+", " ", clean)
    candidates = [clean]

    # Candidate 2: Strip grammar fillers
    words = [w for w in clean.split() if w.lower() not in GRAMMAR_FILLERS]
    keyword_q = " ".join(words)
    if keyword_q and keyword_q != clean:
        candidates.append(keyword_q)

    # Candidate 3: Core first 2-3 essential words
    if len(words) > 2:
        candidates.append(" ".join(words[:2]))
    if len(words) > 1 and words[0] not in candidates:
        candidates.append(words[0])

    return candidates

# =====================================================================
# REPOSITORIES & TOOLS
# =====================================================================
TOOLS = {
    "Stock Video Footage (Pexels)": {
        "tag": "pexels_video",
        "ext": "mp4",
        "desc": "High-bitrate cinematic modern b-roll, drone landscapes, highways, and commercial clips.",
        "type": "video",
        "auth_key": "PEXELS_API_KEY"
    },
    "Stock Photos (Pexels)": {
        "tag": "pexels_photo",
        "ext": "jpg",
        "desc": "Modern commercial photography, clean studio portraits, architecture, and technology stills.",
        "type": "photo",
        "auth_key": "PEXELS_API_KEY"
    },
    "Pixabay Video Footage": {
        "tag": "pixabay_video",
        "ext": "mp4",
        "desc": "Diverse royalty-free nature scenes, slow motion wildlife, motion backgrounds, and time-lapses.",
        "type": "video",
        "auth_key": "PIXABAY_API_KEY"
    },
    "Pixabay Stock Photos": {
        "tag": "pixabay_photo",
        "ext": "jpg",
        "desc": "High-resolution stock illustrations, environmental backgrounds, and commercial editorial stills.",
        "type": "photo",
        "auth_key": "PIXABAY_API_KEY"
    },
    "Unsplash Editorial Photos": {
        "tag": "unsplash_photo",
        "ext": "jpg",
        "desc": "Award-winning artistic lighting, character portraits, street photography, and editorial framing.",
        "type": "photo",
        "auth_key": "UNSPLASH_ACCESS_KEY"
    },
    "Wikimedia Commons Stills": {
        "tag": "wikimedia_still",
        "ext": "jpg",
        "desc": "Public domain archive: antique cartography maps, scientific diagrams, manuscripts, and artwork.",
        "type": "photo",
        "auth_key": None
    },
    "Library of Congress (Historic Film & Video)": {
        "tag": "loc_video",
        "ext": "mp4",
        "desc": "Early 20th-century motion pictures (1890s–1950s), Wright Brothers flights, and historical newsreels.",
        "type": "video",
        "auth_key": None
    },
    "Library of Congress (Historic Photos)": {
        "tag": "loc_photo",
        "ext": "jpg",
        "desc": "Civil War glass negatives, Great Depression (Dorothea Lange), historical architecture, and vintage maps.",
        "type": "photo",
        "auth_key": None
    },
    "iStock Photo Explorer (Search & Copy URL)": {
        "tag": "istock_search",
        "ext": "jpg",
        "desc": "Search live iStock catalog by prompt, preview top 3 matches, and copy official URLs with 1-click.",
        "type": "catalog_explorer",
        "source": "istock",
        "auth_key": None
    },
    "Shutterstock Photo Explorer (Search & Copy URL)": {
        "tag": "shutterstock_search",
        "ext": "jpg",
        "desc": "Search live Shutterstock catalog by prompt, preview top 3 matches, and copy official URLs with 1-click.",
        "type": "catalog_explorer",
        "source": "shutterstock",
        "auth_key": None
    }
}

# =====================================================================
# STREAMING & FAST TRIMMING PIPELINE
# =====================================================================
def prompt_to_clean_filename(prompt: str, ext: str, index: int = 1) -> str:
    clean = re.sub(r'[\\/*?:"<>|]', "", prompt)
    clean = re.sub(r"[^\w\s-]", "", clean).strip()
    clean = re.sub(r"[\s-]+", "_", clean).lower()
    base_name = clean[:35].strip("_") or "media_asset"
    return f"{base_name}_{index}.{ext}"


def fetch_direct_buffer(url: str, referer: str = "https://www.google.com/") -> bytes | None:
    headers = {
        "User-Agent": GLOBAL_USER_AGENT,
        "Referer": referer,
        "Accept": "*/*"
    }
    try:
        r = requests.get(url, headers=headers, timeout=25)
        if r.status_code == 200 and len(r.content) > 1000:
            return r.content
    except Exception:
        pass
    return None


def trim_video_buffer(cdn_url: str, duration_sec: int | None = 10, target_height: int = 1080) -> bytes | None:
    raw_bytes = fetch_direct_buffer(cdn_url)
    if not raw_bytes:
        return None
    if duration_sec is None:
        return raw_bytes

    pid_tag = f"{os.getpid()}_{time.time_ns()}"
    temp_raw = os.path.join(OUTPUT_DIR, f"temp_raw_{pid_tag}.mp4")
    temp_cut = os.path.join(OUTPUT_DIR, f"temp_cut_{pid_tag}.mp4")

    try:
        with open(temp_raw, "wb") as f:
            f.write(raw_bytes)

        # Precise slice: limit duration, scale appropriately, and cap bitrate to ~4500k (keeping 10s under 6MB)
        cmd = [
            FFMPEG_EXE, "-y",
            "-ss", "00:00:00",
            "-i", temp_raw,
            "-t", str(duration_sec),
            "-vf", f"scale=-2:'min({target_height},ih)'",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-b:v", "4500k",
            "-maxrate", "5000k",
            "-bufsize", "10000k",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            temp_cut
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)

        if os.path.exists(temp_cut) and os.path.getsize(temp_cut) > 1000:
            with open(temp_cut, "rb") as f:
                return f.read()
    except Exception:
        pass
    finally:
        for p in (temp_raw, temp_cut):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    return raw_bytes

# =====================================================================
# SEARCH FUNCTIONS WITH FALLBACK LOOP
# =====================================================================
def fetch_top3_pexels_video(raw_query: str, quality_choice: str = "1080p Full HD") -> list[dict]:
    if not PEXELS_API_KEY:
        return []

    target_h = 1080
    if "4K" in quality_choice:
        target_h = 2160
    elif "720p" in quality_choice:
        target_h = 720

    headers = {"Authorization": PEXELS_API_KEY}
    candidates = get_query_candidates(raw_query)

    for q in candidates:
        url = "https://api.pexels.com/videos/search"
        params = {"query": q, "orientation": "landscape", "per_page": 6}
        try:
            r = requests.get(url, headers=headers, params=params, timeout=10)
            if r.status_code == 200:
                videos = r.json().get("videos", [])
                if videos:
                    results = []
                    for v in videos:
                        files = [f for f in v.get("video_files", []) if f.get("link")]
                        files.sort(key=lambda x: abs((x.get("height") or 0) - target_h))
                        chosen = files[0] if files else None

                        if chosen:
                            results.append({
                                "title": f"Pexels Video {v.get('id')}",
                                "stream_url": chosen["link"],
                                "target_height": target_h,
                                "page_url": v.get("url")
                            })
                        if len(results) == 3:
                            break
                    if results:
                        return results
        except Exception:
            continue
    return []


def fetch_top3_pixabay_video(raw_query: str, quality_choice: str = "1080p Full HD") -> list[dict]:
    if not PIXABAY_API_KEY:
        return []

    target_h = 1080
    if "4K" in quality_choice:
        target_h = 2160
    elif "720p" in quality_choice:
        target_h = 720

    candidates = get_query_candidates(raw_query)
    for q in candidates:
        url = "https://pixabay.com/api/videos/"
        params = {"key": PIXABAY_API_KEY, "q": q, "per_page": 6}
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                hits = r.json().get("hits", [])
                if hits:
                    results = []
                    for hit in hits:
                        streams = hit.get("videos", {})
                        if target_h >= 2160 and streams.get("large", {}).get("url"):
                            chosen = streams["large"]
                        elif target_h == 720 and streams.get("medium", {}).get("url"):
                            chosen = streams["medium"]
                        else:
                            chosen = streams.get("large") or streams.get("medium") or streams.get("small")

                        if chosen and chosen.get("url"):
                            results.append({
                                "title": hit.get("tags") or "Pixabay B-Roll Video",
                                "stream_url": chosen["url"],
                                "target_height": target_h,
                                "page_url": hit.get("pageURL")
                            })
                        if len(results) == 3:
                            break
                    if results:
                        return results
        except Exception:
            continue
    return []


def fetch_top3_loc_video(raw_query: str, quality_choice: str = "1080p Full HD") -> list[dict]:
    candidates = get_query_candidates(raw_query)
    for q in candidates:
        url = "https://www.loc.gov/film-and-videos/"
        params = {"q": q, "fo": "json", "fa": "online-format:video", "c": 6}
        try:
            r = requests.get(url, params=params, headers={"User-Agent": GLOBAL_USER_AGENT}, timeout=12)
            results = []
            for item in r.json().get("results", []):
                item_id = item.get("id")
                if not item_id:
                    continue
                m_res = requests.get(f"{item_id}?fo=json", headers={"User-Agent": GLOBAL_USER_AGENT}, timeout=8)
                if m_res.status_code == 200:
                    for res in m_res.json().get("resources", []):
                        for grp in res.get("files", []):
                            for f in grp:
                                if f.get("url", "").endswith(".mp4"):
                                    results.append({
                                        "title": item.get("title") or "Library of Congress Film",
                                        "stream_url": f["url"],
                                        "target_height": 1080,
                                        "page_url": item_id
                                    })
                                    break
                            if len(results) == 3:
                                break
                        if len(results) == 3:
                            break
                if len(results) == 3:
                    break
            if results:
                return results
        except Exception:
            continue
    return []


def fetch_top3_pexels_photo(raw_query: str, *args) -> list[dict]:
    if not PEXELS_API_KEY:
        return []
    candidates = get_query_candidates(raw_query)
    headers = {"Authorization": PEXELS_API_KEY}
    for q in candidates:
        url = "https://api.pexels.com/v1/search"
        params = {"query": q, "orientation": "landscape", "per_page": 3}
        try:
            r = requests.get(url, headers=headers, params=params, timeout=10)
            photos = r.json().get("photos", [])
            if photos:
                results = []
                for p in photos:
                    src = p.get("src", {})
                    u = src.get("large2x") or src.get("large") or src.get("original")
                    b = fetch_direct_buffer(u)
                    if b:
                        results.append({
                            "title": p.get("alt") or f"Pexels Still {p.get('id')}",
                            "media_bytes": b,
                            "page_url": p.get("url")
                        })
                if results:
                    return results
        except Exception:
            continue
    return []


def fetch_top3_pixabay_photo(raw_query: str, *args) -> list[dict]:
    if not PIXABAY_API_KEY:
        return []
    candidates = get_query_candidates(raw_query)
    for q in candidates:
        url = "https://pixabay.com/api/"
        params = {"key": PIXABAY_API_KEY, "q": q, "image_type": "photo", "orientation": "horizontal", "per_page": 3}
        try:
            r = requests.get(url, params=params, timeout=10)
            hits = r.json().get("hits", [])
            if hits:
                results = []
                for hit in hits:
                    u = hit.get("largeImageURL") or hit.get("webformatURL")
                    b = fetch_direct_buffer(u)
                    if b:
                        results.append({
                            "title": hit.get("tags") or "Pixabay Photo",
                            "media_bytes": b,
                            "page_url": hit.get("pageURL")
                        })
                if results:
                    return results
        except Exception:
            continue
    return []


def fetch_top3_unsplash_photo(raw_query: str, *args) -> list[dict]:
    if not UNSPLASH_ACCESS_KEY:
        return []
    candidates = get_query_candidates(raw_query)
    headers = {"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}
    for q in candidates:
        url = "https://api.unsplash.com/search/photos"
        params = {"query": q, "orientation": "landscape", "per_page": 3}
        try:
            r = requests.get(url, headers=headers, params=params, timeout=10)
            hits = r.json().get("results", [])
            if hits:
                results = []
                for p in hits:
                    u = p["urls"].get("regular") or p["urls"].get("full")
                    b = fetch_direct_buffer(u)
                    if b:
                        results.append({
                            "title": p.get("alt_description") or "Unsplash Editorial Still",
                            "media_bytes": b,
                            "page_url": p.get("links", {}).get("html")
                        })
                if results:
                    return results
        except Exception:
            continue
    return []


def fetch_top3_wikimedia_stills(raw_query: str, *args) -> list[dict]:
    candidates = get_query_candidates(raw_query)
    for q in candidates:
        url = "https://commons.wikimedia.org/w/api.php"
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"{q} filetype:bitmap",
            "gsrnamespace": "6",
            "gsrlimit": "6",
            "prop": "imageinfo",
            "iiprop": "url|size",
            "iiurlwidth": "1280"
        }
        try:
            r = requests.get(url, params=params, headers={"User-Agent": GLOBAL_USER_AGENT}, timeout=12)
            pages = r.json().get("query", {}).get("pages", {})
            results = []
            for _, page in pages.items():
                infos = page.get("imageinfo") or []
                if not infos:
                    continue
                u = infos[0].get("thumburl") or infos[0].get("url")
                b = fetch_direct_buffer(u)
                if b:
                    results.append({
                        "title": page.get("title", "").replace("File:", ""),
                        "media_bytes": b,
                        "page_url": infos[0].get("descriptionurl") or u
                    })
                if len(results) == 3:
                    break
            if results:
                return results
        except Exception:
            continue
    return []


def fetch_top3_loc_photo(raw_query: str, *args) -> list[dict]:
    candidates = get_query_candidates(raw_query)
    for q in candidates:
        url = "https://www.loc.gov/photos/"
        params = {"q": q, "fo": "json", "fa": "online-format:image", "c": 6}
        try:
            r = requests.get(url, params=params, headers={"User-Agent": GLOBAL_USER_AGENT}, timeout=12)
            results = []
            for item in r.json().get("results", []):
                img_urls = item.get("image_url", [])
                chosen = img_urls[-1] if isinstance(img_urls, list) and img_urls else None
                if chosen:
                    if chosen.startswith("//"):
                        chosen = "https:" + chosen
                    b = fetch_direct_buffer(chosen)
                    if b:
                        results.append({
                            "title": item.get("title") or "Library of Congress Photo",
                            "media_bytes": b,
                            "page_url": item.get("id") or chosen
                        })
                if len(results) == 3:
                    break
            if results:
                return results
        except Exception:
            continue
    return []


def search_istock_top3(raw_query: str, *args) -> list[dict]:
    candidates = get_query_candidates(raw_query)
    headers = {
        "User-Agent": GLOBAL_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"
    }
    for q in candidates:
        clean_q = requests.utils.quote(q)
        url = f"https://www.istockphoto.com/search/2/image?phrase={clean_q}&sort=mostpopular"
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                matches = re.findall(
                    r'href="(/photo/[^"]+)"[^>]*>.*?<img[^>]+src="([^">]+)"[^>]*alt="([^"]*)"',
                    r.text,
                    re.DOTALL
                )
                results = []
                for page_path, thumb_url, alt_text in matches:
                    full_page_url = f"https://www.istockphoto.com{page_path}" if not page_path.startswith("http") else page_path
                    img_data = fetch_direct_buffer(thumb_url, referer="https://www.istockphoto.com/")
                    if img_data:
                        results.append({
                            "title": alt_text.strip() or "iStock Photo",
                            "media_bytes": img_data,
                            "target_url": full_page_url
                        })
                    if len(results) == 3:
                        break
                if results:
                    return results
        except Exception:
            continue
    return []


def search_shutterstock_top3(raw_query: str, *args) -> list[dict]:
    candidates = get_query_candidates(raw_query)
    for q in candidates:
        clean_q = requests.utils.quote(q)
        api_url = f"https://www.shutterstock.com/_next/data/en/search/{clean_q}.json?term={clean_q}"
        headers_api = {
            "User-Agent": GLOBAL_USER_AGENT,
            "Accept": "application/json",
            "Referer": f"https://www.shutterstock.com/search/{clean_q}"
        }
        try:
            r = requests.get(api_url, headers=headers_api, timeout=10)
            if r.status_code == 200:
                assets = r.json().get("pageProps", {}).get("initialState", {}).get("search", {}).get("results", {}).get("data", [])
                results = []
                for item in assets:
                    img_id = item.get("id")
                    desc = item.get("description", "Shutterstock Photo")
                    displays = item.get("displays", {})
                    thumb_url = displays.get("260nw", {}).get("src") or displays.get("preview", {}).get("src")
                    if img_id and thumb_url:
                        img_data = fetch_direct_buffer(thumb_url, referer="https://www.shutterstock.com/")
                        if img_data:
                            results.append({
                                "title": desc,
                                "media_bytes": img_data,
                                "target_url": f"https://www.shutterstock.com/image-photo/{img_id}"
                            })
                    if len(results) == 3:
                        break
                if results:
                    return results
        except Exception:
            continue
    return []


TOOL_DISPATCHER = {
    "Stock Video Footage (Pexels)": fetch_top3_pexels_video,
    "Stock Photos (Pexels)": fetch_top3_pexels_photo,
    "Pixabay Video Footage": fetch_top3_pixabay_video,
    "Pixabay Stock Photos": fetch_top3_pixabay_photo,
    "Unsplash Editorial Photos": fetch_top3_unsplash_photo,
    "Wikimedia Commons Stills": fetch_top3_wikimedia_stills,
    "Library of Congress (Historic Film & Video)": fetch_top3_loc_video,
    "Library of Congress (Historic Photos)": fetch_top3_loc_photo,
    "iStock Photo Explorer (Search & Copy URL)": search_istock_top3,
    "Shutterstock Photo Explorer (Search & Copy URL)": search_shutterstock_top3
}

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

# Security login
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.markdown("# 🎬 **Automation Tools By Shoaib Malik**")
    st.caption("High-speed B-roll & Public Domain Asset Explorer.")
    st.divider()

    _, col_login, _ = st.columns([1, 1.2, 1])
    with col_login:
        st.markdown("### 🔒 **Security Verification**")
        st.caption("Please log in with authorized credentials.")
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

# State memory
if "search_results" not in st.session_state:
    st.session_state.search_results = []
if "active_query" not in st.session_state:
    st.session_state.active_query = ""
if "active_tool" not in st.session_state:
    st.session_state.active_tool = ""
if "sliced_buffers" not in st.session_state:
    st.session_state.sliced_buffers = {}

# Header
col_header, col_logout = st.columns([4, 1])
with col_header:
    st.markdown("# 🎬 **Automation Tools By Shoaib Malik**")
    st.caption("⚡ Live Single Query Sourcing: Top 3 Results with Precision Controls")
with col_logout:
    st.write("")
    if st.button("🔒 **Log Out**", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.search_results = []
        st.session_state.sliced_buffers = {}
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

if st.session_state.active_tool != selected_tool_name:
    st.session_state.search_results = []
    st.session_state.active_query = ""
    st.session_state.sliced_buffers = {}
    st.session_state.active_tool = selected_tool_name

with col_main:
    st.markdown(f"### **Tool: {selected_tool_name}**")
    st.info(tool_info["desc"])

    auth_key_name = tool_info.get("auth_key")
    if auth_key_name and not globals().get(auth_key_name, ""):
        st.warning(f"⚠️ `{auth_key_name}` is not configured in your Streamlit Secrets vault.")

    # Quality & Clip Controls (Video tools only)
    quality_choice = "1080p Full HD"
    clip_seconds = 10

    if tool_info["type"] == "video":
        col_ctl1, col_ctl2 = st.columns(2)
        with col_ctl1:
            st.markdown("**Clip Duration**")
            clip_label = st.selectbox(
                "Clip Duration",
                ["10 Seconds (Default)", "15 Seconds", "Full Video Length"],
                index=0,
                key="ctrl_duration",
                label_visibility="collapsed"
            )
            if clip_label == "10 Seconds (Default)":
                clip_seconds = 10
            elif clip_label == "15 Seconds":
                clip_seconds = 15
            else:
                clip_seconds = None

        with col_ctl2:
            st.markdown("**Video Quality**")
            quality_choice = st.selectbox(
                "Video Quality",
                ["1080p Full HD", "4K UHD (2160p)", "720p HD"],
                index=0,
                key="ctrl_quality",
                label_visibility="collapsed"
            )

    # Search Bar
    st.markdown("**Search Prompt**")
    col_input, col_action = st.columns([3, 1])
    with col_input:
        search_prompt = st.text_input(
            "Visual Search Prompt",
            placeholder="e.g. winter climate, golden gate bridge drone, corporate handshake",
            label_visibility="collapsed"
        )
    with col_action:
        run_search = st.button("🔍 **Search (Top 3)**", type="primary", use_container_width=True)

    if run_search:
        if not search_prompt.strip():
            st.warning("Please enter a search prompt.")
        else:
            st.session_state.active_query = search_prompt.strip()
            st.session_state.sliced_buffers = {}
            with st.spinner(f"Searching top 3 results from {selected_tool_name}..."):
                fetcher = TOOL_DISPATCHER[selected_tool_name]
                hits = fetcher(search_prompt.strip(), quality_choice)
                st.session_state.search_results = hits

            if not hits:
                st.error(f"No results found for '{search_prompt}'. Try broader keywords.")
            else:
                st.success(f"✓ Found top {len(hits)} matches!")

    # Display Results in a Row of 3
    if st.session_state.search_results:
        st.markdown("---")
        st.markdown(f"#### **Top 3 Results for: *\"{st.session_state.active_query}\"***")

        hits = st.session_state.search_results[:3]
        cols = st.columns(3)

        for idx, item in enumerate(hits):
            with cols[idx]:
                title = item.get("title", f"Result #{idx+1}")
                clean_title = (title[:36] + "...") if len(title) > 36 else title
                ext = tool_info["ext"]
                filename = prompt_to_clean_filename(st.session_state.active_query, ext, idx + 1)

                # 1. CATALOG EXPLORER (iStock & Shutterstock)
                if tool_info["type"] == "catalog_explorer":
                    if item.get("media_bytes"):
                        st.image(item["media_bytes"], use_container_width=True)
                    st.caption(f"**{clean_title}**")
                    raw_url = item.get("target_url", "")

                    btn_id = f"cp_btn_{idx}"
                    copy_html = f"""
                    <div style="margin-bottom: 10px;">
                        <input type="text" value="{raw_url}" id="url_val_{idx}" readonly style="
                            width: 100%; padding: 5px 8px; font-size: 11px; border: 1px solid #d0d7de;
                            border-radius: 4px; background: #f6f8fa; margin-bottom: 6px; box-sizing: border-box;
                        ">
                        <button id="{btn_id}" onclick="
                            const inp = document.getElementById('url_val_{idx}');
                            navigator.clipboard.writeText(inp.value);
                            const b = document.getElementById('{btn_id}');
                            b.innerText = '✓ Copied!';
                            b.style.background = '#2ea44f';
                            setTimeout(() => {{ b.innerText = '📋 Copy Link'; b.style.background = '#0969da'; }}, 1800);
                        " style="
                            width: 100%; background-color: #0969da; color: white; border: none;
                            padding: 7px 10px; font-size: 13px; font-weight: 600; border-radius: 6px; cursor: pointer;
                        ">
                            📋 Copy Link
                        </button>
                    </div>
                    """
                    components.html(copy_html, height=75)

                # 2. PHOTO MEDIA TOOLS
                elif tool_info["type"] == "photo":
                    if item.get("media_bytes"):
                        st.image(item["media_bytes"], use_container_width=True)
                        st.caption(f"**{clean_title}**")

                        st.download_button(
                            label=f"⬇️ **Download Photo #{idx+1}**",
                            data=item["media_bytes"],
                            file_name=filename,
                            mime="image/jpeg",
                            key=f"dl_btn_photo_{idx}",
                            type="primary" if idx == 0 else "secondary",
                            use_container_width=True
                        )
                    if item.get("page_url"):
                        st.link_button("🌐 Open Source", item["page_url"], use_container_width=True)

                # 3. VIDEO MEDIA TOOLS
                elif tool_info["type"] == "video":
                    stream_url = item.get("stream_url")
                    st.video(stream_url)
                    st.caption(f"**{clean_title}**")

                    slice_key = f"slice_{idx}_{st.session_state.active_query}"

                    if slice_key not in st.session_state.sliced_buffers:
                        if st.button(f"⚡ Trim {clip_seconds or 'Full'}s & Prepare", key=f"btn_slice_{idx}", use_container_width=True, type="primary"):
                            with st.spinner(f"Trimming {clip_seconds}s at {quality_choice}..."):
                                t_height = item.get("target_height", 1080)
                                cut_bytes = trim_video_buffer(stream_url, clip_seconds, target_height=t_height)
                                if cut_bytes:
                                    st.session_state.sliced_buffers[slice_key] = cut_bytes
                                    st.rerun()
                                else:
                                    st.error("Failed to slice stream buffer.")

                    if slice_key in st.session_state.sliced_buffers:
                        final_bytes = st.session_state.sliced_buffers[slice_key]
                        mb_size = len(final_bytes) / (1024 * 1024)
                        st.download_button(
                            label=f"⬇️ Save Video #{idx+1} ({mb_size:.1f} MB)",
                            data=final_bytes,
                            file_name=filename,
                            mime="video/mp4",
                            key=f"dl_btn_vid_{idx}",
                            type="primary",
                            use_container_width=True
                        )

                    if item.get("page_url"):
                        st.link_button("🌐 Source Link", item["page_url"], use_container_width=True)
