"""
generate_soul_dashboard.py
Generates soul_dashboard.html from data/octo_music_favorites.json.

Run anytime to refresh the dashboard with latest favorites + images:
  python generate_soul_dashboard.py

Output: soul_dashboard.html (open in any browser)
"""

import json
from pathlib import Path
from datetime import datetime

FAVORITES_FILE = Path(__file__).parent / "data" / "octo_music_favorites.json"
OUTPUT_FILE    = Path(__file__).parent / "soul_dashboard.html"


def load_favorites() -> list:
    if not FAVORITES_FILE.exists():
        return []
    data = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
    return data.get("favorites", [])


def tag_color(tag: str) -> str:
    t = tag.lower()
    if any(k in t for k in ["chicago", "empty bottle", "lounge ax", "hideout"]):
        return "#1DB954"
    if any(k in t for k in ["jazz", "aacm", "improvisation", "free"]):
        return "#F59B0B"
    if any(k in t for k in ["experimental", "avant", "noise", "drone"]):
        return "#A855F7"
    if any(k in t for k in ["folk", "americana", "country", "blues"]):
        return "#F97316"
    if any(k in t for k in ["post-rock", "post-hardcore", "math", "emo"]):
        return "#3B82F6"
    if any(k in t for k in ["electronic", "ambient", "minimal", "tape"]):
        return "#06B6D4"
    return "#6B7280"


