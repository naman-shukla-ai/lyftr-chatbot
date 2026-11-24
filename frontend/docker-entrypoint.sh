#!/bin/sh
# Generate the runtime config from environment variables (no rebuild needed to change the API URL).
set -eu
API_URL="${API_URL:-http://localhost:8000}"
printf 'window.__CONFIG__ = { API_URL: "%s" };\n' "$(printf '%s' "$API_URL" | sed 's/"/\\"/g')" \
  > /usr/share/nginx/html/config.js
