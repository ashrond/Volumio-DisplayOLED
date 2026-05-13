/*
 * Synthwave Display — Volumio plugin (Node entry point).
 *
 * This is the thin Node shell. The actual display program is the Python
 * code under display/. We talk to it via:
 *   - filesystem (display/config/runtime.toml — written via admin.py)
 *   - systemctl (start/stop/restart the systemd service)
 *   - display/tools/admin.py (JSON-stdout CLI for theme/runtime ops)
 *
 * Plugin lifecycle:
 *   onVolumioStart   — load plugin config, return resolved promise
 *   onStart          — service start (called when user enables the plugin)
 *   onStop           — service stop
 *   onUninstall      — cleanup before plugin removal
 *   getUIConfig      — populate the settings UI dynamically from runtime.toml
 *                       + admin.py list-themes (dropdown options)
 *   saveTheme        — UIConfig "Save" handler for the theme section
 *   saveBurnin       — UIConfig "Save" handler for the burn-in section
 *   restartService   — Actions section button handler
 *
 * Phase 6 scaffold scope: lifecycle + theme dropdown + a small subset of
 * burn-in fields + restart button. Phase 7/8 will add upload/delete and
 * fully populate the runtime settings form.
 */

'use strict';

const fs = require('fs-extra');
const path = require('path');
const { spawn } = require('child_process');
const libQ = require('kew');
const vconf = require('v-conf');

const DISPLAY_SUBDIR = 'display';                // relative to this plugin's directory
const ADMIN_REL_PATH = 'tools/admin.py';         // inside DISPLAY_SUBDIR
const SYSTEMD_UNIT   = 'volumio-display.service';

module.exports = SynthwaveDisplay;

function SynthwaveDisplay(context) {
  this.context        = context;
  this.commandRouter  = context.coreCommandRouter;
  this.logger         = context.logger;
  this.configManager  = context.configManager;
}


// ── Lifecycle ───────────────────────────────────────────────────────────────

SynthwaveDisplay.prototype.onVolumioStart = function () {
  const cfgFile = this.commandRouter.pluginManager.getConfigurationFile(this.context, 'config.json');
  this.config = new vconf();
  this.config.loadFile(cfgFile);
  return libQ.resolve();
};

SynthwaveDisplay.prototype.onStart = function () {
  const self = this;
  const defer = libQ.defer();
  self._systemctl('start')
    .then(() => {
      self.config.set('service_status', 'running');
      self.logger.info('[synthwave_display] service started');
      defer.resolve();
    })
    .fail((err) => {
      self.logger.error('[synthwave_display] start failed: ' + err);
      defer.reject(err);
    });
  return defer.promise;
};

SynthwaveDisplay.prototype.onStop = function () {
  const self = this;
  const defer = libQ.defer();
  self._systemctl('stop')
    .then(() => {
      self.config.set('service_status', 'stopped');
      self.logger.info('[synthwave_display] service stopped');
      defer.resolve();
    })
    .fail((err) => {
      self.logger.error('[synthwave_display] stop failed: ' + err);
      defer.resolve();  // never block uninstall on a stop failure
    });
  return defer.promise;
};

SynthwaveDisplay.prototype.onRestart = function () { return libQ.resolve(); };

SynthwaveDisplay.prototype.getConfigurationFiles = function () {
  return ['config.json'];
};


// ── UI Config (dynamic — populated from runtime.toml + admin.py) ────────────