def generate_html(favorites: list) -> str:
    # Stats
    total_artists = len(favorites)
    total_songs   = sum(len([s for s in f.get("songs", []) if "Unknown" not in s]) for f in favorites)
    venues        = sorted(set(f.get("best_show", {}).get("venue", "") for f in favorites if f.get("best_show", {}).get("venue")))
    all_tags      = sorted(set(
        t for f in favorites for t in f.get("mood_tags", [])
    ))
    with_images   = sum(1 for f in favorites if f.get("image_url"))

    built_at = ""
    if FAVORITES_FILE.exists():
        data     = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
        built_at = data.get("built_at", "")[:10]

    # Serialize favorites to JS-safe JSON
    js_data = json.dumps(favorites, ensure_ascii=False)

    venue_options = "\n".join(
        f'<option value="{v}">{v}</option>' for v in venues
    )

    # Tag filter buttons — top 25 most common
    from collections import Counter
    tag_counts = Counter(t for f in favorites for t in f.get("mood_tags", []))
    top_tags   = [t for t, _ in tag_counts.most_common(25)]
    tag_buttons = "\n".join(
        f'<button class="tag-btn" data-tag="{t}" style="--tag-color:{tag_color(t)}">{t}</button>'
        for t in top_tags
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Octodamus — Soul Dashboard</title>
<style>
  :root {{
    --bg:        #121212;
    --surface:   #181818;
    --elevated:  #282828;
    --hover:     #333333;
    --green:     #1DB954;
    --green-dim: #158a3e;
    --text:      #FFFFFF;
    --text2:     #B3B3B3;
    --text3:     #535353;
    --sidebar-w: 260px;
    --radius:    8px;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Circular', 'Helvetica Neue', Helvetica, Arial, sans-serif;
    display: flex;
    height: 100vh;
    overflow: hidden;
  }}

  /* ── SIDEBAR ── */
  #sidebar {{
    width: var(--sidebar-w);
    min-width: var(--sidebar-w);
    background: #000;
    display: flex;
    flex-direction: column;
    padding: 24px 16px;
    gap: 28px;
    overflow-y: auto;
    flex-shrink: 0;
  }}
  .logo {{
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 0 8px;
  }}
  .logo-icon {{ width: 40px; height: 40px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }}
  .logo-text {{
    font-size: 18px;
    font-weight: 700;
    letter-spacing: -0.3px;
    line-height: 1.1;
  }}
  .logo-sub {{
    font-size: 10px;
    color: var(--text3);
    letter-spacing: 1.5px;
    text-transform: uppercase;
  }}
  .sidebar-section h3 {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    color: var(--text3);
    padding: 0 8px;
    margin-bottom: 10px;
  }}
  #search-box {{
    width: 100%;
    background: var(--elevated);
    border: none;
    border-radius: 20px;
    padding: 10px 16px;
    color: var(--text);
    font-size: 13px;
    outline: none;
  }}
  #search-box::placeholder {{ color: var(--text3); }}
  #search-box:focus {{ background: var(--hover); }}
  #venue-filter {{
    width: 100%;
    background: var(--elevated);
    border: none;
    border-radius: var(--radius);
    padding: 10px 12px;
    color: var(--text);
    font-size: 13px;
    outline: none;
    cursor: pointer;
  }}
  #venue-filter option {{ background: var(--elevated); }}
  .tag-cloud {{ display: flex; flex-wrap: wrap; gap: 6px; padding: 0 4px; }}
  .tag-btn {{
    background: transparent;
    border: 1px solid var(--text3);
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 11px;
    color: var(--text2);
    cursor: pointer;
    transition: all 0.15s;
    white-space: nowrap;
  }}
  .tag-btn:hover {{ border-color: var(--tag-color, var(--green)); color: var(--tag-color, var(--green)); }}
  .tag-btn.active {{ background: var(--tag-color, var(--green)); border-color: var(--tag-color, var(--green)); color: #000; font-weight: 700; }}
  .sidebar-stats {{ padding: 0 8px; }}
  .stat-row {{ display: flex; justify-content: space-between; margin-bottom: 8px; }}
  .stat-label {{ font-size: 12px; color: var(--text3); }}
  .stat-val {{ font-size: 12px; color: var(--text2); font-weight: 600; }}
  .clear-btn {{
    width: 100%;
    background: var(--elevated);
    border: none;
    border-radius: 20px;
    padding: 10px;
    color: var(--text2);
    font-size: 12px;
    cursor: pointer;
    transition: background 0.15s;
    margin-top: 4px;
  }}
  .clear-btn:hover {{ background: var(--hover); color: var(--text); }}

  /* ── MAIN ── */
  #main {{
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }}
  #topbar {{
    background: linear-gradient(180deg, #2a1f3d 0%, var(--bg) 100%);
    padding: 28px 32px 20px;
    flex-shrink: 0;
  }}
  #topbar h1 {{
    font-size: 32px;
    font-weight: 900;
    letter-spacing: -0.5px;
    margin-bottom: 4px;
  }}
  #topbar .subtitle {{
    font-size: 13px;
    color: var(--text2);
    margin-bottom: 16px;
  }}
  .topbar-stats {{
    display: flex;
    gap: 24px;
  }}
  .topbar-stat {{
    display: flex;
    flex-direction: column;
    align-items: center;
    background: rgba(255,255,255,0.06);
    border-radius: var(--radius);
    padding: 10px 18px;
    min-width: 80px;
  }}
  .topbar-stat-num {{ font-size: 22px; font-weight: 700; color: var(--green); }}
  .topbar-stat-lbl {{ font-size: 11px; color: var(--text3); text-transform: uppercase; letter-spacing: 0.8px; margin-top: 2px; }}
  .sort-bar {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 32px;
    border-bottom: 1px solid rgba(255,255,255,0.06);
    flex-shrink: 0;
    background: var(--bg);
  }}
  .sort-bar span {{ font-size: 12px; color: var(--text3); }}
  .sort-btn {{
    background: transparent;
    border: 1px solid var(--text3);
    border-radius: 12px;
    padding: 4px 12px;
    font-size: 12px;
    color: var(--text2);
    cursor: pointer;
    transition: all 0.15s;
  }}
  .sort-btn.active, .sort-btn:hover {{ background: var(--green); border-color: var(--green); color: #000; font-weight: 700; }}
  #result-count {{ margin-left: auto; font-size: 12px; color: var(--text3); }}

  /* ── GRID ── */
  #grid-container {{
    flex: 1;
    overflow-y: auto;
    padding: 24px 32px 80px;
  }}
  #grid-container::-webkit-scrollbar {{ width: 6px; }}
  #grid-container::-webkit-scrollbar-track {{ background: transparent; }}
  #grid-container::-webkit-scrollbar-thumb {{ background: var(--text3); border-radius: 3px; }}
  #card-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 20px;
  }}
  .card {{
    background: var(--surface);
    border-radius: var(--radius);
    padding: 16px;
    cursor: pointer;
    transition: background 0.2s, transform 0.15s;
    position: relative;
  }}
  .card:hover {{ background: var(--elevated); transform: translateY(-2px); }}
  .card-img-wrap {{
    position: relative;
    width: 100%;
    padding-bottom: 100%;
    border-radius: 6px;
    overflow: hidden;
    margin-bottom: 14px;
    background: var(--elevated);
  }}
  .card-img {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    border-radius: 6px;
  }}
  .card-img-placeholder {{
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 52px;
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    border-radius: 6px;
    color: var(--text3);
  }}
  .play-btn {{
    position: absolute;
    bottom: 8px;
    right: 8px;
    width: 40px;
    height: 40px;
    background: var(--green);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transition: opacity 0.2s, transform 0.2s;
    transform: translateY(4px);
    font-size: 16px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.5);
  }}
  .card:hover .play-btn {{ opacity: 1; transform: translateY(0); }}
  .card-artist {{
    font-size: 14px;
    font-weight: 700;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 4px;
  }}
  .card-show {{
    font-size: 12px;
    color: var(--text2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 8px;
  }}
  .card-tags {{
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }}
  .tag-pill {{
    font-size: 10px;
    padding: 2px 7px;
    border-radius: 8px;
    font-weight: 600;
    white-space: nowrap;
  }}

  /* ── MODAL ── */
  #modal-overlay {{
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.7);
    z-index: 100;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }}
  #modal-overlay.open {{ display: flex; }}
  #modal {{
    background: #1e1e1e;
    border-radius: 12px;
    width: 100%;
    max-width: 680px;
    max-height: 85vh;
    overflow-y: auto;
    position: relative;
    box-shadow: 0 24px 80px rgba(0,0,0,0.8);
  }}
  #modal::-webkit-scrollbar {{ width: 6px; }}
  #modal::-webkit-scrollbar-thumb {{ background: var(--text3); border-radius: 3px; }}
  .modal-header {{
    display: flex;
    gap: 24px;
    padding: 28px 28px 20px;
    background: linear-gradient(180deg, #2a1a4a 0%, #1e1e1e 100%);
    border-radius: 12px 12px 0 0;
    align-items: flex-end;
  }}
  .modal-img-wrap {{
    width: 120px;
    height: 120px;
    border-radius: 6px;
    overflow: hidden;
    flex-shrink: 0;
    background: var(--elevated);
    box-shadow: 0 8px 24px rgba(0,0,0,0.5);
  }}
  .modal-img {{ width: 100%; height: 100%; object-fit: cover; }}
  .modal-img-ph {{
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 48px;
    background: linear-gradient(135deg, #1a1a2e, #0f3460);
  }}
  .modal-meta {{ flex: 1; }}
  .modal-type {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--text2); margin-bottom: 6px; }}
  .modal-artist {{ font-size: 28px; font-weight: 900; letter-spacing: -0.5px; margin-bottom: 4px; line-height: 1.1; }}
  .modal-show {{ font-size: 14px; color: var(--text2); margin-bottom: 10px; }}
  .modal-archive-link {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--green);
    text-decoration: none;
    border: 1px solid var(--green-dim);
    border-radius: 14px;
    padding: 5px 12px;
    transition: background 0.15s;
  }}
  .modal-archive-link:hover {{ background: rgba(29,185,84,0.1); }}
  .modal-close {{
    position: absolute;
    top: 16px;
    right: 16px;
    background: rgba(0,0,0,0.4);
    border: none;
    width: 30px;
    height: 30px;
    border-radius: 50%;
    color: var(--text2);
    font-size: 16px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.15s;
    z-index: 1;
  }}
  .modal-close:hover {{ background: rgba(0,0,0,0.7); color: var(--text); }}
  .modal-body {{ padding: 20px 28px 28px; }}
  .modal-note {{
    font-size: 14px;
    line-height: 1.7;
    color: var(--text2);
    font-style: italic;
    border-left: 3px solid var(--green);
    padding-left: 14px;
    margin-bottom: 22px;
  }}
  .modal-section-title {{
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    color: var(--text3);
    margin-bottom: 12px;
    margin-top: 20px;
  }}
  .song-list {{
    list-style: none;
  }}
  .song-row {{
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 8px 10px;
    border-radius: 6px;
    transition: background 0.15s;
  }}
  .song-row:hover {{ background: var(--elevated); }}
  .song-num {{ width: 20px; text-align: right; font-size: 13px; color: var(--text3); flex-shrink: 0; }}
  .song-name {{ font-size: 14px; flex: 1; }}
  .song-unknown {{ color: var(--text3); font-style: italic; }}
  .modal-tags {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .modal-tag-pill {{
    font-size: 12px;
    padding: 4px 12px;
    border-radius: 12px;
    font-weight: 600;
  }}

  #no-results {{
    display: none;
    grid-column: 1 / -1;
    text-align: center;
    padding: 60px 20px;
    color: var(--text3);
  }}
  #no-results .nr-icon {{ font-size: 48px; margin-bottom: 12px; }}
  #no-results p {{ font-size: 15px; }}
