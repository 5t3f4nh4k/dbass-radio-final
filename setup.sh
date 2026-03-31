#!/usr/bin/env bash
# ============================================================
#  D-BASS RADIO — Setup Script v3
#  Drujba Bass Radio — Sofia
#  Ubuntu 22.04 LTS | Run as root: sudo bash setup.sh
# ============================================================
set -e

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; BLU='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${GRN}[D-BASS]${NC} $1"; }
die()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

[[ $EUID -ne 0 ]] && die "Run as root: sudo bash setup.sh"

echo -e "${BLU}"
cat << 'LOGO'
  ____        ____    _    ____ ____
 |  _ \      | __ )  / \  / ___/ ___|
 | | | |_____|  _ \ / _ \ \___ \___ \
 | |_| |_____| |_) / ___ \ ___) |__) |
 |____/      |____/_/   \_\____/____/
  D-BASS RADIO — Drujba Bass — Setup v3
LOGO
echo -e "${NC}"

echo ""
log "Setting up passwords..."
echo ""
read -p "  Icecast source password  (Liquidsoap->Icecast): " SOURCE_PASS
read -p "  Icecast admin password   (Icecast web panel):   " ICE_ADMIN_PASS
read -p "  DJ stream password       (DJs connect with):    " DJ_PASS
read -p "  Admin panel password     (your web admin):      " ADMIN_PANEL_PASS
echo ""

log "Installing system packages..."
apt-get update -qq
apt-get install -y icecast2 liquidsoap python3-pip nginx ffmpeg curl wget apache2-utils netcat-openbsd 2>&1 | tail -5

log "Installing yt-dlp..."
curl -skL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp
chmod +x /usr/local/bin/yt-dlp
yt-dlp --version

log "Installing Python deps..."
pip3 install flask flask-cors 2>&1 | tail -3

log "Creating directories..."
mkdir -p /var/lib/dbass/music /var/lib/dbass/jingles /etc/dbass
mkdir -p /var/log/dbass /var/www/dbass/drujbaman /etc/liquidsoap /opt/dbass
chmod 777 /var/lib/dbass/music /var/log/dbass

log "Writing Icecast2 config..."
cat > /etc/icecast2/icecast.xml << EOF
<icecast>
  <location>Sofia, Bulgaria</location>
  <admin>admin@dbass.radio</admin>
  <limits>
    <clients>100</clients>
    <sources>3</sources>
    <queue-size>524288</queue-size>
    <client-timeout>30</client-timeout>
    <header-timeout>15</header-timeout>
    <source-timeout>10</source-timeout>
    <burst-on-connect>1</burst-on-connect>
    <burst-size>1048576</burst-size>
  </limits>
  <authentication>
    <source-password>${SOURCE_PASS}</source-password>
    <relay-password>${SOURCE_PASS}</relay-password>
    <admin-user>admin</admin-user>
    <admin-password>${ICE_ADMIN_PASS}</admin-password>
  </authentication>
  <hostname>localhost</hostname>
  <listen-socket>
    <port>8000</port>
    <bind-address>127.0.0.1</bind-address>
  </listen-socket>
  <http-headers>
    <header name="Access-Control-Allow-Origin" value="*"/>
    <header name="Access-Control-Allow-Headers" value="Origin, Accept, X-Requested-With, Content-Type"/>
    <header name="Access-Control-Allow-Methods" value="GET, OPTIONS, HEAD"/>
  </http-headers>
  <mount type="normal">
    <mount-name>/stream</mount-name>
    <max-listeners>100</max-listeners>
    <max-listener-duration>0</max-listener-duration>
    <fallback-when-empty>1</fallback-when-empty>
  </mount>
  <fileserve>1</fileserve>
  <paths>
    <basedir>/usr/share/icecast2</basedir>
    <logdir>/var/log/icecast2</logdir>
    <webroot>/usr/share/icecast2/web</webroot>
    <adminroot>/usr/share/icecast2/admin</adminroot>
    <alias source="/" destination="/status.xsl"/>
  </paths>
  <logging>
    <accesslog>access.log</accesslog>
    <errorlog>error.log</errorlog>
    <loglevel>3</loglevel>
  </logging>
  <security>
    <chroot>0</chroot>
  </security>
</icecast>
EOF
sed -i 's/ENABLE=false/ENABLE=true/' /etc/default/icecast2 2>/dev/null || true

log "Writing Liquidsoap config..."
cat > /etc/liquidsoap/dbass.liq << EOF
#!/usr/bin/liquidsoap
set("init.allow_root",true)
settings.frame.duration.set(0.04)

settings.server.telnet.set(true)
settings.server.telnet.port.set(1234)
settings.server.telnet.bind_addr.set("127.0.0.1")

settings.log.file.set(true)
settings.log.file.path.set("/var/log/dbass/liquidsoap.log")
settings.log.level.set(3)
settings.decoder.decoders.set(["ffmpeg","mad","gstreamer"])