SynthwaveDisplay.prototype.getUIConfig = function () {
  const self = this;
  const defer = libQ.defer();

  const langCode = self.commandRouter.sharedVars.get('language_code');

  libQ.all([
    self.commandRouter.i18nJson(
      path.join(__dirname, 'i18n', 'strings_' + langCode + '.json'),
      path.join(__dirname, 'i18n', 'strings_en.json'),
      path.join(__dirname, 'UIConfig.json')
    ),
    self._admin(['list-themes']),
    self._admin(['get-runtime']),
  ])
    .spread((uiconf, themesResp, runtimeResp) => {
      try {
        // Populate active-theme dropdown with whatever themes/ contains.
        const themes = (themesResp.themes || []).map((t) => ({
          value: t.name,
          label: t.display_name || t.name,
        }));
        const active = themesResp.active || 'default';
        const themeField = self._findContent(uiconf, 'section_theme', 'active_theme');
        if (themeField) {
          themeField.options = themes.length ? themes : [{ value: 'default', label: 'Default' }];
          themeField.value = themes.find((t) => t.value === active)
            || { value: active, label: active };
        }

        // Inject one "Delete: <name>" button per non-default, non-active theme
        // into section_themes_manage. Static fields (upload_path, download)
        // are already in UIConfig.json; we just append the dynamic ones.
        const manageSection = uiconf.sections.find((s) => s.id === 'section_themes_manage');
        if (manageSection) {
          const deletable = (themesResp.themes || []).filter(
            (t) => !t.is_default && !t.is_active
          );
          deletable.forEach((t) => {
            manageSection.content.push({
              id: 'btn_delete_' + t.name,
              element: 'button',
              label: 'Delete: ' + (t.display_name || t.name),
              onClick: {
                type: 'emit',
                message: 'callMethod',
                data: {
                  endpoint: 'user_interface/synthwave_display',
                  method: 'deleteTheme',
                  data: { theme_name: t.name },
                },
              },
            });
          });
        }

        // Populate every section's field defaults from current runtime.toml.
        // _setField is null-safe: missing fields silently skipped (so adding
        // a UI field that doesn't have a runtime counterpart is harmless).
        const runtime = runtimeResp.runtime || {};
        const display = runtime.display || {};
        const burnin  = runtime.burnin  || {};
        const timing  = runtime.timing  || {};
        const volume  = runtime.volume  || {};
        const menu    = runtime.menu    || {};
        const ir      = runtime.ir      || {};
        const debounce = (ir.debounce) || {};
        const logging = runtime.logging || {};

        // Display Hardware
        self._setField(uiconf, 'section_display', 'display_type',
          { value: display.type, label: display.type });
        self._setField(uiconf, 'section_display', 'display_width',  display.width);
        self._setField(uiconf, 'section_display', 'display_height', display.height);
        self._setField(uiconf, 'section_display', 'spi_bus',       display.spi_bus);
        self._setField(uiconf, 'section_display', 'spi_cs',        display.spi_cs);
        self._setField(uiconf, 'section_display', 'spi_speed_hz',  display.spi_speed_hz);

        // Populate display-type dropdown from list-devices, marking
        // unimplemented drivers with a "(coming soon)" suffix.
        return self._admin(['list-devices']).then((devicesResp) => {
          const typeField = self._findContent(uiconf, 'section_display', 'display_type');
          if (typeField) {
            typeField.options = (devicesResp.devices || []).map((d) => ({
              value: d.type_name,
              label: d.type_name + ' (' + d.category + ')'
                + (d.is_implemented ? '' : ' — coming soon'),
            }));
            const match = typeField.options.find((o) => o.value === display.type);
            if (match) typeField.value = match;
          }

          // Burn-in
          self._setField(uiconf, 'section_burnin', 'quiet_hours_start',
            burnin.quiet_hours_start);
          self._setField(uiconf, 'section_burnin', 'quiet_hours_end',
            burnin.quiet_hours_end);
          self._setField(uiconf, 'section_burnin', 'pixel_shift_enabled',
            burnin.pixel_shift_enabled);
          self._setField(uiconf, 'section_burnin', 'pixel_shift_interval_s',
            burnin.pixel_shift_interval_s);
          self._setField(uiconf, 'section_burnin', 'scanline_alternation_enabled',
            burnin.scanline_alternation_enabled);
          self._setField(uiconf, 'section_burnin', 'scanline_dim_factor',
            burnin.scanline_dim_factor);
          self._setField(uiconf, 'section_burnin', 'scanline_alternation_interval_s',
            burnin.scanline_alternation_interval_s);
          self._setField(uiconf, 'section_burnin', 'startup_contrast',
            burnin.startup_contrast);
          self._setField(uiconf, 'section_burnin', 'track_fade_enabled',
            burnin.track_fade_enabled);
          self._setField(uiconf, 'section_burnin', 'track_fade_max',
            burnin.track_fade_max);
          self._setField(uiconf, 'section_burnin', 'track_fade_min',
            burnin.track_fade_min);
          self._setField(uiconf, 'section_burnin', 'track_fade_min_remaining_s',
            burnin.track_fade_min_remaining_s);

          // Timing
          self._setField(uiconf, 'section_timing', 'playback_refresh_seconds',
            timing.playback_refresh_seconds);
          self._setField(uiconf, 'section_timing', 'volume_hold_seconds',
            timing.volume_hold_seconds);
          self._setField(uiconf, 'section_timing', 'idle_after_stop_seconds',
            timing.idle_after_stop_seconds);
          self._setField(uiconf, 'section_timing', 'screen_off_after_idle_seconds',
            timing.screen_off_after_idle_seconds);
          self._setField(uiconf, 'section_timing', 'render_tick_seconds',
            timing.render_tick_seconds);
          self._setField(uiconf, 'section_timing', 'pause_to_play_debounce_seconds',
            timing.pause_to_play_debounce_seconds);

          // Volume
          self._setField(uiconf, 'section_volume', 'volume_max', volume.max);
          self._setField(uiconf, 'section_volume', 'volume_button_recent_window',
            volume.button_recent_window);

          // IR
          self._setField(uiconf, 'section_ir', 'ir_udp_port', menu.ir_udp_port);
          self._setField(uiconf, 'section_ir', 'menu_timeout_seconds',
            menu.timeout_seconds);
          self._setField(uiconf, 'section_ir', 'skip_anim_cooldown_s',
            ir.skip_anim_cooldown_s);
          self._setField(uiconf, 'section_ir', 'debounce_right',
            debounce.KEY_RIGHT);
          self._setField(uiconf, 'section_ir', 'debounce_left',
            debounce.KEY_LEFT);
          self._setField(uiconf, 'section_ir', 'debounce_play',
            debounce.KEY_PLAY);

          // Logging
          self._setField(uiconf, 'section_logging', 'log_enabled', logging.enabled);
          self._setField(uiconf, 'section_logging', 'log_level',
            { value: logging.level, label: logging.level });
          self._setField(uiconf, 'section_logging', 'log_file', logging.file);
          self._setField(uiconf, 'section_logging', 'log_max_bytes', logging.max_bytes);
          self._setField(uiconf, 'section_logging', 'log_backup_count', logging.backup_count);

          return uiconf;
        })
        .then((uiconfReady) => defer.resolve(uiconfReady))
        .fail((e) => {
          self.logger.error('[synthwave_display] getUIConfig populate (devices) failed: ' + e);
          defer.reject(e);
        });
      } catch (e) {
        self.logger.error('[synthwave_display] getUIConfig populate failed: ' + e);
        defer.reject(e);
      }
    })
    .fail((err) => {
      self.logger.error('[synthwave_display] getUIConfig load failed: ' + err);
      defer.reject(err);
    });

  return defer.promise;
};


