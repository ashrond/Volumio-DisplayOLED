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

        // Populate burn-in field defaults from current runtime.toml.
        const runtime = runtimeResp.runtime || {};
        const burnin = runtime.burnin || {};
        const setField = (id, val) => {
          const f = self._findContent(uiconf, 'section_burnin', id);
          if (f) f.value = val;
        };
        setField('quiet_hours_start',           burnin.quiet_hours_start);
        setField('quiet_hours_end',             burnin.quiet_hours_end);
        setField('pixel_shift_enabled',         burnin.pixel_shift_enabled);
        setField('scanline_alternation_enabled', burnin.scanline_alternation_enabled);
        setField('track_fade_enabled',          burnin.track_fade_enabled);

        defer.resolve(uiconf);
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

SynthwaveDisplay.prototype.saveBurnin = function (data) {
  const self = this;
  // List of (toml_key, ui_id, kind) tuples. `kind` tells _admin how to format.
  const fields = [
    ['quiet_hours_start',              'quiet_hours_start',              'number'],
    ['quiet_hours_end',                'quiet_hours_end',                'number'],
    ['pixel_shift_enabled',            'pixel_shift_enabled',            'bool'],
    ['scanline_alternation_enabled',   'scanline_alternation_enabled',   'bool'],
    ['track_fade_enabled',             'track_fade_enabled',             'bool'],
  ];
  const calls = fields.map(([key, uiId, kind]) => {
    const raw = data[uiId];
    const val = kind === 'bool' ? (raw ? 'true' : 'false') : String(raw);
    return self._admin(['set-runtime', 'burnin', key, val]);
  });
  return libQ.all(calls)
    .then(() => self._admin(['restart']))
    .then(() => self._toastOk('TOAST_SAVED'))
    .fail((err) => self._toastErr('TOAST_ERROR', err));
};

SynthwaveDisplay.prototype.restartService = function () {
  const self = this;
  return self._admin(['restart'])
    .then(() => self._toastOk('TOAST_RESTARTED'))
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
