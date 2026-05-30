# Superset configuration for Lake of Tears
# Mounted at /app/pythonpath/superset_config.py inside the container.

# Serve Superset under /superset/ path (proxied by nginx).
APPLICATION_PREFIX_PATH = "/superset"

# Allow Superset to be embedded in an iframe from the Lake UI shell.
# Default HTTP_HEADERS includes X-Frame-Options: SAMEORIGIN which blocks iframes.
HTTP_HEADERS = {}
# Flask-Talisman also sets X-Frame-Options; override it here.
TALISMAN_CONFIG = {
    "frame_options": "ALLOWALL",
}
# Grant anonymous users Gamma-level read access so the iframe loads without login.
PUBLIC_ROLE_LIKE = "Gamma"
