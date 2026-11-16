// Watches a claude.ai chat: Claude writing -> "working", Claude finished -> "done".
// Cheap: it only looks when the page changes, and only reports when the state flips.
let chatId = null, streaming = false, doneTimer = null, title = "";

const idFromUrl = () => (location.pathname.match(/\/chat\/([0-9a-f-]{36})/) || [])[1] || null;
const isStreaming = () =>
  !!document.querySelector('[data-is-streaming="true"], button[aria-label="Stop response"]');
const cleanTitle = () => document.title.replace(/\s*-\s*Claude\s*$/, "").trim() || "Claude chat";

function send(event) {
  if (!chatId) return;
  try {
    chrome.runtime.sendMessage({ event, id: "web-" + chatId, title: cleanTitle(), url: location.href });
  } catch (e) { /* extension reloaded; ignore */ }
}

function check() {
  const id = idFromUrl();
  if (id !== chatId) {                 // switched chats in this tab
    if (chatId) send("end");
    chatId = id; streaming = false;
  }
  if (!chatId) return;
  const now = isStreaming();
  if (now && !streaming) {
    streaming = true;
    clearTimeout(doneTimer);
    send("working");
  } else if (!now && streaming) {
    clearTimeout(doneTimer);           // wait a beat so a pause mid-reply isn't "done"
    doneTimer = setTimeout(() => {
      if (!isStreaming()) { streaming = false; send("done"); }
    }, 1500);
  } else if (!now && document.title !== title && !streaming) {
    title = document.title;            // keep the title fresh (claude.ai names chats late)
  }
}

let queued = false;
new MutationObserver(() => {
  if (queued) return;
  queued = true;
  setTimeout(() => { queued = false; check(); }, 400);
}).observe(document.documentElement, { subtree: true, childList: true, attributes: true,
                                       attributeFilter: ["data-is-streaming", "aria-label"] });
window.addEventListener("pagehide", () => send("end"));
check();
