// Injected into web.whatsapp.com by the Android app, next to wa_bridge.js / wa_bridge_reactions.js
// (the Windows app's scripts, unchanged). Reads a chat's messages through WhatsApp Web's own
// internal modules, so Model Detect and Pathao Auto-fill need no scrolling or clicking.
// Exposed as window.__wab.
(function () {
  if (window.__wab) return;

  const CONTENT_TYPES = new Set([
    'chat', 'image', 'video', 'gif', 'audio', 'ptt', 'ptv', 'document', 'sticker',
    'location', 'vcard', 'multi_vcard', 'poll_creation', 'list', 'buttons', 'product',
    'order', 'event_creation',
  ]);

  function req(name) {
    try { return typeof window.require === 'function' ? window.require(name) : null; } catch (e) { return null; }
  }
  function mods() {
    const col = req('WAWebCollections');
    if (!col || !col.Msg || !col.Chat) return null;
    return { Msg: col.Msg, Chat: col.Chat, Load: req('WAWebChatLoadMessages') };
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
  function textOf(m) {
    if (!m) return '';
    if (m.type === 'chat') return m.body || '';
    return [m.caption, m.type === 'document' ? m.filename : ''].filter(Boolean).join(' ');
  }
  function sender(m) {
    if (m.id && m.id.fromMe) return 'me';
    return ser(m.author) || ser(m.from) || '';
  }
  async function loadEarlier(L, chat) {
    if (!L || typeof L.loadEarlierMsgs !== 'function') return null;
    try {
      return await withTimeout(L.loadEarlierMsgs({ chat, msgCollection: chat.msgs }), 45000, 'loading older messages timed out');
    } catch (e) {
      if (!/waitForChatLoading|msgLoadState|of undefined/.test(errMsg(e))) throw e;
      return await withTimeout(L.loadEarlierMsgs(chat), 45000, 'loading older messages timed out');
    }
  }
  const oldestT = (arr) => arr.reduce((min, m) => Math.min(min, (m && m.t) || Infinity), Infinity);

  window.__wab = {
    // Every content message of a chat since `since` (unix seconds), oldest first.
    // opts: {since, maxLoads, maxText, stopWhen?: regex source - stop loading once the oldest
    // loaded page has a message matching it AND is older than `since`}
    async messages(chatId, opts) {
      const M = need();
      const chat = M.Chat.get(chatId);
      if (!chat || !chat.msgs) return { error: 'chat not found' };
      const since = opts.since || 0;
      const maxText = opts.maxText || 3000;
      const warnings = [];
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
      const out = [];
      for (const m of arr) {
        if (!m || !m.id || (m.t || 0) < since || !CONTENT_TYPES.has(m.type)) continue;
        out.push({
          id: ser(m.id),
          t: m.t || 0,
          type: m.type,
          image: isImage(m),
          text: textOf(m).slice(0, maxText),
          from: sender(m),
        });
      }
      const oldest = oldestT(arr);
      return {
        loaded: arr.length,
        loads,
        oldest: Number.isFinite(oldest) ? oldest : 0,
        reached: oldest <= since,
        exhausted,
        messages: out,
        warnings,
      };
    },
  };
})();
