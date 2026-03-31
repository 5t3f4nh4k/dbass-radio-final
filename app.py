from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess, socket, os, json, threading, time, urllib.request, base64, re
from pathlib import Path

app = Flask(__name__)
CORS(app, origins=["*"])

MUSIC_DIR       = "/var/lib/dbass/music"
CONFIG_FILE     = "/etc/dbass/config.json"
LQ_HOST         = "127.0.0.1"
LQ_PORT         = 1234
ICE_ADMIN_USER  = os.environ.get("ICE_ADMIN_USER", "admin")
ICE_ADMIN_PASS  = os.environ.get("ICE_ADMIN_PASS", "changeme")
BATCH_SIZE      = 30
PRE_DOWNLOAD_AT = 20  # download next batch when this many songs have been deleted

# ── HELPERS ───────────────────────────────────────────────────────────────────

def lq_cmd(cmd):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((LQ_HOST, LQ_PORT))
        s.sendall(f"{cmd}\n".encode())
        buf = b""
        while True:
            chunk = s.recv(4096)
            if not chunk: break
            buf += chunk
            if b"END" in buf: break
        s.close()
        return buf.decode(errors="ignore").replace("END", "").strip()
    except Exception as e:
        return f"lq_error: {e}"

def get_now_playing():
    try:
        rid = lq_cmd("request.on_air").strip()
        if rid and rid.isdigit():
            meta = lq_cmd(f"request.metadata {rid}")
            for line in meta.split("\n"):
                if "title=" in line:
                    return line.split("title=", 1)[1].strip().strip('"')
                if "uri=" in line or "filename=" in line:
                    return Path(line.split("=", 1)[1].strip().strip('"')).stem
        return "—"
    except:
        return "—"

