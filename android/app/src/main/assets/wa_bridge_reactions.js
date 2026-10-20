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
      Reactions: col.Reactions,
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

  // Load the chat's history back to `since` (unix seconds). Returns {arr, loads, exhausted}.
  async function loadBack(M, chat, since, maxLoads, warnings) {
    // getModelsArray() is WhatsApp's live list (it grows in place), so count before loading.
    // A chat read while history was still syncing can be stuck on "no earlier messages": retry it.
    if (chat.msgs.msgLoadState) chat.msgs.msgLoadState.noEarlierMsgs = false;
    let loads = 0;
    let exhausted = false;
    while (oldestT(models(chat.msgs)) > since && loads < (maxLoads || 300)) {
      const prevLen = models(chat.msgs).length;
      let got;
      try { got = await loadEarlier(M.Load, chat); } catch (e) { warnings.push(errMsg(e)); break; }
      loads++;
      if (!got || !got.length || models(chat.msgs).length === prevLen) { exhausted = true; break; }
    }
    return { arr: models(chat.msgs).slice(), loads, exhausted };
  }

  // The message that carries the text for an uncaptioned photo: the captioned photo or text
  // message the same person sent right before it (an album, or "code, then photos").
  function anchorBefore(arr, idx, gapSec) {
    const photo = arr[idx];
    const who = sender(photo);
    let lastT = photo.t || 0;
    for (let j = idx - 1; j >= 0; j--) {
      const m = arr[j];
      if (!m || !CONTENT_TYPES.has(m.type)) continue;
      if (Math.abs(lastT - (m.t || 0)) > gapSec || sender(m) !== who) return null;
      if (textOf(m).trim()) return m;
      if (!isImage(m)) return null;
      lastT = m.t || lastT;
    }
    return null;
  }

  // ---- reactions ------------------------------------------------------------------
  // WhatsApp keeps every reaction in IndexedDB 'model-storage' > 'reactions'
  // ({parentMsgKey, senderUserJid, reactionText, ...}) and links album photos to their
  // album message in 'message-association'. Messages are matched on the stanza id (the
  // third part of 'false_<chat>_<id>_<author>'): the chat/author part can be written as a
  // phone number or as a @lid depending on where the key came from.

  // '❤️' and '❤', '👍🏻' and '👍' count as the same reaction.
  const normEmoji = (s) => String(s || '').replace(/[︎️\u{1F3FB}-\u{1F3FF}]/gu, '').trim();

  function splitEmojis(s) {
    if (!s) return [];
    const parts = typeof Intl !== 'undefined' && Intl.Segmenter
      ? Array.from(new Intl.Segmenter('en', { granularity: 'grapheme' }).segment(s), (x) => x.segment)
      : Array.from(s);
    return [...new Set(parts.map(normEmoji).filter((e) => e && !/^[,;.]+$/.test(e)))];
  }

  function stanzaId(k) {
    if (!k) return '';
    if (typeof k === 'object') return typeof k.id === 'string' ? k.id : stanzaId(k._serialized);
    const p = String(k).split('_');
    return p.length >= 3 && (p[0] === 'true' || p[0] === 'false') ? p[2] : String(k);
  }

  function idbOpen(name) {
    return new Promise((resolve, reject) => {
      const r = indexedDB.open(name);
      r.onupgradeneeded = () => r.transaction.abort(); // never create WhatsApp's database
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error || new Error('cannot open ' + name));
      r.onblocked = () => reject(new Error(name + ' is blocked'));
    });
  }

  function idbAll(db, store) {
    return new Promise((resolve, reject) => {
      const r = db.transaction(store, 'readonly').objectStore(store).getAll();
      r.onsuccess = () => resolve(r.result || []);
      r.onerror = () => reject(r.error);
    });
  }

  let reactCache = null; // {at, byId: Map<stanza id, Set<emoji>>, albumOf: Map<photo id, album id>}

  async function reactionIndex(maxAgeMs) {
    if (reactCache && Date.now() - reactCache.at <= maxAgeMs) return reactCache;
    const db = await withTimeout(idbOpen('model-storage'), 15000, 'opening the database timed out');
    try {
      if (!db.objectStoreNames.contains('reactions')) throw new Error('no reactions table');
      const byId = new Map();
      for (const r of await idbAll(db, 'reactions')) {
        const e = normEmoji(r.reactionText);
        const id = stanzaId(r.parentMsgKey);
        if (!e || !id) continue; // empty text = reaction was removed
        if (!byId.has(id)) byId.set(id, new Set());
        byId.get(id).add(e);
      }
      const albumOf = new Map();
      if (db.objectStoreNames.contains('message-association')) {
        for (const a of await idbAll(db, 'message-association')) {
          if (a.associationType && !/ALBUM/i.test(a.associationType)) continue;
          const child = stanzaId(a.msgKey);
          const parent = stanzaId(a.parentMsgKey);
          if (child && parent) albumOf.set(child, parent);
        }
      }
      reactCache = { at: Date.now(), byId, albumOf };
      return reactCache;
    } finally {
      db.close();
    }
  }

  // Ask WhatsApp's in-memory reaction store (used when the database can't be read, or for
  // a reaction that arrived moments ago and isn't saved yet).
  async function liveEmojis(M, key) {
    const out = new Set();
    if (!M.Reactions || typeof M.Reactions.find !== 'function' || !key || typeof key !== 'object') return out;
    try {
      const r = await withTimeout(M.Reactions.find(key), 10000, 'reading reactions timed out');
      for (const agg of models(r && r.reactions)) {
        const e = normEmoji(agg.aggregateEmoji);
        if (e) out.add(e);
        for (const s of models(agg.senders)) {
          const t = normEmoji(s.reactionText);
          if (t) out.add(t);
        }
      }
    } catch (e) { /* no reactions for this message */ }
    return out;
  }

  // Returns m => Set of the reactions on the message itself and on its album.
  // flaggedOnly: without the database, ask WhatsApp only about messages it flags as reacted
  // (for when msgs is a whole chat, not a few photos).
  async function reactionLookup(M, msgs, fresh, warnings, flaggedOnly = false) {
    let idx = null;
    try {
      idx = await reactionIndex(fresh ? 0 : 60000);
    } catch (e) {
      warnings.push('could not read reactions from the local database (' + errMsg(e) + '), asking WhatsApp instead');
    }
    const albumKey = (m) => m.parentMsgKey || (idx && idx.albumOf.get(stanzaId(m.id))) || '';
    const live = new Map();
    for (const m of msgs) {
      for (const k of [m.id, albumKey(m)]) {
        const id = stanzaId(k);
        if (!id || live.has(id)) continue;
        // With the database, only ask about messages WhatsApp flags as reacted but the DB doesn't know yet.
        if (idx && (idx.byId.has(id) || k !== m.id || !m.hasReaction)) continue;
        if (!idx && flaggedOnly && k === m.id && !m.hasReaction) continue;
        live.set(id, await liveEmojis(M, k));
      }
    }
    const of = (id) => new Set([...((idx && idx.byId.get(id)) || []), ...(live.get(id) || [])]);
    return (m) => {
      const s = of(stanzaId(m.id));
      const album = stanzaId(albumKey(m));
      if (album) of(album).forEach((e) => s.add(e));
      return s;
    };
  }

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
    // opts: {codes: [{key, pattern}], since, neighbors, gapMinutes, maxNeighbors, maxLoads, reactions?}
    // reactions: e.g. '❌📵😥' - keep only photos reacted with one of these, on the photo
    // itself, on its album, or on the message that carries the code.
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
      const wanted = splitEmojis(opts.reactions);
      const warnings = [];
      const { arr, loads, exhausted } = await loadBack(M, chat, since, opts.maxLoads, warnings);

      const hits = {};
      const mentions = {};
      const add = (key, m, via, react) => {
        const bucket = hits[key] || (hits[key] = {});
        const id = ser(m.id);
        if (!bucket[id]) bucket[id] = info(M, m, via);
        if (react && !bucket[id].react) bucket[id].react = react;
      };
      // One group per message carrying a code: [message, its photos (itself if captioned + nearby)].
      const groups = [];
      for (let i = 0; i < arr.length; i++) {
        const m = arr[i];
        if (!m || !m.id || (m.t || 0) < since) continue;
        const text = textOf(m);
        if (!text) continue;
        const found = codes.filter((c) => c.re.test(text));
        if (!found.length) continue;
        const distinct = new Set([...text.matchAll(anyCode)].map(codeKey));
        const isList = found.length > 1 || distinct.size > 1;
        for (const c of found) {
          mentions[c.key] = (mentions[c.key] || 0) + 1;
          const imgs = isImage(m) ? [[m, 'caption']] : [];
          if (!isList && mode !== 'none') {
            if (after) walk(arr, i, +1, m, c.re, gapSec, maxN).forEach((x) => imgs.push([x, 'nearby']));
            if (before) walk(arr, i, -1, m, c.re, gapSec, maxN).forEach((x) => imgs.push([x, 'nearby']));
          }
          if (imgs.length) groups.push({ key: c.key, anchor: m, imgs });
        }
      }

      const skipped = {}; // key -> ids of photos found without a wanted reaction
      if (!wanted.length) {
        for (const g of groups) g.imgs.forEach(([x, via]) => add(g.key, x, via));
      } else if (groups.length) {
        const candidates = groups.flatMap((g) => [g.anchor, ...g.imgs.map(([x]) => x)]);
        const reactionsOf = await reactionLookup(M, candidates, loads > 0, warnings);
        const match = (m) => { const s = reactionsOf(m); return wanted.filter((e) => s.has(e)).join(''); };
        for (const g of groups) {
          const onAnchor = match(g.anchor);
          for (const [x, via] of g.imgs) {
            const react = (x === g.anchor ? '' : match(x)) || onAnchor;
            if (react) add(g.key, x, via, react);
            else (skipped[g.key] || (skipped[g.key] = new Set())).add(ser(x.id));
          }
        }
      }

      const out = {};
      for (const k of Object.keys(hits)) out[k] = Object.values(hits[k]);
      const unreacted = {};
      for (const k of Object.keys(skipped)) {
        const n = [...skipped[k]].filter((id) => !(hits[k] && hits[k][id])).length;
        if (n) unreacted[k] = n;
      }
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
        unreacted,
        warnings,
      };
    },

    // Every message since `since` reacted with one of `reactions` (on itself or on its album),
    // given by the text that names it: its own text / caption, or for an uncaptioned photo the
    // captioned photo or text message the same person sent right before it. The codes in the
    // text are read by the Reaction List tool.
    // opts: {since, reactions, gapMinutes, maxLoads}
    // hits: one per text message, react = the wanted emojis on it and on its photos, in the
    // order asked for. orphans: reacted photos with no text before them.
    async reactedMessages(chatId, opts) {
      const M = need();
      const chat = M.Chat.get(chatId);
      if (!chat || !chat.msgs) return { error: 'chat not found' };
      const wanted = splitEmojis(opts.reactions);
      if (!wanted.length) return { error: 'no reactions to look for' };
      const since = opts.since || 0;
      const gapSec = Math.max(1, opts.gapMinutes || 5) * 60;
      const warnings = [];
      const { arr, loads, exhausted } = await loadBack(M, chat, since, opts.maxLoads, warnings);

      const counts = (m) => m && m.id && (m.t || 0) >= since && CONTENT_TYPES.has(m.type);
      const recent = arr.filter(counts);
      const byAnchor = new Map(); // anchor message id -> {anchor, emojis}
      let orphans = 0;
      if (recent.length) {
        const reactionsOf = await reactionLookup(M, recent, loads > 0, warnings, true);
        for (let i = 0; i < arr.length; i++) {
          const m = arr[i];
          if (!counts(m)) continue;
          const s = reactionsOf(m);
          const found = wanted.filter((e) => s.has(e));
          if (!found.length) continue;
          const anchor = textOf(m).trim() ? m : isImage(m) ? anchorBefore(arr, i, gapSec) : null;
          if (!anchor) { orphans++; continue; }
          const id = ser(anchor.id);
          if (!byAnchor.has(id)) byAnchor.set(id, { anchor, emojis: new Set() });
          found.forEach((e) => byAnchor.get(id).emojis.add(e));
        }
      }
      const hits = [...byAnchor.values()].map(({ anchor, emojis }) => Object.assign(info(M, anchor, 'react'), {
        text: textOf(anchor).slice(0, 2000),
        react: wanted.filter((e) => emojis.has(e)).join(''),
      }));
      const oldest = oldestT(arr);
      return {
        title: chatTitle(M, chat),
        loaded: arr.length,
        loads,
        oldest: Number.isFinite(oldest) ? oldest : 0,
        reached: oldest <= since,
        exhausted,
        hits,
        orphans,
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