playlist_source = playlist(reload=60, mode="randomize", "/var/lib/dbass/music")
silence = blank(duration=0.)
live = input.harbor("live", port=8001, password="${DJ_PASS}")

# Jingles — place MP3s in /var/lib/dbass/jingles/
# To disable: change [live, music_with_jingles, silence] to [live, playlist_source, silence]
jingle1 = single("/var/lib/dbass/jingles/dabassradio.mp3")
jingle2 = single("/var/lib/dbass/jingles/radiobass-drujbabass.mp3")
jingles = rotate(weights=[1,1], [jingle1, jingle2])
music_with_jingles = rotate(weights=[3,1], [playlist_source, jingles])

radio = fallback(track_sensitive=false, [live, music_with_jingles, silence])

output.icecast(
  %mp3(bitrate=128, stereo=true, samplerate=44100),
  host="127.0.0.1", port=8000, password="${SOURCE_PASS}",
  mount="/stream", name="D-BASS Radio",
  description="Drujba Bass Radio",
  genre="DnB Dubstep Reggae Hardcore Hip-Hop Bass",
  url="http://localhost", public=false, radio
)
EOF

log "Writing admin bridge..."
cp "$(dirname "$0")/app.py" /opt/dbass/app.py

log "Writing Nginx config..."
htpasswd -cb /etc/nginx/.dbass_admin admin "${ADMIN_PANEL_PASS}"

cat > /etc/nginx/sites-available/dbass << 'NGINXEOF'
server {
    listen 80;
    server_name localhost _;

    location / {
        auth_basic off;
        root /var/www/dbass;
        index index.html;
        try_files $uri $uri/ =404;
        add_header Cache-Control "no-cache";
    }

    location /drujbaman {
        root /var/www/dbass;
        auth_basic "D-BASS Admin";
        auth_basic_user_file /etc/nginx/.dbass_admin;
        try_files $uri /drujbaman/index.html;
    }

    location /api/status {
        auth_basic off;
        proxy_pass http://127.0.0.1:5000/api/status;
        proxy_set_header Host $host;
    }

    location /api/ {
        auth_basic off;
        proxy_pass http://127.0.0.1:5000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300;
    }

    location /stream {
        proxy_pass http://127.0.0.1:8000/stream;
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_cache off;
        proxy_ignore_headers Cache-Control;
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
        proxy_http_version 1.1;
        proxy_set_header Connection '';
        tcp_nodelay on;
        add_header Access-Control-Allow-Origin "*";
        add_header Cache-Control "no-cache, no-store";
    }

    location /icecast-status {
        auth_basic "D-BASS Admin";
        auth_basic_user_file /etc/nginx/.dbass_admin;
        proxy_pass http://127.0.0.1:8000/status-json.xsl;
    }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/dbass /etc/nginx/sites-enabled/dbass
rm -f /etc/nginx/sites-enabled/default

log "Writing systemd services..."
cat > /etc/systemd/system/dbass-liquidsoap.service << 'EOF'
[Unit]
Description=D-BASS Radio — Liquidsoap
After=network.target icecast2.service
Requires=icecast2.service

[Service]
Type=simple
ExecStart=/usr/bin/liquidsoap /etc/liquidsoap/dbass.liq
Restart=always
RestartSec=5
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/dbass-bridge.service << EOF
[Unit]
Description=D-BASS Radio — Admin Bridge API
After=network.target dbass-liquidsoap.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/dbass/app.py
Restart=always
RestartSec=3
Environment=ICE_ADMIN_USER=admin
Environment=ICE_ADMIN_PASS=${ICE_ADMIN_PASS}
WorkingDirectory=/opt/dbass
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

log "Copying web files..."
cp "$(dirname "$0")/www/index.html"            /var/www/dbass/index.html
cp "$(dirname "$0")/www/drujbaman/index.html"  /var/www/dbass/drujbaman/index.html
chown -R www-data:www-data /var/www/dbass

log "Starting services..."
systemctl daemon-reload
systemctl enable icecast2 dbass-liquidsoap dbass-bridge nginx
systemctl restart icecast2
sleep 2
systemctl restart dbass-liquidsoap
sleep 2
systemctl restart dbass-bridge
systemctl restart nginx
nginx -t && systemctl reload nginx

echo ""
echo -e "${GRN}========================================================${NC}"
echo -e "${GRN}   D-BASS RADIO — SETUP COMPLETE${NC}"
echo -e "${GRN}========================================================${NC}"
echo ""
echo -e "  Listener page:  ${BLU}http://localhost${NC}"
echo -e "  Admin panel:    ${BLU}http://localhost/drujbaman${NC}  (user: admin)"
echo -e "  Stream URL:     ${BLU}http://localhost/stream${NC}"
echo ""
echo -e "  DJ settings: Protocol=Icecast2, Port=8001, Mount=/live"
echo -e "  DJ password: ${DJ_PASS}"
echo ""
echo -e "${YLW}  Next: Start Cloudflare tunnel on Windows — see INSTALL.txt${NC}"
echo ""