def icecast_stats():
    try:
        url  = "http://127.0.0.1:8000/status-json.xsl"
        cred = base64.b64encode(f"{ICE_ADMIN_USER}:{ICE_ADMIN_PASS}".encode()).decode()
        req  = urllib.request.Request(url, headers={"Authorization": f"Basic {cred}"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read())
    except:
        return None

def sanitize_name(name):
    clean = re.sub(r'[^\x00-\x7F]', '', name)
    clean = re.sub(r'[?"\|<>*]', '', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean

def make_prefix(pl_num, batch_num):
    return f"P{pl_num}B{batch_num:02d}"

# ── BATCH MANAGER ─────────────────────────────────────────────────────────────

class BatchManager:

    def __init__(self):
        self.lock             = threading.Lock()
        self._load_state()
        self.next_downloading = False
        self.next_ready       = False
        self.last_on_air      = ""
        self.status_msg       = "Ready"
        self.download_log     = []
        self.reset_pending    = False
        threading.Thread(target=self._loop, daemon=True).start()

    # ── State persistence ──────────────────────────────────────────────────────

    def _load_state(self):
        try:
            with open(CONFIG_FILE) as f:
                s = json.load(f)
        except:
            s = {}
        self.pl1_url        = s.get("pl1_url", "")
        self.pl2_url        = s.get("pl2_url", "")
        self.active_pl      = s.get("active_pl", 1)
        self.active_batch   = s.get("active_batch", 1)
        self.batch_start    = s.get("batch_start", 1)
        self.current_prefix = s.get("current_prefix", "")
        self.next_prefix    = s.get("next_prefix", "")
        self.cleanup_prefix = s.get("cleanup_prefix", "")
        self.reset_pending  = s.get("reset_pending", False)

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump({
                    "pl1_url":        self.pl1_url,
                    "pl2_url":        self.pl2_url,
                    "active_pl":      self.active_pl,
                    "active_batch":   self.active_batch,
                    "batch_start":    self.batch_start,
                    "current_prefix": self.current_prefix,
                    "next_prefix":    self.next_prefix,
                    "cleanup_prefix": self.cleanup_prefix,
                    "reset_pending":  self.reset_pending,
                }, f, indent=2)
        except:
            pass

    # ── Download ───────────────────────────────────────────────────────────────

    def _download_batch(self, url, prefix, start_idx):
        end_idx = start_idx + BATCH_SIZE - 1
        out_tpl = (
            f"{MUSIC_DIR}/{prefix}_"
            "%(autonumber)04d - %(title)s.%(ext)s"
        )
        cmd = [
            "yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "128K",
            "-o", out_tpl,
            "--playlist-items", f"{start_idx}-{end_idx}",
            "--no-warnings", "--newline",
            "--no-overwrites",
            "--autonumber-start", str(start_idx),
            url
        ]

        def log(line):
            self.download_log.append(line)
            self.download_log = self.download_log[-100:]

        log(f"[batch] Downloading {prefix} items {start_idx}-{end_idx}")
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            for line in proc.stdout:
                line = line.strip()
                if line:
                    log(line)
            proc.wait()
        except Exception as e:
            log(f"ERROR: {e}")
            return 0

        # Remove all non-mp3 files with this prefix
        for f in list(Path(MUSIC_DIR).iterdir()):
            if f.name.startswith(prefix) and f.suffix != '.mp3':
                try: f.unlink()
                except: pass

        # Sanitize filenames — skip if destination already exists
        for f in list(Path(MUSIC_DIR).iterdir()):
            if not f.name.startswith(prefix) or f.suffix != '.mp3':
                continue
            clean = sanitize_name(f.name)
            if clean != f.name:
                dest = Path(MUSIC_DIR) / clean
                if not dest.exists():
                    try: f.rename(dest)
                    except: pass
                else:
                    try: f.unlink()
                    except: pass

        # Re-encode to fix broken timestamps
        for f in list(Path(MUSIC_DIR).glob(f"{prefix}*.mp3")):
            tmp = Path(MUSIC_DIR) / f"_tmp_{f.name}"
            try:
                subprocess.run(
                    ["ffmpeg", "-i", str(f), "-vn", "-acodec", "libmp3lame",
                     "-ac", "2", "-ar", "44100", "-ab", "128k", "-write_xing", "0",
                     str(tmp), "-y", "-loglevel", "quiet"],
                    check=True, timeout=300
                )
                tmp.replace(f)
            except:
                if tmp.exists():
                    tmp.unlink()

        count = len(list(Path(MUSIC_DIR).glob(f"{prefix}*.mp3")))
        log(f"[batch] {prefix} ready — {count} tracks")
        return count

    def _delete_prefix(self, prefix):
        if not prefix:
            return
        for f in list(Path(MUSIC_DIR).glob(f"{prefix}*")):
            try: f.unlink()
            except: pass

    def _delete_file(self, filename):
        """Delete a single finished track from disk."""
        if not filename:
            return
        f = Path(MUSIC_DIR) / filename
        try:
            if f.exists():
                f.unlink()
        except:
            pass

    # ── Main loop ──────────────────────────────────────────────────────────────

    def _loop(self):
        while True:
            try:
                self._tick()
            except Exception as e:
                self.status_msg = f"Error: {e}"
            time.sleep(5)

    def _tick(self):
        with self.lock:
            if not self.current_prefix:
                return

            on_air = lq_cmd("request.on_air").strip()
            if not on_air or not on_air.isdigit():
                return

            meta = lq_cmd(f"request.metadata {on_air}")
            filename = ""
            for line in meta.split("\n"):
                if "filename=" in line or "uri=" in line:
                    raw = line.split("=", 1)[1].strip().strip('"')
                    filename = Path(raw).name
                    break

            if not filename or filename == self.last_on_air:
                return

            # ── New song started — delete the one that just finished ───────────
            if self.last_on_air:
                self._delete_file(self.last_on_air)

            self.last_on_air = filename

            # ── Detect switch to next batch ────────────────────────────────────
            if self.next_prefix and filename.startswith(self.next_prefix):
                # Wipe any remaining files from the old batch
                if self.cleanup_prefix:
                    self._delete_prefix(self.cleanup_prefix)
                    self.cleanup_prefix = ""
                self.next_ready       = False
                self.next_downloading = False
                self.current_prefix   = self.next_prefix
                self.next_prefix      = ""
                self._save_state()
                self.status_msg = f"PL{self.active_pl} Batch {self.active_batch} playing"
                return

            # ── Count remaining songs — deleted ones can never replay ──────────
            remaining = len(list(Path(MUSIC_DIR).glob(f"{self.current_prefix}*.mp3")))
            played    = BATCH_SIZE - remaining

            self.status_msg = (
                f"PL{self.active_pl} Batch {self.active_batch} — "
                f"{played}/{BATCH_SIZE} played, {remaining} remaining"
            )

            # ── Trigger pre-download when PRE_DOWNLOAD_AT songs deleted ────────
            if played >= PRE_DOWNLOAD_AT and not self.next_downloading and not self.next_ready:
                if self.reset_pending:
                    next_start  = 1
                    next_batch  = 1
                    next_pl_num = self.active_pl
                    next_url    = self.pl1_url if self.active_pl == 1 else self.pl2_url
                    self.reset_pending = False
                else:
                    next_start  = self.batch_start + BATCH_SIZE
                    next_batch  = self.active_batch + 1
                    next_pl_num = self.active_pl
                    next_url    = self.pl1_url if self.active_pl == 1 else self.pl2_url

                next_pfx = make_prefix(next_pl_num, next_batch)
                self.next_prefix      = next_pfx
                self.cleanup_prefix   = self.current_prefix
                self.next_downloading = True
                self.status_msg       = f"Pre-downloading batch {next_batch}..."
                self._save_state()

                _next_pl    = next_pl_num
                _next_batch = next_batch
                _next_start = next_start
                _next_url   = next_url
                _next_pfx   = next_pfx

                def do_download():
                    count = self._download_batch(_next_url, _next_pfx, _next_start)
                    with self.lock:
                        if count == 0:
                            other_pl  = 2 if _next_pl == 1 else 1
                            other_url = self.pl2_url if other_pl == 2 else self.pl1_url
                            if not other_url:
                                other_pl    = _next_pl
                                other_url   = self.pl1_url
                                other_batch = 1
                                other_start = 1
                            else:
                                other_batch = 1
                                other_start = 1

                            new_pfx = make_prefix(other_pl, other_batch)
                            self.next_prefix  = new_pfx
                            self.active_pl    = other_pl
                            self.active_batch = other_batch
                            self.batch_start  = other_start
                            count2 = self._download_batch(other_url, new_pfx, other_start)
                            self.next_ready = count2 > 0
                        else:
                            self.active_pl    = _next_pl
                            self.active_batch = _next_batch
                            self.batch_start  = _next_start
                            self.next_ready   = True

                        self.next_downloading = False
                        self.status_msg = f"Batch {self.active_batch} preloaded"
                        lq_cmd("music.reload")
                        self._save_state()

                threading.Thread(target=do_download, daemon=True).start()

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self, pl1_url, pl2_url=""):
        with self.lock:
            for f in list(Path(MUSIC_DIR).iterdir()):
                try: f.unlink()
                except: pass
            self.pl1_url          = pl1_url
            self.pl2_url          = pl2_url
            self.active_pl        = 1
            self.active_batch     = 1
            self.batch_start      = 1
            self.current_prefix   = make_prefix(1, 1)
            self.next_prefix      = ""
            self.cleanup_prefix   = ""
            self.next_ready       = False
            self.next_downloading = False
            self.reset_pending    = False
            self.last_on_air      = ""
            self.status_msg       = "Downloading first batch..."
            self.download_log     = []
            self._save_state()

        def do_first():
            pfx   = make_prefix(1, 1)
            count = self._download_batch(pl1_url, pfx, 1)
            with self.lock:
                self.status_msg = f"Batch 1 ready — {count} tracks"
            lq_cmd("music.reload")

        threading.Thread(target=do_first, daemon=True).start()

    def set_pl1(self, url):
        with self.lock:
            self.pl1_url = url
            if self.active_pl == 1:
                self.reset_pending = True
                self.status_msg = "PL1 updated — resets on next batch"
            self._save_state()

    def set_pl2(self, url):
        with self.lock:
            self.pl2_url = url
            if self.active_pl == 2:
                self.reset_pending = True
                self.status_msg = "PL2 updated — resets on next batch"
            self._save_state()

    def get_info(self):
        with self.lock:
            remaining = len(list(Path(MUSIC_DIR).glob(f"{self.current_prefix}*.mp3"))) if self.current_prefix else 0
            return {
                "pl1_url":        self.pl1_url,
                "pl2_url":        self.pl2_url,
                "active_pl":      self.active_pl,
                "active_batch":   self.active_batch,
                "batch_start":    self.batch_start,
                "current_prefix": self.current_prefix,
                "next_prefix":    self.next_prefix,
                "played":         max(0, BATCH_SIZE - remaining),
                "batch_size":     BATCH_SIZE,
                "remaining":      remaining,
                "downloading":    self.next_downloading,
                "next_ready":     self.next_ready,
                "status_msg":     self.status_msg,
                "reset_pending":  self.reset_pending,
                "log":            self.download_log[-20:],
            }


bm = BatchManager()

# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/api/status")
def status():
    stats = icecast_stats()
    listeners = 0
    if stats:
        try:
            src = stats.get("icestats", {}).get("source")
            if isinstance(src, list): src = src[0] if src else {}
            listeners = int((src or {}).get("listeners", 0))
        except:
            pass
    lq_online     = "lq_error" not in lq_cmd("version")
    harbor_status = lq_cmd("input.harbor_0.status").lower() if lq_online else ""
    is_live       = "connected" in harbor_status and "no source" not in harbor_status
    music_files   = len(list(Path(MUSIC_DIR).glob("*.mp3")))
    info          = bm.get_info()
    return jsonify({
        "listeners":     listeners,
        "is_live":       is_live,
        "current_track": get_now_playing(),
        "music_files":   music_files,
        "icecast":       "online" if stats else "offline",
        "liquidsoap":    "online" if lq_online else "offline",
        "mode":          "dj" if is_live else ("playing" if music_files > 0 else "idle"),
        "batch_info":    info,
    })

@app.route("/api/playlist", methods=["POST"])
def set_playlist():
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()
    pl2  = (data.get("url2") or "").strip()
    if not url:
        return jsonify({"error": "No URL"}), 400
    bm.start(url, pl2)
    return jsonify({"status": "started", "message": "Downloading first batch..."})

@app.route("/api/playlist1", methods=["POST"])
def set_playlist1():
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "No URL"}), 400
    bm.set_pl1(url)
    return jsonify({"status": "ok", "reset_pending": bm.reset_pending})

@app.route("/api/playlist2", methods=["POST"])
def set_playlist2():
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "No URL"}), 400
    bm.set_pl2(url)
    return jsonify({"status": "ok"})

@app.route("/api/skip", methods=["POST"])
def skip():
    return jsonify({"result": lq_cmd("music.skip")})

@app.route("/api/reload", methods=["POST"])
def reload():
    return jsonify({"result": lq_cmd("music.reload")})

@app.route("/api/music")
def music_list():
    files = sorted(Path(MUSIC_DIR).glob("*.mp3"), key=lambda f: f.name)
    def display(f):
        return f.stem
    result = [{"file": f.stem, "display": display(f)} for f in files]
    return jsonify({"files": result, "count": len(result)})

if __name__ == "__main__":
    os.makedirs(MUSIC_DIR, exist_ok=True)
    app.run(host="127.0.0.1", port=5000, debug=False)
