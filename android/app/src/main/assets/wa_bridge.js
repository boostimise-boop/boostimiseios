// Injected into web.whatsapp.com by the fetcher (Playwright add_init_script).
// Uses WhatsApp Web's own internal modules to read chat history and decrypt media,
// so it does not depend on the (frequently changing) page layout.
//
// Why not WhatsApp's search box: WhatsApp Web only indexes recent messages for search,
// so older captioned photos never show up there. Reading the chat history (what scrolling
// up in a chat does) finds them.
// Everything is exposed as window.__waf.
(function () {
  if (window.__waf) return;

  // Message types that are real content. Anything else (system notices, album
  // headers, e2e notifications, ...) is skipped when looking at nearby messages.
  const CONTENT_TYPES = new Set([
    'chat', 'image', 'video', 'gif', 'audio', 'ptt', 'ptv', 'document', 'sticker',
    'location', 'vcard', 'multi_vcard', 'poll_creation', 'list', 'buttons', 'product',
    'order', 'event_creation',
  ]);
  // Anything shaped like a product code (2-558, 4a-103). Used to spot list/summary
  // messages that mention several codes, which must not pull in nearby photos.
  // scanChat's opts.anyCode replaces it with the shop's code style (core/codes.py: Reader.js_any_code).
  // Group 1 is the prefix ("" for codes without one, like #1023), group 2 the number.
  const ANY_CODE = /(?<![0-9A-Za-z])(\d{1,3}[A-Za-z]?)\s*-\s*(\d{2,4})(?![0-9A-Za-z])/g;
  const codeKey = (m) => (m[1] ? m[1].toLowerCase() + '-' : '') + parseInt(m[2], 10);  // as codes.norm

  function req(name) {
    try { return typeof window.require === 'function' ? window.require(name) : null; } catch (e) { return null; }
  }

  function mods() {
    const col = req('WAWebCollections');
    if (!col || !col.Msg || !col.Chat) return null;
    const dm = req('WAWebDownloadManager');
    const sock = req('WAWebSocketModel');
    return {
      Msg: col.Msg,
      Chat: col.Chat,
      DM: dm && dm.downloadManager,
      Load: req('WAWebChatLoadMessages'),
      Socket: sock && sock.Socket,
    };
  }

  function need() {
    const M = mods();
    if (!M) throw new Error('WhatsApp Web is not ready (internal modules not found)');
    return M;
  }

  const ser = (w) => (w == null ? '' : typeof w === 'string' ? w : (w._serialized || String(w)));
  const models = (col) => (col && typeof col.getModelsArray === 'function' ? col.getModelsArray() : (col && col._models) || []);
  const errMsg = (e) => (e && (e.message || e.name)) || String(e);

  function withTimeout(promise, ms, label) {
    let timer;
    return Promise.race([
      Promise.resolve(promise).finally(() => clearTimeout(timer)),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(label || 'timed out')), ms); }),
    ]);
  }

  function isImage(m) {
    if (!m || m.isViewOnce) return false;
    return m.type === 'image' || (m.type === 'document' && /^image\//i.test(m.mimetype || ''));
  }

  // Searchable text of a message. For media the body is a thumbnail, so only the caption counts.
  function textOf(m) {
    if (!m) return '';
    if (m.type === 'chat') return m.body || '';
    return [m.caption, m.type === 'document' ? m.filename : ''].filter(Boolean).join(' ');
  }

  function sender(m) {
    if (m.id && m.id.fromMe) return 'me';
    return ser(m.author) || ser(m.from) || '';
  }

  function chatTitle(M, chatOrId) {
    const c = typeof chatOrId === 'string' ? M.Chat.get(chatOrId) : chatOrId;
    if (!c) return typeof chatOrId === 'string' ? chatOrId : '';
    let title = '';
    try { title = c.formattedTitle || c.name || ''; } catch (e) { /* getter can throw on odd chats */ }
    if (!title && c.contact) title = c.contact.name || c.contact.pushname || c.contact.verifiedName || '';
    return title || (c.id && c.id.user) || ser(c.id);
  }

  function info(M, m, via) {
    const chatId = ser(m.id.remote);
    return {
      id: ser(m.id),
      chatId,
      chat: chatTitle(M, chatId),
      t: m.t || 0,
      type: m.type,
      mimetype: m.mimetype || '',
      caption: (m.caption || '').slice(0, 200),
      via,
    };
  }

  async function getMsg(M, id) {
    let m = M.Msg.get(id);
    if (!m && typeof M.Msg.getMessagesById === 'function') {
      try {
        const r = await M.Msg.getMessagesById([id]);
        m = r && r.messages && r.messages[0];
      } catch (e) { /* not in local DB */ }
    }
    return m || null;
  }

  // Load one more page of older messages. Current WhatsApp takes ({chat, msgCollection}),
  // older builds took (chat). Resolves to the loaded messages ([] / undefined at the start).
  async function loadEarlier(L, chat) {
    if (!L || typeof L.loadEarlierMsgs !== 'function') return null;
    try {
      return await withTimeout(L.loadEarlierMsgs({ chat, msgCollection: chat.msgs }), 45000, 'loading older messages timed out');
    } catch (e) {
      if (!/waitForChatLoading|msgLoadState|of undefined/.test(errMsg(e))) throw e;
      return await withTimeout(L.loadEarlierMsgs(chat), 45000, 'loading older messages timed out');
    }
  }

  // Walk from the anchor message in one direction and collect uncaptioned images
  // sent by the same person right before/after it (albums, "code as text" posts).
  function walk(arr, idx, step, anchor, re, gapSec, maxN) {
    const out = [];
    const who = sender(anchor);
    let lastT = anchor.t || 0;
    for (let j = idx + step; j >= 0 && j < arr.length && out.length < maxN; j += step) {
      const m = arr[j];
      if (!m || !CONTENT_TYPES.has(m.type)) continue;
      if (Math.abs((m.t || 0) - lastT) > gapSec) break;
      if (sender(m) !== who) break;
      if (!isImage(m)) break;
      const cap = textOf(m).trim();
      if (cap && !re.test(cap)) break; // captioned for a different item
      out.push(m);
      lastT = m.t || lastT;
    }
    return out;
  }

  const oldestT = (arr) => arr.reduce((min, m) => Math.min(min, (m && m.t) || Infinity), Infinity);

  // downloadAndMaybeDecrypt() wants a performance-logger object; this stub accepts any call.
  function fakeQpl() {
    const p = new Proxy(function () {}, {
      get: (t, k) => {
        if (k === 'then') return undefined;
        if (k === Symbol.toPrimitive) return () => '';
        return () => p;
      },
      apply: () => p,
    });
    return p;
  }

  function toBytes(b) {
    if (!b) return null;
    if (b instanceof Uint8Array) return b;
    if (b instanceof ArrayBuffer) return new Uint8Array(b);
    if (ArrayBuffer.isView(b)) return new Uint8Array(b.buffer, b.byteOffset, b.byteLength);
    return null;
  }

  function toB64(bytes) {
    let bin = '';
    const CH = 0x8000;
    for (let i = 0; i < bytes.length; i += CH) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
    }
    return btoa(bin);
  }

  async function cachedBlob(m) {
    const md = m.mediaData;
    if (!md) return null;
    const mb = md.mediaBlob;
    if (mb) {
      if (mb instanceof Blob) return mb;
      if (typeof mb.forceToBlob === 'function') {
        const b = mb.forceToBlob();
        if (b instanceof Blob) return b;
      }
    }
    const url = md.renderableUrl;
    if (typeof url === 'string' && url.startsWith('blob:')) {
      const r = await fetch(url);
      return await r.blob();
    }
    return null;
  }

  window.__waf = {
    status() {
      const s = {
        version: (window.Debug && window.Debug.VERSION) || '',
        pane: !!document.querySelector('#pane-side'),
        qr: !!document.querySelector('canvas[aria-label*="QR" i], [data-ref] canvas'),
      };
      const M = mods();
      s.modules = !!M;
      if (M) {
        try { s.socket = (M.Socket && M.Socket.state) || ''; } catch (e) { s.socket = ''; }
        try { s.chats = models(M.Chat).length; } catch (e) { s.chats = 0; }
      }
      if (/UNPAIRED/.test(s.socket || '')) s.qr = true;
      s.ready = !!M && !s.qr && (s.pane || s.socket === 'CONNECTED');
      return s;
    },

    listChats() {
      const M = need();
      return models(M.Chat)
        .map((c) => ({ id: ser(c.id), title: chatTitle(M, c), t: c.t || 0 }))
        .filter((c) => c.id && c.title)
        .sort((a, b) => b.t - a.t)
        .slice(0, 1500);
    },

    // Chats to read: matching the filter and with activity since `since` (unix seconds).
    // opts: {since, chatId?, chatTitle?, groupsOnly?}
    scanTargets(opts) {
      const M = need();
      const needle = (opts.chatTitle || '').toLowerCase();
      return models(M.Chat)
        .filter((c) => {
          const id = ser(c.id);
          if (!id || id.endsWith('@broadcast')) return false;
          if (opts.chatId) return id === opts.chatId;
          if (opts.groupsOnly && !id.endsWith('@g.us')) return false;
          if (needle && !chatTitle(M, c).toLowerCase().includes(needle)) return false;
          return !c.t || c.t >= opts.since;
        })
        .sort((a, b) => (b.t || 0) - (a.t || 0))
        .map((c) => ({ id: ser(c.id), title: chatTitle(M, c) }));
    },

    // Read one chat back to `since` and find the photos for every code in one pass.
    // opts: {codes: [{key, pattern}], since, neighbors, gapMinutes, maxNeighbors, maxLoads}
    async scanChat(chatId, opts) {
      const M = need();
      const chat = M.Chat.get(chatId);
      if (!chat || !chat.msgs) return { error: 'chat not found' };
      const codes = opts.codes.map((c) => ({ key: c.key, re: new RegExp(c.pattern, 'i') }));
      const since = opts.since || 0;
      const anyCode = opts.anyCode ? new RegExp(opts.anyCode, 'gi') : ANY_CODE;
      const mode = opts.neighbors || 'none';
      const after = mode === 'after' || mode === 'both';
      const before = mode === 'before' || mode === 'both';
      const gapSec = Math.max(1, opts.gapMinutes || 5) * 60;
      const maxN = opts.maxNeighbors || 30;
      const warnings = [];

      // getModelsArray() is WhatsApp's live list (it grows in place), so count before loading.
      // A chat read while history was still syncing can be stuck on "no earlier messages": retry it.
      if (chat.msgs.msgLoadState) chat.msgs.msgLoadState.noEarlierMsgs = false;
      let loads = 0;
      let exhausted = false;
      while (oldestT(models(chat.msgs)) > since && loads < (opts.maxLoads || 300)) {
        const prevLen = models(chat.msgs).length;
        let got;
        try { got = await loadEarlier(M.Load, chat); } catch (e) { warnings.push(errMsg(e)); break; }
        loads++;
        if (!got || !got.length || models(chat.msgs).length === prevLen) { exhausted = true; break; }
      }
      const arr = models(chat.msgs).slice();

      const hits = {};
      const mentions = {};
      const add = (key, m, via) => {
        const bucket = hits[key] || (hits[key] = {});
        const id = ser(m.id);
        if (!bucket[id]) bucket[id] = info(M, m, via);
      };
      for (let i = 0; i < arr.length; i++) {
        const m = arr[i];
        if (!m || !m.id || (m.t || 0) < since) continue;
        const text = textOf(m);
        if (!text) continue;
        const found = codes.filter((c) => c.re.test(text));
        if (!found.length) continue;
        const distinct = new Set([...text.matchAll(anyCode)].map(codeKey));
        const isList = found.length > 1 || distinct.size > 1;
        for (const c of found) mentions[c.key] = (mentions[c.key] || 0) + 1;
        if (isImage(m)) for (const c of found) add(c.key, m, 'caption');
        if (!isList && mode !== 'none') {
          const c = found[0];
          if (after) walk(arr, i, +1, m, c.re, gapSec, maxN).forEach((x) => add(c.key, x, 'nearby'));
          if (before) walk(arr, i, -1, m, c.re, gapSec, maxN).forEach((x) => add(c.key, x, 'nearby'));
        }
      }

      const out = {};
      for (const k of Object.keys(hits)) out[k] = Object.values(hits[k]);
      const oldest = oldestT(arr);
      return {
        title: chatTitle(M, chat),
        loaded: arr.length,
        loads,
        oldest: Number.isFinite(oldest) ? oldest : 0,
        reached: oldest <= since,
        exhausted,
        hits: out,
        mentions,
        warnings,
      };
    },

    async download(id) {
      const M = need();
      const m = await getMsg(M, id);
      if (!m) return { error: 'message not found' };
      const errors = [];
      const ok = (bytes) => ({ data: toB64(bytes), mimetype: m.mimetype || '', filename: m.filename || '', size: bytes.length });

      if (M.DM && typeof M.DM.downloadAndMaybeDecrypt === 'function' && m.directPath && m.mediaKey) {
        try {
          const buf = await withTimeout(M.DM.downloadAndMaybeDecrypt({
            directPath: m.directPath,
            encFilehash: m.encFilehash,
            filehash: m.filehash,
            mediaKey: m.mediaKey,
            mediaKeyTimestamp: m.mediaKeyTimestamp,
            type: m.type,
            mimetype: m.mimetype,
            signal: new AbortController().signal,
            downloadQpl: fakeQpl(),
          }), 90000, 'download timed out');
          const bytes = toBytes(buf);
          if (bytes && bytes.length) return ok(bytes);
          errors.push('empty download');
        } catch (e) {
          errors.push('decrypt: ' + errMsg(e));
        }
      }

      // Fallback: let WhatsApp's own pipeline fetch the media, then read the blob it keeps.
      try {
        if (m.mediaData && m.mediaData.mediaStage !== 'RESOLVED' && typeof m.downloadMedia === 'function') {
          await withTimeout(m.downloadMedia({ downloadEvenIfExpensive: true, rmrReason: 1, isUserInitiated: true }), 90000, 'downloadMedia timed out');
        }
        const blob = await cachedBlob(m);
        if (blob) {
          const bytes = new Uint8Array(await blob.arrayBuffer());
          if (bytes.length) return ok(bytes);
        }
        errors.push('no cached copy');
      } catch (e) {
        errors.push('fallback: ' + errMsg(e));
      }
      return { error: errors.join('; ') };
    },
  };
})();
