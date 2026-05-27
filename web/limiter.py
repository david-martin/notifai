from slowapi import Limiter
from slowapi.util import get_remote_address

# get_remote_address reads X-Forwarded-For, which is set by the reverse proxy.
# This only works correctly when uvicorn is started with:
#   --proxy-headers --forwarded-allow-ips=127.0.0.1
# (set in deploy/notifai-web.service). Without these flags, clients could spoof
# their IP and bypass per-IP rate limits.
limiter = Limiter(key_func=get_remote_address)