</style>
</head>
<body>

<!-- SIDEBAR -->
<aside id="sidebar">
  <div class="logo">
    <img class="logo-icon" src="/octo_logo.jpg" alt="Octodamus">
    <div>
      <div class="logo-text">Octodamus</div>
      <div class="logo-sub">Music Archive</div>
    </div>
  </div>

  <div class="sidebar-section">
    <h3>Search</h3>
    <input type="text" id="search-box" placeholder="Artist, venue, song..." />
  </div>

  <div class="sidebar-section">
    <h3>Venue</h3>
    <select id="venue-filter">
      <option value="">All venues</option>
      {venue_options}
    </select>
  </div>

  <div class="sidebar-section">
    <h3>Mood / Genre</h3>
    <div class="tag-cloud">
      {tag_buttons}
    </div>
  </div>

  <div class="sidebar-section">
    <h3>Stats</h3>
    <div class="sidebar-stats">
      <div class="stat-row"><span class="stat-label">Artists</span><span class="stat-val">{total_artists}</span></div>
      <div class="stat-row"><span class="stat-label">Known songs</span><span class="stat-val">{total_songs}</span></div>
      <div class="stat-row"><span class="stat-label">Venues</span><span class="stat-val">{len(venues)}</span></div>
      <div class="stat-row"><span class="stat-label">With photos</span><span class="stat-val">{with_images}</span></div>
      <div class="stat-row"><span class="stat-label">Built</span><span class="stat-val">{built_at}</span></div>
    </div>
  </div>

  <button class="clear-btn" onclick="clearFilters()">Clear all filters</button>