// ── Section save handlers ───────────────────────────────────────────────────

SynthwaveDisplay.prototype.saveTheme = function (data) {
  const self = this;
  const themeName = (data.active_theme && data.active_theme.value) || data.active_theme;
  if (!themeName) {
    return self._toastErr('No theme selected');
  }
  return self._admin(['set-theme', themeName])
    .then(() => self._admin(['restart']))
    .then(() => self._toastOk('TOAST_THEME_APPLIED'))
    .fail((err) => self._toastErr('TOAST_ERROR', err));
};

// All section save handlers use _saveSectionFields with a field map.
// Each row: [ui_id, runtime.toml section, runtime.toml key, kind].
// kind controls how the raw UI value is stringified for admin.py set-runtime.

SynthwaveDisplay.prototype.saveBurnin = function (data) {
  return this._saveSectionFields(data, [
    ['quiet_hours_start',              'burnin', 'quiet_hours_start',              'number'],
    ['quiet_hours_end',                'burnin', 'quiet_hours_end',                'number'],
    ['pixel_shift_enabled',            'burnin', 'pixel_shift_enabled',            'bool'],
    ['pixel_shift_interval_s',         'burnin', 'pixel_shift_interval_s',         'number'],
    ['scanline_alternation_enabled',   'burnin', 'scanline_alternation_enabled',   'bool'],
    ['scanline_dim_factor',            'burnin', 'scanline_dim_factor',            'number'],
    ['scanline_alternation_interval_s','burnin', 'scanline_alternation_interval_s','number'],
    ['startup_contrast',               'burnin', 'startup_contrast',               'number'],
    ['track_fade_enabled',             'burnin', 'track_fade_enabled',             'bool'],
    ['track_fade_max',                 'burnin', 'track_fade_max',                 'number'],
    ['track_fade_min',                 'burnin', 'track_fade_min',                 'number'],
    ['track_fade_min_remaining_s',     'burnin', 'track_fade_min_remaining_s',     'number'],
  ]);
};

SynthwaveDisplay.prototype.saveDisplay = function (data) {
  return this._saveSectionFields(data, [
    ['display_type',   'display', 'type',         'string'],
    ['display_width',  'display', 'width',        'number'],
    ['display_height', 'display', 'height',       'number'],
    ['spi_bus',        'display', 'spi_bus',      'number'],
    ['spi_cs',         'display', 'spi_cs',       'number'],
    ['spi_speed_hz',   'display', 'spi_speed_hz', 'number'],
  ]);
};

