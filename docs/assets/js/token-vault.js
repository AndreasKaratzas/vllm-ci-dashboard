/**
 * Token vault — AES-GCM envelope for user-scoped secrets in the browser.
 *
 * Threat model (what this actually protects against)
 * --------------------------------------------------
 * The concrete risks for a single-page dashboard that persists a Buildkite
 * token and a HuggingFace token in the browser are:
 *
 *   1. An attacker dumps ``sessionStorage`` via DevTools, a malicious browser
 *      extension with storage-read permission, or a stale browser profile.
 *   2. A later-loaded script on the same origin reads ``sessionStorage``
 *      directly (e.g. a vendored snippet that starts doing something it
 *      shouldn't).
 *   3. A network-level adversary intercepts a request in flight — mitigated
 *      by TLS + the CSP ``connect-src`` allowlist, not by us.
 *
 * For (1) and (2), storing raw tokens means a single key lookup dumps every
 * secret in cleartext. The vault instead stores AES-GCM ciphertext and keeps
 * the unwrap key only in a closure variable — so a process that can read
 * ``sessionStorage`` but cannot attach a debugger to this closure gets opaque
 * bytes.
 *
 * Key derivation (PAT-based, since we moved off passwords)
 * --------------------------------------------------------
 *   wrapKey = PBKDF2(pat, saltBytes, 200_000, 256, SHA-256)
 *   saltBytes = UTF-8("<github_id>|vault")
 *
 * The GitHub id is stable per user (you cannot rename away from it) and is
 * public, which is fine — it is only a salt, not a secret. The ``|vault``
 * suffix is a domain separator so the wrap key is not identical to any
 * other derivation that might reuse the same (pat, id) inputs. Iteration
 * count is hardcoded at 200k because the wrap key protects session-lifetime
 * ciphertext, not durable password hashes.
 *
 * Ciphertext format
 * -----------------
 *   sessionStorage["vllm_dashboard_enc_<name>"] =
 *     JSON.stringify({ v: 1, iv: <12-byte hex>, ct: <base64 ciphertext+tag> })
 *
 * Every ``put`` picks a fresh 12-byte IV via ``crypto.getRandomValues``;
 * never reuse an (iv, key) pair under AES-GCM or you leak plaintext xor.
 */
