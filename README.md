# D-BASS RADIO
### Drujba Bass Radio — Self-Hosted Internet Radio

A fully synchronized internet radio station that runs on your own PC or any Linux server. Everyone who tunes in hears the exact same audio at the exact same moment — like FM radio, over the internet.

---

## Features

- **Synchronized streaming** — all listeners hear the same track at the same time
- **Playlist from YouTube / SoundCloud / Bandcamp** — paste a URL, yt-dlp downloads automatically
- **Delete-after-play** — finished songs are deleted from disk immediately — repeats are physically impossible
- **DJ Live Takeover** — DJs connect with Mixxx, BUTT, or VirtualDJ and override the playlist live
- **Jingles** — auto-plays radio ident MP3s between every 3 tracks
- **Admin panel** — web UI at `/drujbaman` to manage playlist, skip tracks, check status
- **Free to run** — use Cloudflare Tunnel for public access, no domain needed
- **Auto filename sanitization** — handles special characters in track names

---

## Stack

| Component | Role |
|-----------|------|
| **Icecast2** | Radio server — streams audio to all listeners simultaneously |
| **Liquidsoap** | Stream mixer — manages playlist, jingles, DJ takeover |
| **yt-dlp** | Downloads audio from YouTube, SoundCloud, Bandcamp and 1000+ sites |
| **Flask (Python)** | Admin API bridge |
| **Nginx** | Web server — serves frontend, proxies stream and API |
| **Cloudflare Tunnel** | Exposes radio to internet for free, no static IP needed |

---

## Quick Install

**Requirements:** Ubuntu 22.04 LTS (local PC with WSL2, or any cloud VM)

```bash
git clone https://github.com/5t3f4nh4k/dbass-radio-final.git
cd dbass-radio-final
sudo bash setup.sh
```

Full guide: see **INSTALL.txt**

---

## URLs

| Page | Path |
|------|------|
| Listener radio | `http://YOUR_IP/` |
| Admin panel | `http://YOUR_IP/drujbaman` |
| Stream direct | `http://YOUR_IP/stream` |

---

## DJ Live Takeover

| Setting | Value |
|---------|-------|
| Protocol | Icecast 2 |
| Port | 8001 |
| Mount | /live |
| Username | source |
| Password | set during setup |

---

## Scaling

| Listeners | Setup |
|-----------|-------|
| 0–50 | Home PC + Cloudflare Tunnel (free) |
| 50–100 | Good upload connection or Hetzner CX22 (€4.51/mo) |
| 100–500 | Hetzner CX32 + increase `<clients>` in icecast.xml |

---

## License

MIT — free to use, modify, distribute.

*Built in Sofia, Bulgaria. The bass will set you free.*