</aside>

<!-- MAIN -->
<div id="main">
  <div id="topbar">
    <h1>Music Archive</h1>
    <div class="subtitle">Aadam Jacobs Collection &nbsp;·&nbsp; Chicago Underground 1984–2019 &nbsp;·&nbsp; archive.org</div>
    <div class="topbar-stats">
      <div class="topbar-stat">
        <span class="topbar-stat-num">{total_artists}</span>
        <span class="topbar-stat-lbl">Artists</span>
      </div>
      <div class="topbar-stat">
        <span class="topbar-stat-num">2,492</span>
        <span class="topbar-stat-lbl">Shows</span>
      </div>
      <div class="topbar-stat">
        <span class="topbar-stat-num">{len(venues)}</span>
        <span class="topbar-stat-lbl">Venues</span>
      </div>
      <div class="topbar-stat">
        <span class="topbar-stat-num">{total_songs}</span>
        <span class="topbar-stat-lbl">Songs</span>
      </div>
    </div>
  </div>

  <div style="padding: 0 24px 24px;">
    <iframe style="border-radius:12px" src="https://open.spotify.com/embed/playlist/5nYNLjkGYKM5rWApig2nk2?utm_source=generator&theme=0" width="100%" height="152" frameBorder="0" allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" loading="lazy"></iframe>
  </div>

  <div class="sort-bar">
    <span>Sort by</span>
    <button class="sort-btn active" onclick="setSort('artist', this)">Artist A–Z</button>
    <button class="sort-btn" onclick="setSort('date', this)">Show Date</button>
    <button class="sort-btn" onclick="setSort('venue', this)">Venue</button>
    <span id="result-count"></span>
  </div>

  <div id="grid-container">
    <div id="card-grid"></div>
  </div>
</div>