SynthwaveDisplay.prototype.saveTiming = function (data) {
  return this._saveSectionFields(data, [
    ['playback_refresh_seconds',       'timing', 'playback_refresh_seconds',       'number'],
    ['volume_hold_seconds',            'timing', 'volume_hold_seconds',            'number'],
    ['idle_after_stop_seconds',        'timing', 'idle_after_stop_seconds',        'number'],
    ['screen_off_after_idle_seconds',  'timing', 'screen_off_after_idle_seconds',  'number'],
    ['render_tick_seconds',            'timing', 'render_tick_seconds',            'number'],
    ['pause_to_play_debounce_seconds', 'timing', 'pause_to_play_debounce_seconds', 'number'],
  ]);
};

SynthwaveDisplay.prototype.saveVolume = function (data) {
  return this._saveSectionFields(data, [
    ['volume_max',                  'volume', 'max',                  'number'],
    ['volume_button_recent_window', 'volume', 'button_recent_window', 'number'],
  ]);
};

SynthwaveDisplay.prototype.saveIR = function (data) {
  // ir_udp_port and menu_timeout_seconds live in [menu], not [ir] — the UI
  // groups them with IR for user convenience since they're all remote-related.
  return this._saveSectionFields(data, [
    ['ir_udp_port',          'menu',         'ir_udp_port',          'number'],
    ['menu_timeout_seconds', 'menu',         'timeout_seconds',      'number'],
    ['skip_anim_cooldown_s', 'ir',           'skip_anim_cooldown_s', 'number'],
    ['debounce_right',       'ir.debounce',  'KEY_RIGHT',            'number'],
    ['debounce_left',        'ir.debounce',  'KEY_LEFT',             'number'],
    ['debounce_play',        'ir.debounce',  'KEY_PLAY',             'number'],
  ]);
};

SynthwaveDisplay.prototype.saveLogging = function (data) {
  return this._saveSectionFields(data, [
    ['log_enabled',      'logging', 'enabled',      'bool'],
    ['log_level',        'logging', 'level',        'string'],
    ['log_file',         'logging', 'file',         'string'],
    ['log_max_bytes',    'logging', 'max_bytes',    'number'],
    ['log_backup_count', 'logging', 'backup_count', 'number'],
  ]);
};

SynthwaveDisplay.prototype.restartService = function () {
  const self = this;
  return self._admin(['restart'])
    .then(() => self._toastOk('TOAST_RESTARTED'))
    .fail((err) => self._toastErr('TOAST_ERROR', err));
};

/** Install a theme from a zip already on the Pi at the user-provided path. */
SynthwaveDisplay.prototype.uploadThemeFromPath = function (data) {
  const self = this;
  const zipPath = (data.upload_path || '').trim();
  if (!zipPath) {
    return self._toastErr('TOAST_ERROR', 'Provide a path to the theme zip.');
  }
  return self._admin(['upload-theme', zipPath])
    .then((resp) => {
      self.logger.info('[synthwave_display] installed theme ' + (resp.theme || '?'));
      return self._toastOk('TOAST_UPLOAD_OK');
    })
    .fail((err) => self._toastErr('TOAST_ERROR', err));
};

/** Remove a theme folder. The button calling this carries data.theme_name. */
SynthwaveDisplay.prototype.deleteTheme = function (data) {
  const self = this;
  const name = (data && data.theme_name) || '';
  if (!name) {
    return self._toastErr('TOAST_ERROR', 'Missing theme name.');
  }
  return self._admin(['delete-theme', name])
    .then(() => self._toastOk('TOAST_DELETE_OK'))
    .fail((err) => self._toastErr('TOAST_ERROR', err));
};

/** Create a zip of themes/default/ at /tmp and surface the path to the user.
 * Until we have a real browser file picker for upload, this is a manual
 * download — the user grabs the zip via SCP / file manager. */
SynthwaveDisplay.prototype.downloadDefaultTheme = function () {
  const self = this;
  return self._admin(['download-theme', 'default'])
    .then((resp) => {
      const where = resp.path || '/tmp/default-theme.zip';
      // Show the path in the toast so the user knows where to fetch it.
      self.commandRouter.pushToastMessage(
        'success', 'Synthwave Display',
        (self.commandRouter.getI18nString('SYNTHWAVE_DISPLAY.TOAST_DOWNLOAD_OK')
          || 'Default theme zipped to ') + where
      );
      return libQ.resolve();
    })
    .fail((err) => self._toastErr('TOAST_ERROR', err));
};


// ── Internals ───────────────────────────────────────────────────────────────

