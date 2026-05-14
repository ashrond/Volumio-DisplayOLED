#!/bin/bash
# build-plugin-zip.sh — assemble a deployable Volumio plugin zip.
#
# Output: ./dist/synthwave-display-<version>.zip with this structure:
#
#   <zip-root>/
#   ├── index.js              (from plugin/)
#   ├── package.json
#   ├── config.json
#   ├── UIConfig.json
#   ├── install.sh
#   ├── uninstall.sh
#   ├── i18n/
#   ├── README.md
#   └── display/              (the Python display program)
#       ├── main.py
#       ├── config/
#       │   ├── config.py
#       │   ├── settings.toml   (service_name rewritten to synthwave-display)
#       │   └── runtime.toml
#       ├── themes/default/
#       ├── screens/
#       ├── tools/
#       └── install/
#           └── requirements.txt
#
# Things deliberately NOT in the zip: dev/, masterplan.md, plugin/ itself
# (we're building FROM it, not bundling it inside), tests, IDE crud.
#
# Run from project root. Requires zip(1) or python3 -m zipfile fallback.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLUGIN_DIR="$PROJECT_ROOT/plugin"
DIST_DIR="$PROJECT_ROOT/dist"
STAGE_DIR="$DIST_DIR/stage"

# Extract version from plugin/package.json
VERSION=$(python3 -c "import json; print(json.load(open('$PLUGIN_DIR/package.json'))['version'])")
ZIP_NAME="synthwave-display-${VERSION}.zip"

echo ":: building $ZIP_NAME"
rm -rf "$STAGE_DIR" "$DIST_DIR/$ZIP_NAME"
mkdir -p "$STAGE_DIR/display"

# --- Plugin files at zip root ---
echo ":: staging plugin/ → zip root"
cp -r "$PLUGIN_DIR"/. "$STAGE_DIR/"

# --- Python display under display/ ---
echo ":: staging display/ tree"
# Copy each subtree we ship, individually, to be deliberate about what
# ends up in the zip. New top-level items don't auto-leak.
for item in main.py config screens themes tools install AUTOSTART.md; do
    if [[ -e "$PROJECT_ROOT/$item" ]]; then
        cp -r "$PROJECT_ROOT/$item" "$STAGE_DIR/display/"
    fi
done

# Strip dev artifacts that might have crept into subtrees.
find "$STAGE_DIR" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$STAGE_DIR" -name "*.pyc" -delete 2>/dev/null || true
find "$STAGE_DIR" -name ".DS_Store" -delete 2>/dev/null || true

# --- Rewrite service_name in display's settings.toml so the plugin's
#     admin.py drives the right systemd unit. ---
SETTINGS="$STAGE_DIR/display/config/settings.toml"
if [[ -f "$SETTINGS" ]]; then
    echo ":: rewriting service_name → synthwave-display.service"
    # Single-line substitution under the [systemd] table. Tried tomlkit/toml
    # first for proper TOML round-trip; falls back to sed because the dev
    # box may not have either lib installed and we don't need full parsing
    # for this one fixed key.
    if python3 -c "import tomlkit" 2>/dev/null || python3 -c "import toml" 2>/dev/null; then
        python3 - <<PYEOF
try: import tomlkit as t
except ImportError: import toml as t
path = "$SETTINGS"
doc = t.loads(open(path).read())
if 'systemd' not in doc:
    doc['systemd'] = {}
doc['systemd']['service_name'] = 'synthwave-display.service'
with open(path, 'w') as f:
    f.write(t.dumps(doc))
PYEOF
    else
        sed -i -E 's/^service_name *= *"[^"]*"/service_name = "synthwave-display.service"/' "$SETTINGS"
    fi
fi

# --- Zip it ---
echo ":: producing $DIST_DIR/$ZIP_NAME"
if command -v zip >/dev/null 2>&1; then
    (cd "$STAGE_DIR" && zip -r "$DIST_DIR/$ZIP_NAME" . -x "*.DS_Store") >/dev/null
else
    # Fallback to Python zipfile if `zip` is absent (Volumio default
    # install has it; Pi 0w2 dev image may not).
    python3 - <<PYEOF
import os, zipfile
stage = "$STAGE_DIR"
out = "$DIST_DIR/$ZIP_NAME"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(stage):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, stage)
            zf.write(full, rel)
PYEOF
fi

# --- Cleanup stage ---
rm -rf "$STAGE_DIR"

SIZE=$(du -h "$DIST_DIR/$ZIP_NAME" | cut -f1)
echo ":: done — $DIST_DIR/$ZIP_NAME ($SIZE)"
echo ":: install on Pi via:"
echo "     volumio plugin install $DIST_DIR/$ZIP_NAME"
echo "   or upload via Volumio WebUI → Plugins → Search → 'Upload Plugin'"
