"use strict";
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// main.ts
var main_exports = {};
__export(main_exports, {
  default: () => SecondBrainSearchPlugin
});
module.exports = __toCommonJS(main_exports);
var import_obsidian = require("obsidian");
var import_child_process = require("child_process");
var import_os = require("os");
var import_path = require("path");
var PYTHON_BIN = (0, import_path.join)((0, import_os.homedir)(), ".second-brain", "venv", "bin", "python");
var SEARCH_SCRIPT = (0, import_path.join)((0, import_os.homedir)(), ".second-brain", "search.py");
var CHAT_SCRIPT = (0, import_path.join)((0, import_os.homedir)(), ".second-brain", "chat.py");
var N_RESULTS = 8;
var TIMEOUT_MS = 3e4;
var CHAT_TIMEOUT_MS = 2e5;
function runSearch(query) {
  return new Promise((resolve, reject) => {
    (0, import_child_process.execFile)(
      PYTHON_BIN,
      [SEARCH_SCRIPT, query, String(N_RESULTS), "--json"],
      { timeout: TIMEOUT_MS },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error(stderr?.trim() || error.message));
          return;
        }
        try {
          const parsed = JSON.parse(stdout);
          if (parsed && parsed.error) {
            reject(new Error(parsed.error));
            return;
          }
          resolve(parsed);
        } catch (e) {
          reject(new Error("Invalid response from search.py: " + stdout));
        }
      }
    );
  });
}
function runChat(question, history) {
  return new Promise((resolve, reject) => {
    const child = (0, import_child_process.execFile)(
      PYTHON_BIN,
      [CHAT_SCRIPT, "--json"],
      { timeout: CHAT_TIMEOUT_MS, maxBuffer: 10 * 1024 * 1024 },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error(stderr?.trim() || error.message));
          return;
        }
        try {
          const parsed = JSON.parse(stdout);
          if (parsed.error) {
            reject(new Error(parsed.error));
            return;
          }
          resolve(parsed);
        } catch (e) {
          reject(new Error("Invalid response from chat.py: " + stdout));
        }
      }
    );
    child.stdin?.write(JSON.stringify({ question, history }));
    child.stdin?.end();
  });
}
var SecondBrainChatModal = class extends import_obsidian.Modal {
  constructor(app) {
    super(app);
    this.history = [];
  }
  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("h2", { text: "Chat with your Second Brain" });
    this.messagesEl = contentEl.createDiv({ cls: "sb-chat-messages" });
    this.messagesEl.style.maxHeight = "50vh";
    this.messagesEl.style.overflowY = "auto";
    this.messagesEl.style.marginBottom = "12px";
    this.messagesEl.style.display = "flex";
    this.messagesEl.style.flexDirection = "column";
    this.messagesEl.style.gap = "10px";
    const inputRow = contentEl.createDiv({ cls: "sb-chat-input-row" });
    inputRow.style.display = "flex";
    inputRow.style.gap = "8px";
    this.inputEl = inputRow.createEl("input", {
      type: "text",
      placeholder: "Ask a question about your notes..."
    });
    this.inputEl.style.flexGrow = "1";
    this.inputEl.focus();
    this.sendButton = inputRow.createEl("button", { text: "Send" });
    const doSend = () => this.handleSend();
    this.sendButton.addEventListener("click", doSend);
    this.inputEl.addEventListener("keydown", (evt) => {
      if (evt.key === "Enter") doSend();
    });
  }
  addBubble(role, text) {
    const bubble = this.messagesEl.createDiv({ cls: `sb-chat-bubble sb-chat-${role}` });
    bubble.style.padding = "8px 12px";
    bubble.style.borderRadius = "8px";
    bubble.style.whiteSpace = "pre-wrap";
    if (role === "user") {
      bubble.style.alignSelf = "flex-end";
      bubble.style.background = "var(--interactive-accent)";
      bubble.style.color = "var(--text-on-accent)";
    } else if (role === "assistant") {
      bubble.style.alignSelf = "flex-start";
      bubble.style.background = "var(--background-secondary)";
    } else {
      bubble.style.alignSelf = "center";
      bubble.style.opacity = "0.7";
      bubble.style.fontStyle = "italic";
    }
    bubble.setText(text);
    this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    return bubble;
  }
  addSources(sources) {
    const sourcesEl = this.messagesEl.createDiv({ cls: "sb-chat-sources" });
    sourcesEl.style.alignSelf = "flex-start";
    sourcesEl.style.fontSize = "0.85em";
    sourcesEl.style.opacity = "0.75";
    sourcesEl.createSpan({ text: "Sources: " });
    sources.forEach((path, i) => {
      const link = sourcesEl.createEl("a", { text: path });
      link.style.cursor = "pointer";
      link.style.textDecoration = "underline";
      link.addEventListener("click", () => this.openSource(path));
      if (i < sources.length - 1) sourcesEl.createSpan({ text: ", " });
    });
    this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
  }
  async openSource(path) {
    const file = this.app.vault.getAbstractFileByPath(path);
    if (file instanceof import_obsidian.TFile) {
      await this.app.workspace.getLeaf(false).openFile(file);
    } else {
      new import_obsidian.Notice("Note not found in vault: " + path);
    }
  }
  async handleSend() {
    const question = this.inputEl.value.trim();
    if (!question) return;
    this.inputEl.value = "";
    this.inputEl.disabled = true;
    this.sendButton.disabled = true;
    this.addBubble("user", question);
    const statusBubble = this.addBubble("status", "Searching your notes...");
    try {
      const response = await runChat(question, this.history);
      statusBubble.remove();
      this.addBubble("assistant", response.answer ?? "");
      if (response.sources?.length) this.addSources(response.sources);
      this.history.push({ q: question, a: response.answer ?? "" });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      statusBubble.remove();
      this.addBubble("status", `Error: ${message}`);
      new import_obsidian.Notice("Second Brain Chat: " + message);
    } finally {
      this.inputEl.disabled = false;
      this.sendButton.disabled = false;
      this.inputEl.focus();
    }
  }
  onClose() {
    this.contentEl.empty();
  }
};
var SecondBrainSearchModal = class extends import_obsidian.Modal {
  constructor(app) {
    super(app);
  }
  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("h2", { text: "Search your Second Brain" });
    const searchRow = contentEl.createDiv({ cls: "sb-search-row" });
    searchRow.style.display = "flex";
    searchRow.style.gap = "8px";
    this.inputEl = searchRow.createEl("input", {
      type: "text",
      placeholder: "What are you looking for?"
    });
    this.inputEl.style.flexGrow = "1";
    this.inputEl.focus();
    const button = searchRow.createEl("button", { text: "Search" });
    this.resultsEl = contentEl.createDiv({ cls: "sb-search-results" });
    this.resultsEl.style.marginTop = "16px";
    const doSearch = () => this.handleSearch(this.inputEl.value.trim());
    button.addEventListener("click", doSearch);
    this.inputEl.addEventListener("keydown", (evt) => {
      if (evt.key === "Enter") doSearch();
    });
  }
  async handleSearch(query) {
    if (!query) return;
    this.resultsEl.empty();
    this.resultsEl.createEl("p", { text: "Searching...", cls: "sb-search-status" });
    try {
      const results = await runSearch(query);
      this.renderResults(results);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.resultsEl.empty();
      this.resultsEl.createEl("p", { text: `Error: ${message}` });
      new import_obsidian.Notice("Second Brain Search: " + message);
    }
  }
  renderResults(results) {
    this.resultsEl.empty();
    if (results.length === 0) {
      this.resultsEl.createEl("p", { text: "No results found." });
      return;
    }
    for (const result of results) {
      const item = this.resultsEl.createDiv({ cls: "sb-search-item" });
      item.style.padding = "8px 0";
      item.style.borderBottom = "1px solid var(--background-modifier-border)";
      item.style.cursor = "pointer";
      item.createEl("div", { text: result.path, cls: "sb-search-item-title" }).style.fontWeight = "600";
      item.createEl("div", {
        text: result.snippet.replace(/\s+/g, " ").slice(0, 220),
        cls: "sb-search-item-snippet"
      }).style.opacity = "0.75";
      item.addEventListener("click", () => this.openResult(result.path));
    }
  }
  async openResult(path) {
    const file = this.app.vault.getAbstractFileByPath(path);
    if (file instanceof import_obsidian.TFile) {
      await this.app.workspace.getLeaf(false).openFile(file);
      this.close();
    } else {
      new import_obsidian.Notice("Note not found in vault: " + path);
    }
  }
  onClose() {
    this.contentEl.empty();
  }
};
var SecondBrainSearchPlugin = class extends import_obsidian.Plugin {
  async onload() {
    this.addRibbonIcon("search", "Search your Second Brain", () => {
      new SecondBrainSearchModal(this.app).open();
    });
    this.addCommand({
      id: "open-second-brain-search",
      name: "Search your Second Brain",
      callback: () => {
        new SecondBrainSearchModal(this.app).open();
      }
    });
    this.addRibbonIcon("message-circle", "Chat with your Second Brain", () => {
      new SecondBrainChatModal(this.app).open();
    });
    this.addCommand({
      id: "open-second-brain-chat",
      name: "Chat with your Second Brain",
      callback: () => {
        new SecondBrainChatModal(this.app).open();
      }
    });
  }
};
