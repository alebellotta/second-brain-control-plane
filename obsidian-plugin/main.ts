import { App, Modal, Notice, Plugin, TFile } from "obsidian";
import { execFile } from "child_process";
import { homedir } from "os";
import { join } from "path";

const PYTHON_BIN = join(homedir(), ".second-brain", "venv", "bin", "python");
const SEARCH_SCRIPT = join(homedir(), ".second-brain", "search.py");
const CHAT_SCRIPT = join(homedir(), ".second-brain", "chat.py");
const N_RESULTS = 8;
const TIMEOUT_MS = 30_000;
const CHAT_TIMEOUT_MS = 200_000;

interface SearchResult {
	path: string;
	snippet: string;
	distance: number;
}

interface ChatTurn {
	q: string;
	a: string;
}

interface ChatResponse {
	answer?: string;
	sources?: string[];
	error?: string;
}

function runSearch(query: string): Promise<SearchResult[]> {
	return new Promise((resolve, reject) => {
		execFile(
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
					resolve(parsed as SearchResult[]);
				} catch (e) {
					reject(new Error("Invalid response from search.py: " + stdout));
				}
			}
		);
	});
}

function runChat(question: string, history: ChatTurn[]): Promise<ChatResponse> {
	return new Promise((resolve, reject) => {
		const child = execFile(
			PYTHON_BIN,
			[CHAT_SCRIPT, "--json"],
			{ timeout: CHAT_TIMEOUT_MS, maxBuffer: 10 * 1024 * 1024 },
			(error, stdout, stderr) => {
				if (error) {
					reject(new Error(stderr?.trim() || error.message));
					return;
				}
				try {
					const parsed = JSON.parse(stdout) as ChatResponse;
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

class SecondBrainChatModal extends Modal {
	private messagesEl: HTMLElement;
	private inputEl: HTMLInputElement;
	private sendButton: HTMLButtonElement;
	private history: ChatTurn[] = [];

	constructor(app: App) {
		super(app);
	}

	onOpen(): void {
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
			placeholder: "Ask a question about your notes...",
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

	addBubble(role: "user" | "assistant" | "status", text: string): HTMLElement {
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

	addSources(sources: string[]): void {
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

	async openSource(path: string): Promise<void> {
		const file = this.app.vault.getAbstractFileByPath(path);
		if (file instanceof TFile) {
			await this.app.workspace.getLeaf(false).openFile(file);
		} else {
			new Notice("Note not found in vault: " + path);
		}
	}

	async handleSend(): Promise<void> {
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
			new Notice("Second Brain Chat: " + message);
		} finally {
			this.inputEl.disabled = false;
			this.sendButton.disabled = false;
			this.inputEl.focus();
		}
	}

	onClose(): void {
		this.contentEl.empty();
	}
}

class SecondBrainSearchModal extends Modal {
	private resultsEl: HTMLElement;
	private inputEl: HTMLInputElement;

	constructor(app: App) {
		super(app);
	}

	onOpen(): void {
		const { contentEl } = this;
		contentEl.empty();
		contentEl.createEl("h2", { text: "Search your Second Brain" });

		const searchRow = contentEl.createDiv({ cls: "sb-search-row" });
		searchRow.style.display = "flex";
		searchRow.style.gap = "8px";

		this.inputEl = searchRow.createEl("input", {
			type: "text",
			placeholder: "What are you looking for?",
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

	async handleSearch(query: string): Promise<void> {
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
			new Notice("Second Brain Search: " + message);
		}
	}

	renderResults(results: SearchResult[]): void {
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

			item.createEl("div", { text: result.path, cls: "sb-search-item-title" }).style.fontWeight =
				"600";
			item.createEl("div", {
				text: result.snippet.replace(/\s+/g, " ").slice(0, 220),
				cls: "sb-search-item-snippet",
			}).style.opacity = "0.75";

			item.addEventListener("click", () => this.openResult(result.path));
		}
	}

	async openResult(path: string): Promise<void> {
		const file = this.app.vault.getAbstractFileByPath(path);
		if (file instanceof TFile) {
			await this.app.workspace.getLeaf(false).openFile(file);
			this.close();
		} else {
			new Notice("Note not found in vault: " + path);
		}
	}

	onClose(): void {
		this.contentEl.empty();
	}
}

export default class SecondBrainSearchPlugin extends Plugin {
	async onload(): Promise<void> {
		this.addRibbonIcon("search", "Search your Second Brain", () => {
			new SecondBrainSearchModal(this.app).open();
		});

		this.addCommand({
			id: "open-second-brain-search",
			name: "Search your Second Brain",
			callback: () => {
				new SecondBrainSearchModal(this.app).open();
			},
		});

		this.addRibbonIcon("message-circle", "Chat with your Second Brain", () => {
			new SecondBrainChatModal(this.app).open();
		});

		this.addCommand({
			id: "open-second-brain-chat",
			name: "Chat with your Second Brain",
			callback: () => {
				new SecondBrainChatModal(this.app).open();
			},
		});
	}
}
