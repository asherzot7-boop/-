/* Nam Tour site chat. Embed: <script src="https://agent.YOUR-DOMAIN/webhooks/web/widget.js" defer></script>
   Optional: data-color="#0F8A87" data-title="..." on the script tag. Uses a shadow root, so the site's CSS cannot break it. */
(function () {
  "use strict";
  var script = document.currentScript;
  if (!script || window.__ntChat) return;
  window.__ntChat = true;
  var base = script.src.replace(/\/widget\.js.*$/, "");
  var color = script.getAttribute("data-color") || "#0F8A87";
  var title = script.getAttribute("data-title") || __SHOP__;
  var store = { get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
                set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };
  var session = store.get("nt_chat_session");
  if (!session) {
    session = (window.crypto && crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(36).slice(2)).replace(/[^A-Za-z0-9-]/g, "");
    store.set("nt_chat_session", session);
  }
  var lastId = +(store.get("nt_chat_last") || 0), n = +(store.get("nt_chat_n") || 0), opened = false, started = store.get("nt_chat_started") === "1", timer = null;
  var params = new URLSearchParams(location.search);
  var source = params.get("utm_source") || params.get("utm_campaign") ? { source: params.get("utm_source"), campaign: params.get("utm_campaign") } : null;

  var host = document.createElement("div");
  host.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483000";
  document.body.appendChild(host);
  var root = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
  root.innerHTML =
    '<style>' +
    ':host{all:initial}*{box-sizing:border-box;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}' +
    '.btn{width:58px;height:58px;border-radius:50%;border:0;background:' + color + ';color:#fff;font-size:26px;cursor:pointer;box-shadow:0 6px 24px rgba(0,0,0,.25)}' +
    '.panel{position:absolute;right:0;bottom:72px;width:min(360px,calc(100vw - 32px));height:min(520px,calc(100vh - 120px));background:#fff;color:#142030;border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.28);display:none;flex-direction:column;overflow:hidden}' +
    '.panel.open{display:flex}.head{background:' + color + ';color:#fff;padding:14px 16px;font-weight:700;display:flex;justify-content:space-between;align-items:center}' +
    '.head button{background:transparent;border:0;color:#fff;font-size:20px;cursor:pointer}' +
    '.log{flex:1;overflow:auto;padding:12px;display:flex;flex-direction:column;gap:8px;background:#F3F6FA}' +
    '.m{max-width:85%;padding:9px 12px;border-radius:14px;white-space:pre-wrap;word-wrap:break-word;font-size:14px;line-height:1.4}' +
    '.in{align-self:flex-end;background:' + color + ';color:#fff;border-bottom-right-radius:4px}.out{align-self:flex-start;background:#fff;border:1px solid #DCE3EC;border-bottom-left-radius:4px}' +
    'form{display:flex;gap:8px;padding:10px;border-top:1px solid #E3E8EF;background:#fff}input{flex:1;border:1px solid #CBD5E1;border-radius:10px;padding:10px;font-size:14px;color:#142030}' +
    'form button{border:0;border-radius:10px;background:' + color + ';color:#fff;padding:0 14px;font-weight:700;cursor:pointer}' +
    '.typing{font-size:12px;color:#5A6A80;padding:0 14px 6px}' +
    '</style>' +
    '<div class="panel" role="dialog" aria-label="Chat"><div class="head"><span class="t"></span><button class="x" aria-label="Close">×</button></div>' +
    '<div class="log" aria-live="polite"></div><div class="typing" hidden>…</div>' +
    '<form><input type="text" maxlength="1000" placeholder="Xabar yozing / Mesaj / Сообщение" aria-label="Message"><button type="submit">➤</button></form></div>' +
    '<button class="btn" aria-label="Chat">💬</button>';
  var $ = function (s) { return root.querySelector(s); };
  $(".t").textContent = title;
  var log = $(".log"), panel = $(".panel"), input = $("input"), typing = $(".typing");

  function add(text, who) {
    var d = document.createElement("div"); d.className = "m " + who; d.textContent = text;
    log.appendChild(d); log.scrollTop = log.scrollHeight;
  }
  function saveLast(id) { if (id > lastId) { lastId = id; store.set("nt_chat_last", String(id)); } }
  function send(text) {
    n += 1; store.set("nt_chat_n", String(n));
    typing.hidden = false;
    return fetch(base + "/chat", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session: session, text: text, n: n, source: source }) })
      .then(function (r) { return r.ok ? r.json() : { reply: "⚠️" }; })
      .then(function (j) { typing.hidden = true; if (j.reply) add(j.reply, "out"); saveLast(j.last_id || 0); })
      .catch(function () { typing.hidden = true; add("⚠️ Internet?", "out"); });
  }
  function poll() {
    fetch(base + "/poll?session=" + encodeURIComponent(session) + "&after=" + lastId)
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (list) { list.forEach(function (m) { add(m.text, "out"); saveLast(m.id); }); })
      .catch(function () {});
  }
  function open() {
    opened = !opened; panel.classList.toggle("open", opened);
    clearInterval(timer);
    if (!opened) return;
    if (!started) { started = true; store.set("nt_chat_started", "1"); send(""); } else poll();
    timer = setInterval(poll, 5000);
    input.focus();
  }
  $(".btn").addEventListener("click", open);
  $(".x").addEventListener("click", open);
  $("form").addEventListener("submit", function (e) {
    e.preventDefault();
    var t = input.value.trim(); if (!t) return;
    input.value = ""; add(t, "in"); send(t);
  });
})();