(function () {
  'use strict';

  var ENC_PREFIX = 'vllm_dashboard_enc_';
  var VAULT_VERSION = 1;
  var KDF_ITERATIONS = 200000;

  // Unwrap key (CryptoKey) held only in memory. Cleared by ``lock()`` and
  // by the browser when the tab closes.
  var _wrapKey = null;
  var _unlockedFor = '';  // github_id this key was derived for (informational)

  // ── byte helpers ────────────────────────────────────────────────────
  function _hexToBytes(hex) {
    var out = new Uint8Array(hex.length / 2);
    for (var i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
    return out;
  }
  function _bytesToHex(buf) {
    var u = new Uint8Array(buf), s = '';
    for (var i = 0; i < u.length; i++) {
      var h = u[i].toString(16);
      s += (h.length < 2 ? '0' : '') + h;
    }
    return s;
  }
  function _bytesToB64(buf) {
    var u = new Uint8Array(buf), bin = '';
    for (var i = 0; i < u.length; i++) bin += String.fromCharCode(u[i]);
    return btoa(bin);
  }
  function _b64ToBytes(b64) {
    var bin = atob(b64);
    var out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  // ── KDF: PAT + user-id-derived salt → AES-GCM 256 CryptoKey ─────────
  async function _deriveWrapKey(pat, userId) {
    var enc = new TextEncoder();
    var salt = enc.encode(String(userId) + '|vault');
    var material = await crypto.subtle.importKey(
      'raw', enc.encode(pat), { name: 'PBKDF2' }, false, ['deriveKey']
    );
    return crypto.subtle.deriveKey(
      { name: 'PBKDF2', salt: salt, iterations: KDF_ITERATIONS, hash: 'SHA-256' },
      material,
      { name: 'AES-GCM', length: 256 },
      false,           // non-extractable — key cannot be read out of WebCrypto
      ['encrypt', 'decrypt']
    );
  }

  // ── Public API ──────────────────────────────────────────────────────
  async function unlock(pat, userId) {
    if (!pat) throw new Error('vault.unlock: pat required');
    if (!userId || !(userId > 0)) throw new Error('vault.unlock: positive userId required');
    _wrapKey = await _deriveWrapKey(pat, userId);
    _unlockedFor = String(userId);
  }

  function lock() {
    _wrapKey = null;
    _unlockedFor = '';
    try {
      var toRemove = [];
      for (var i = 0; i < sessionStorage.length; i++) {
        var k = sessionStorage.key(i);
        if (k && k.indexOf(ENC_PREFIX) === 0) toRemove.push(k);
      }
      for (var j = 0; j < toRemove.length; j++) sessionStorage.removeItem(toRemove[j]);
    } catch (e) { /* sessionStorage unavailable — nothing to clear */ }
  }

  function isUnlocked() { return _wrapKey !== null; }
  function unlockedFor() { return _unlockedFor; }

  async function put(name, value) {
    if (!_wrapKey) throw new Error('vault.put: vault is locked');
    if (!name || typeof name !== 'string') throw new Error('vault.put: name required');
    if (value == null || value === '') { clear(name); return; }

    var iv = new Uint8Array(12);
    crypto.getRandomValues(iv);
    var ct = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv: iv },
      _wrapKey,
      new TextEncoder().encode(String(value))
    );
    var record = { v: VAULT_VERSION, iv: _bytesToHex(iv), ct: _bytesToB64(ct) };
    try { sessionStorage.setItem(ENC_PREFIX + name, JSON.stringify(record)); }
    catch (e) { throw new Error('vault.put: sessionStorage write failed'); }
  }

  async function get(name) {
    if (!_wrapKey) return '';  // locked → no access; callers decide how to prompt
    var raw;
    try { raw = sessionStorage.getItem(ENC_PREFIX + name); } catch (e) { return ''; }
    if (!raw) return '';
    var record;
    try { record = JSON.parse(raw); } catch (e) { return ''; }
    if (!record || record.v !== VAULT_VERSION || !record.iv || !record.ct) return '';
    try {
      var pt = await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: _hexToBytes(record.iv) },
        _wrapKey,
        _b64ToBytes(record.ct)
      );
      return new TextDecoder('utf-8').decode(pt);
    } catch (e) {
      // Tampered ciphertext or wrong key — treat as empty, drop the record
      // so a subsequent put() starts fresh rather than failing silently.
      try { sessionStorage.removeItem(ENC_PREFIX + name); } catch (_) {}
      return '';
    }
  }

  function clear(name) {
    try { sessionStorage.removeItem(ENC_PREFIX + name); } catch (e) {}
  }

  function has(name) {
    try { return sessionStorage.getItem(ENC_PREFIX + name) != null; }
    catch (e) { return false; }
  }

  // Decrypt a ``{v, iv, ct}`` record produced externally — e.g. by
  // ``scripts/vllm/encrypt_roster.py`` — using the same wrap key. Lets us
  // gate publicly-hosted ciphertext behind the admin's PAT without
  // re-deriving a key. Returns '' on failure (locked, malformed, or wrong
  // key) so callers can treat an unreadable blob as "no data".
  async function decryptExternal(record) {
    if (!_wrapKey) return '';
    if (!record || record.v !== VAULT_VERSION || !record.iv || !record.ct) return '';
    try {
      var pt = await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: _hexToBytes(record.iv) },
        _wrapKey,
        _b64ToBytes(record.ct)
      );
      return new TextDecoder('utf-8').decode(pt);
    } catch (e) {
      return '';
    }
  }

  window.__tokenVault = {
    unlock: unlock,
    lock: lock,
    isUnlocked: isUnlocked,
    unlockedFor: unlockedFor,
    put: put,
    get: get,
    clear: clear,
    has: has,
    decryptExternal: decryptExternal,
    VERSION: VAULT_VERSION,
  };
})();
