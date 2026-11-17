// Watches a claude.ai chat: Claude writing -> "working", Claude finished -> "done"
// (with the question, if its reply ends by asking you something).
// Cheap: it only looks when the page changes, and only reports when the state flips.
let chatId = null, streaming = false, doneTimer = null, title = "", asked = "";

const idFromUrl = () => (location.pathname.match(/\/chat\/([0-9a-f-]{36})/) || [])[1] || null;
const isStreaming = () =>
  !!document.querySelector('[data-is-streaming="true"], button[aria-label="Stop response"]');
const cleanTitle = () => document.title.replace(/\s*-\s*Claude\s*$/, "").trim() || "Claude chat";

// claude.ai's "Claude asked you a question" box, or a reply that ends with "?" -> the question.
function question() {
  const card = document.querySelector('[data-testid="hub-ask-user-question-card"]');
  if (card) {
    const lines = card.innerText.split("\n").map(l => l.trim()).filter(Boolean);
    return (lines.find(l => !/asked you a question/i.test(l)) || "Asked you a question").slice(0, 200);
  }
  const msgs = document.querySelectorAll("[data-is-streaming]");
  const last = msgs[msgs.length - 1];
  if (!last) return "";
  // only the reply's text, not the buttons / "4 minutes ago" under it
  const md = last.querySelectorAll('[class*="markdown"]');
  const text = md.length ? md[md.length - 1].innerText : "";
  const line = (text || "").trim().split("\n").pop().trim();
  return /\?[\s*_)"'\u201d\u2019]*$/.test(line) ? line.slice(0, 200) : "";
}

function send(event) {
  if (!chatId) return;
  try {
    chrome.runtime.sendMessage({ event, id: "web-" + chatId, title: cleanTitle(), url: location.href,
                                 question: event === "done" ? question() : "" });
  } catch (e) { /* extension reloaded; ignore */ }
}

function check() {
  const id = idFromUrl();
  if (id !== chatId) {                 // switched chats in this tab
    if (chatId) send("end");
    chatId = id; streaming = false; asked = "";
  }
  if (!chatId) return;
  // the question box can show up on its own (no streaming in between) -> report it once
  const q = document.querySelector('[data-testid="hub-ask-user-question-card"]') ? question() : "";
  if (q && q !== asked && !streaming) { asked = q; send("done"); }
  if (!q) asked = "";
  const now = isStreaming();
  if (now && !streaming) {
    streaming = true;
    clearTimeout(doneTimer);
    send("working");
  } else if (!now && streaming) {
    clearTimeout(doneTimer);           // wait a beat so a pause mid-reply isn't "done"
    doneTimer = setTimeout(() => {
      if (!isStreaming()) { streaming = false; asked = question(); send("done"); }
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