<!-- MODAL -->
<div id="modal-overlay" onclick="closeModal(event)">
  <div id="modal">
    <button class="modal-close" onclick="closeModalBtn()">✕</button>
    <div class="modal-header">
      <div class="modal-img-wrap" id="modal-img-wrap"></div>
      <div class="modal-meta">
        <div class="modal-type">Octodamus Favorite</div>
        <div class="modal-artist" id="modal-artist"></div>
        <div class="modal-show" id="modal-show"></div>
        <a class="modal-archive-link" id="modal-link" href="#" target="_blank">
          ▶ Listen on archive.org
        </a>
      </div>
    </div>
    <div class="modal-body">
      <div class="modal-note" id="modal-note"></div>
      <div class="modal-section-title">Setlist</div>
      <ul class="song-list" id="modal-songs"></ul>
      <div class="modal-section-title" style="margin-top:20px">Tags</div>
      <div class="modal-tags" id="modal-tags"></div>
    </div>
  </div>
</div>

<script>
const RAW = {js_data};

const TAG_COLORS = {{}};
function tagColor(tag) {{
  const t = tag.toLowerCase();
  if (['chicago','empty bottle','lounge ax','hideout'].some(k => t.includes(k))) return '#1DB954';
  if (['jazz','aacm','improvisation','free'].some(k => t.includes(k))) return '#F59B0B';
  if (['experimental','avant','noise','drone'].some(k => t.includes(k))) return '#A855F7';
  if (['folk','americana','country','blues'].some(k => t.includes(k))) return '#F97316';
  if (['post-rock','post-hardcore','math','emo'].some(k => t.includes(k))) return '#3B82F6';
  if (['electronic','ambient','minimal','tape'].some(k => t.includes(k))) return '#06B6D4';
  return '#6B7280';
}}

let currentSort = 'artist';
let activeTag   = null;
let activeVenue = '';
let searchQ     = '';

function getSorted(data) {{
  const d = [...data];
  if (currentSort === 'artist') d.sort((a,b) => a.artist.localeCompare(b.artist));
  if (currentSort === 'date')   d.sort((a,b) => (a.best_show?.date||'').localeCompare(b.best_show?.date||''));
  if (currentSort === 'venue')  d.sort((a,b) => (a.best_show?.venue||'').localeCompare(b.best_show?.venue||''));
  return d;
}}

function getFiltered() {{
  return RAW.filter(f => {{
    if (activeVenue && f.best_show?.venue !== activeVenue) return false;
    if (activeTag   && !(f.mood_tags||[]).includes(activeTag)) return false;
    if (searchQ) {{
      const q = searchQ.toLowerCase();
      const haystack = [
        f.artist,
        f.best_show?.venue||'',
        f.best_show?.date||'',
        f.note||'',
        ...(f.songs||[]),
        ...(f.mood_tags||[]),
      ].join(' ').toLowerCase();
      if (!haystack.includes(q)) return false;
    }}
    return true;
  }});
}}

