// Hands each event to govee_claude.py on the Mac (Chrome native messaging).
chrome.runtime.onMessage.addListener((msg) => {
  chrome.runtime.sendNativeMessage("com.govee_claude.web", msg, () => void chrome.runtime.lastError);
});