/**
 * Spawn tools/admin.py with the given argv. Resolves with the parsed JSON
 * envelope from stdout on success; rejects with the JSON envelope or stderr
 * string on failure.
 */
SynthwaveDisplay.prototype._admin = function (args) {
  const self = this;
  const defer = libQ.defer();
  const adminPath = path.join(__dirname, DISPLAY_SUBDIR, ADMIN_REL_PATH);
  const proc = spawn('python3', [adminPath].concat(args));
  let stdout = '';
  let stderr = '';
  proc.stdout.on('data', (chunk) => { stdout += chunk; });
  proc.stderr.on('data', (chunk) => { stderr += chunk; });
  proc.on('error', (err) => defer.reject('spawn failed: ' + err.message));
  proc.on('close', (code) => {
    if (code === 0) {
      try { defer.resolve(JSON.parse(stdout)); }
      catch (e) { defer.reject('admin.py stdout not JSON: ' + stdout); }
    } else {
      // admin.py errors land on stderr as JSON too — try to parse it.
      let payload;
      try { payload = JSON.parse(stderr); }
      catch (e) { payload = { status: 'error', message: stderr || ('exit ' + code) }; }
      self.logger.warn('[synthwave_display] admin ' + args.join(' ') + ' → ' + JSON.stringify(payload));
      defer.reject(payload);
    }
  });
  return defer.promise;
};

/**
 * Wrap a systemctl action in a promise. Used by onStart/onStop. The admin.py
 * restart command does the equivalent for runtime-triggered restarts.
 */
SynthwaveDisplay.prototype._systemctl = function (action) {
  const defer = libQ.defer();
  const proc = spawn('sudo', ['systemctl', action, SYSTEMD_UNIT]);
  let stderr = '';
  proc.stderr.on('data', (c) => { stderr += c; });
  proc.on('error', (err) => defer.reject('spawn failed: ' + err.message));
  proc.on('close', (code) => {
    if (code === 0) defer.resolve();
    else defer.reject('systemctl ' + action + ' exit ' + code + ': ' + stderr);
  });
  return defer.promise;
};

/** Helper: locate a content entry by section id + field id in a UIConfig doc. */
SynthwaveDisplay.prototype._findContent = function (uiconf, sectionId, fieldId) {
  if (!uiconf || !uiconf.sections) return null;
  const sec = uiconf.sections.find((s) => s.id === sectionId);
  if (!sec || !sec.content) return null;
  return sec.content.find((c) => c.id === fieldId) || null;
};

/** Helper: set a field's value if it exists. Silently no-ops if missing.
 * Accepts plain values or {value, label} objects for select fields. */
SynthwaveDisplay.prototype._setField = function (uiconf, sectionId, fieldId, val) {
  if (val === undefined || val === null) return;
  const field = this._findContent(uiconf, sectionId, fieldId);
  if (field) field.value = val;
};

/** Generic section saver. `fields` is a list of [ui_id, section, key, kind]
 * tuples — kind is 'bool', 'number', 'string', or 'select'. The handler
 * extracts each field from `data`, formats it, calls admin.py set-runtime,
 * and finally restarts the service. Used by every saveX handler below. */
SynthwaveDisplay.prototype._saveSectionFields = function (data, fields) {
  const self = this;
  const calls = fields.map((tuple) => {
    const [uiId, section, key, kind] = tuple;
    let raw = data[uiId];
    if (raw && raw.value !== undefined) raw = raw.value;  // select fields
    let val;
    switch (kind) {
      case 'bool':   val = raw ? 'true' : 'false'; break;
      case 'number': val = String(raw); break;
      default:       val = String(raw);
    }
    return self._admin(['set-runtime', section, key, val]);
  });
  return libQ.all(calls)
    .then(() => self._admin(['restart']))
    .then(() => self._toastOk('TOAST_SAVED'))
    .fail((err) => self._toastErr('TOAST_ERROR', err));
};

SynthwaveDisplay.prototype._toastOk = function (msgKey) {
  this.commandRouter.pushToastMessage(
    'success', 'Synthwave Display',
    this.commandRouter.getI18nString('SYNTHWAVE_DISPLAY.' + msgKey) || msgKey
  );
  return libQ.resolve();
};

SynthwaveDisplay.prototype._toastErr = function (msgKey, err) {
  const detail = err && err.message ? err.message : (err ? String(err) : '');
  this.commandRouter.pushToastMessage(
    'error', 'Synthwave Display',
    (this.commandRouter.getI18nString('SYNTHWAVE_DISPLAY.' + msgKey) || msgKey) + ' ' + detail
  );
  return libQ.reject(err);
};