function renderGrid() {{
  const filtered = getSorted(getFiltered());
  const grid = document.getElementById('card-grid');
  const rc   = document.getElementById('result-count');
  rc.textContent = filtered.length === RAW.length
    ? `${{RAW.length}} artists`
    : `${{filtered.length}} of ${{RAW.length}} artists`;

  if (filtered.length === 0) {{
    grid.innerHTML = '<div id="no-results"><div class="nr-icon">🎵</div><p>No artists match your filters.</p></div>';
    return;
  }}

  grid.innerHTML = filtered.map((f, i) => {{
    const show  = f.best_show || {{}};
    const tags  = (f.mood_tags||[]).slice(0,3);
    const imgEl = f.image_url
      ? `<img class="card-img" src="${{f.image_url}}" alt="${{f.artist}}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
      : '';
    const phEl  = `<div class="card-img-placeholder" ${{f.image_url ? 'style="display:none"' : ''}}>🎵</div>`;
    const tagPills = tags.map(t => {{
      const c = tagColor(t);
      return `<span class="tag-pill" style="background:${{c}}22;color:${{c}}">${{t}}</span>`;
    }}).join('');

    return `<div class="card" onclick="openModal(${{i}}, ${{JSON.stringify(filtered).replace(/'/g,"&#39;")}})" data-idx="${{i}}">
      <div class="card-img-wrap">
        ${{imgEl}}${{phEl}}
        <div class="play-btn">▶</div>
      </div>
      <div class="card-artist">${{f.artist}}</div>
      <div class="card-show">${{show.date||''}} · ${{show.venue||''}}</div>
      <div class="card-tags">${{tagPills}}</div>
    </div>`;
  }}).join('');

  // Re-attach click handlers via event delegation
  grid.onclick = (e) => {{
    const card = e.target.closest('.card');
    if (!card) return;
    const idx = parseInt(card.dataset.idx);
    openModal(idx, filtered);
  }};
}}

function openModal(idx, arr) {{
  const f    = arr[idx];
  const show = f.best_show || {{}};
  const overlay = document.getElementById('modal-overlay');

  // Image
  const imgWrap = document.getElementById('modal-img-wrap');
  if (f.image_url) {{
    imgWrap.innerHTML = `<img class="modal-img" src="${{f.image_url}}" alt="${{f.artist}}" onerror="this.parentNode.innerHTML='<div class=\\"modal-img-ph\\">🎵</div>'">`;
  }} else {{
    imgWrap.innerHTML = '<div class="modal-img-ph">🎵</div>';
  }}

  document.getElementById('modal-artist').textContent = f.artist;
  document.getElementById('modal-show').textContent =
    `${{show.date||''}}  ·  ${{show.venue||''}}`;

  const archiveLink = document.getElementById('modal-link');
  archiveLink.href = `https://archive.org/details/${{show.identifier||''}}`;
  archiveLink.style.display = show.identifier ? 'inline-flex' : 'none';

  document.getElementById('modal-note').textContent = f.note || '';

  const songs = (f.songs||[]);
  document.getElementById('modal-songs').innerHTML = songs.length
    ? songs.map((s,i) => {{
        const isUnknown = s.toLowerCase().includes('unknown');
        return `<li class="song-row">
          <span class="song-num">${{i+1}}</span>
          <span class="song-name ${{isUnknown ? 'song-unknown' : ''}}">${{isUnknown ? 'Track not documented' : s}}</span>
        </li>`;
      }}).join('')
    : '<li class="song-row"><span class="song-name song-unknown">Setlist not documented</span></li>';

  const tags = f.mood_tags || [];
  document.getElementById('modal-tags').innerHTML = tags.map(t => {{
    const c = tagColor(t);
    return `<span class="modal-tag-pill" style="background:${{c}}22;color:${{c}};border:1px solid ${{c}}44">${{t}}</span>`;
  }}).join('');

  overlay.classList.add('open');
  document.body.style.overflow = 'hidden';
}}

function closeModal(e) {{
  if (e.target.id === 'modal-overlay') closeModalBtn();
}}
function closeModalBtn() {{
  document.getElementById('modal-overlay').classList.remove('open');
  document.body.style.overflow = '';
}}
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeModalBtn(); }});

function setSort(key, btn) {{
  currentSort = key;
  document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderGrid();
}}

document.getElementById('search-box').addEventListener('input', e => {{
  searchQ = e.target.value.trim();
  renderGrid();
}});

document.getElementById('venue-filter').addEventListener('change', e => {{
  activeVenue = e.target.value;
  renderGrid();
}});

document.querySelectorAll('.tag-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    const tag = btn.dataset.tag;
    if (activeTag === tag) {{
      activeTag = null;
      btn.classList.remove('active');
    }} else {{
      document.querySelectorAll('.tag-btn').forEach(b => b.classList.remove('active'));
      activeTag = tag;
      btn.classList.add('active');
    }}
    renderGrid();
  }});
}});

function clearFilters() {{
  searchQ     = '';
  activeTag   = null;
  activeVenue = '';
  document.getElementById('search-box').value = '';
  document.getElementById('venue-filter').value = '';
  document.querySelectorAll('.tag-btn').forEach(b => b.classList.remove('active'));
  renderGrid();
}}

// Initial render
renderGrid();
</script>
</body>
</html>"""


def main():
    favorites = load_favorites()
    if not favorites:
        print("[SoulDashboard] No favorites found. Run: python octo_music_archive.py --build")
        return

    html = generate_html(favorites)
    OUTPUT_FILE.write_text(html, encoding="utf-8")

    with_img = sum(1 for f in favorites if f.get("image_url"))
    print(f"[SoulDashboard] Generated {OUTPUT_FILE.name}")
    print(f"  Artists: {len(favorites)} | With images: {with_img}")
    print(f"  Open: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
