use std::io;

use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEventKind};
use ratatui::layout::Rect;
use ratatui::style::Color;
use tokio::sync::mpsc;

use crate::Cli;
use crate::chat::{self, ChatClient, ChatEvent};
use crate::config::Config;
use crate::header::{Header, WELCOMES};
use crate::render::{FlexLayout, Renderable};
use crate::tui;
use crate::ui::{ChatView, InputBox, StatusBar};

const COMMANDS: &[(&str, &str)] = &[
    ("/clear", "Clear the chat display"),
    (
        "/compact",
        "Compact conversation context  [requires server]",
    ),
    ("/config", "Show or set config  (/config host <value>)"),
    ("/cost", "Show token usage and cost     [requires server]"),
    ("/export", "Export conversation to file   (/export [path])"),
    ("/forget", "Compress recent conversation  [requires server]"),
    ("/help", "Show available commands"),
    ("/memory", "Show Marcel's memory          [requires server]"),
    ("/model", "Show or set the current model"),
    ("/new", "Alias for /forget"),
    ("/reconnect", "Reconnect to the Marcel server"),
    ("/resume", "Resume a conversation        (/resume <id>)"),
    (
        "/sessions",
        "List recent conversations    [requires server]",
    ),
    ("/status", "Show connection and server status"),
    ("/exit", "Exit Marcel"),
    ("/quit", "Exit Marcel"),
];

#[derive(Debug, serde::Deserialize)]
struct ConversationInfo {
    id: String,
    channel: String,
}

#[derive(Debug, serde::Deserialize)]
struct ConversationListResponse {
    conversations: Vec<ConversationInfo>,
}

#[derive(Debug, serde::Deserialize)]
struct HistoryMessageEntry {
    role: String,
    text: String,
}

#[derive(Debug, serde::Deserialize)]
struct HistoryResponse {
    summary: Option<String>,
    messages: Vec<HistoryMessageEntry>,
}

async fn fetch_conversations(
    cfg: &Config,
    dev_mode: bool,
) -> Result<Vec<ConversationInfo>, String> {
    let base = cfg.base_url(dev_mode);
    let url = format!("{base}/conversations?user={}&limit=20", cfg.user);

    let mut req = reqwest::Client::new().get(&url);
    if !cfg.token.is_empty() {
        req = req.header("Authorization", format!("Bearer {}", cfg.token));
    }

    let resp = req.send().await.map_err(|e| e.to_string())?;
    let body: ConversationListResponse = resp.json().await.map_err(|e| e.to_string())?;
    Ok(body.conversations)
}

#[derive(Debug, serde::Deserialize)]
struct ForgetResponse {
    #[allow(dead_code)]
    success: bool,
    message: String,
}

/// Trigger conversation compaction via the server.
async fn forget_conversation(cfg: &Config, dev_mode: bool) -> Result<String, String> {
    let base = cfg.base_url(dev_mode);
    let url = format!("{base}/api/forget?user={}&channel=cli", cfg.user);

    let mut req = reqwest::Client::new().post(&url);
    if !cfg.token.is_empty() {
        req = req.header("Authorization", format!("Bearer {}", cfg.token));
    }

    let resp = req.send().await.map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("HTTP {}", resp.status()));
    }
    let body: ForgetResponse = resp.json().await.map_err(|e| e.to_string())?;
    Ok(body.message)
}

/// Fetch conversation history from the server to display on CLI startup.
async fn fetch_history(
    cfg: &Config,
    dev_mode: bool,
    channel: &str,
) -> Result<HistoryResponse, String> {
    let base = cfg.base_url(dev_mode);
    let url = format!("{base}/api/history?user={}&channel={channel}", cfg.user);

    let mut req = reqwest::Client::new().get(&url);
    if !cfg.token.is_empty() {
        req = req.header("Authorization", format!("Bearer {}", cfg.token));
    }

    let resp = req.send().await.map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("HTTP {}", resp.status()));
    }
    let body: HistoryResponse = resp.json().await.map_err(|e| e.to_string())?;
    Ok(body)
}

pub async fn run(cfg: Config, cli: &Cli) -> io::Result<()> {
    let dev_mode = cli.dev;
    let use_v2 = true; // Always use v2 harness; --v2 flag retained for compatibility
    let mut terminal = tui::init()?;

    // State
    let port = cfg.effective_port(dev_mode);
    let mut header = Header::new(&cfg.user, &cfg.model, &cfg.host, port);
    let mut chat_view = ChatView::new();
    let mut input = InputBox::new();
    let mut status = StatusBar::new(&cfg.model);
    let mut client = ChatClient::new(
        &cfg.ws_url(dev_mode, use_v2),
        &cfg.user,
        &cfg.model,
        &cfg.token,
    );

    if dev_mode {
        chat_view.push_system(&format!("DEV MODE — connecting to port {port}"));
    }

    // Check server health
    let version = chat::fetch_server_version(&cfg.health_url(dev_mode)).await;
    header.server_version = version.clone();
    header.connected = version != "offline";
    status.connected = header.connected;

    if !header.connected {
        chat_view.push_error("Could not connect to Marcel server.");
        chat_view.push_system("Start the server with: make serve");
    }

    // Channel for receiving streaming events
    let mut stream_rx: Option<mpsc::Receiver<ChatEvent>> = None;

    // Load conversation history on startup — like opening a chat app.
    // If history is available, show it. Otherwise, show a welcome message.
    let mut loaded_history = false;
    if header.connected {
        match fetch_history(&cfg, dev_mode, "cli").await {
            Ok(history) => {
                let has_content = history.summary.is_some() || !history.messages.is_empty();
                if has_content {
                    if let Some(summary) = &history.summary {
                        chat_view.push_system(summary);
                    }
                    for msg in &history.messages {
                        match msg.role.as_str() {
                            "user" => chat_view.push_user(&msg.text),
                            "assistant" => chat_view.push_assistant(&msg.text),
                            _ => {}
                        }
                    }
                    chat_view.scroll_to_bottom();
                    loaded_history = true;
                }
            }
            Err(_) => {
                // Silent — just start fresh if history unavailable
            }
        }
    }

    // Show a welcome message only if no history was loaded
    if !loaded_history {
        use std::time::{SystemTime, UNIX_EPOCH};
        let idx = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as usize
            % WELCOMES.len();
        chat_view.push_assistant(WELCOMES[idx]);
    }

    // Legacy resume support (if explicitly requested)
    if let Some(ref resume) = cli.resume
        && let Some(id) = resume
    {
        client.set_conversation_id(id);
        chat_view.push_system(&format!("Resumed conversation: {id}"));
    }

    // If a prompt was given on the command line, send it immediately
    if let Some(prompt) = &cli.prompt {
        let prompt = prompt.trim();
        if !prompt.is_empty() && header.connected {
            chat_view.push_user(prompt);
            match client.send(prompt).await {
                Ok(rx) => stream_rx = Some(rx),
                Err(e) => chat_view.push_error(&format!("Send failed: {e}")),
            }
        }
    }

    // ── Mouse / selection state ────────────────────────────────────────
    // drag_start: screen position of left-button press
    let mut drag_start: Option<(u16, u16)> = None;
    // selection: (start, end) screen positions while dragging or after copy
    let mut selection: Option<((u16, u16), (u16, u16))> = None;
    // buf_snapshot: flat text snapshot of the last frame, indexed by row
    let mut buf_snapshot: Vec<String> = Vec::new();
    // copy_notif_at: when we last set the "copied" notification
    let mut copy_notif_at: Option<std::time::Instant> = None;

    loop {
        // ── expire copy notification ───────────────────────────────────
        if let Some(t) = copy_notif_at
            && t.elapsed() > std::time::Duration::from_millis(1500)
        {
            status.notification = None;
            copy_notif_at = None;
        }

        // ── update chat area dimensions ────────────────────────────────
        {
            let sz = terminal.size()?;
            chat_view.area_height = sz.height.saturating_sub(
                header.desired_height(sz.width)
                    + input.desired_height(sz.width)
                    + status.desired_height(sz.width),
            );
            chat_view.area_width = sz.width;
        }

        // ── auto-follow: keep pinned to bottom when following ──────────
        if chat_view.following {
            chat_view.scroll_to_bottom();
        }

        // ── draw ───────────────────────────────────────────────────────
        terminal.draw(|frame| {
            let area = frame.area();

            let mut layout = FlexLayout::new();
            layout.push(0, &header);
            layout.push(1, &chat_view);
            layout.push(0, &input);
            layout.push(0, &status);
            layout.render(area, frame.buffer_mut());

            // Cursor inside input box
            let input_y = area
                .height
                .saturating_sub(1 + input.desired_height(area.width));
            let input_area = Rect::new(
                area.x,
                input_y,
                area.width,
                input.desired_height(area.width),
            );
            if let Some((cx, cy)) = input.cursor_pos(input_area) {
                frame.set_cursor_position((cx, cy));
            }

            // Capture text snapshot for selection extraction (before overlay).
            // We only read symbols here; bg/fg are ignored so the snapshot is
            // pure text and unaffected by the highlight we apply next.
            {
                let buf = frame.buffer_mut();
                buf_snapshot = (0..area.height)
                    .map(|y| {
                        (0..area.width)
                            .map(|x| buf[(x, y)].symbol().to_string())
                            .collect()
                    })
                    .collect();
            }

            // Selection highlight overlay
            if let Some((start, end)) = &selection {
                let (r1, c1, r2, c2) = normalize_sel(*start, *end);
                let buf = frame.buffer_mut();
                for y in r1..=r2.min(area.height.saturating_sub(1)) {
                    let x_start = if y == r1 { c1 } else { 0 };
                    let x_end = if y == r2 {
                        c2
                    } else {
                        area.width.saturating_sub(1)
                    };
                    for x in x_start..=x_end.min(area.width.saturating_sub(1)) {
                        buf[(x, y)].set_bg(Color::Rgb(0x26, 0x4F, 0x78));
                    }
                }
            }
        })?;

        // ── drain streaming events ─────────────────────────────────────
        let timeout = std::time::Duration::from_millis(50);

        if let Some(rx) = &mut stream_rx {
            loop {
                match rx.try_recv() {
                    Ok(ChatEvent::Token(t)) => {
                        chat_view.push_token(&t);
                        if chat_view.following {
                            chat_view.scroll_to_bottom();
                        }
                    }
                    Ok(ChatEvent::Done(meta)) => {
                        chat_view.finish_stream();
                        if chat_view.following {
                            chat_view.scroll_to_bottom();
                        }
                        if let Some(cost) = meta.cost_usd {
                            status.session_cost += cost;
                        }
                        if let Some(turns) = meta.turns {
                            status.turn_count = turns;
                        }
                        stream_rx = None;
                        break;
                    }
                    Ok(ChatEvent::Error(e)) => {
                        chat_view.finish_stream();
                        chat_view.push_error(&format!("Server error: {e}"));
                        stream_rx = None;
                        break;
                    }
                    Ok(ChatEvent::Disconnected) => {
                        chat_view.finish_stream();
                        chat_view.push_error("Disconnected from server.");
                        header.connected = false;
                        status.connected = false;
                        stream_rx = None;
                        break;
                    }
                    Ok(ChatEvent::Connected(meta)) => {
                        if let Some(id) = &meta.conversation_id {
                            client.set_conversation_id(id);
                            crate::state::set_last_conversation(&cfg.user, id);
                        }
                    }
                    Ok(ChatEvent::ToolCallStart {
                        tool_call_id,
                        tool_name,
                    }) => {
                        chat_view.start_tool(tool_call_id, tool_name);
                        if chat_view.following {
                            chat_view.scroll_to_bottom();
                        }
                    }
                    Ok(ChatEvent::ToolCallEnd { tool_call_id }) => {
                        chat_view.end_tool(&tool_call_id);
                    }
                    Err(mpsc::error::TryRecvError::Empty) => break,
                    Err(mpsc::error::TryRecvError::Disconnected) => {
                        chat_view.finish_stream();
                        stream_rx = None;
                        break;
                    }
                }
            }
        }

        // ── drain keyboard + mouse events ──────────────────────────────
        // We wait up to `timeout` for the first event, then drain the rest
        // immediately so the queue never stalls the render loop.
        if event::poll(timeout)? {
            let mut quit = false;
            loop {
                match event::read()? {
                    Event::Key(key) => {
                        // Any key clears the selection highlight
                        selection = None;
                        match handle_key(
                            key,
                            &mut input,
                            &mut chat_view,
                            &mut header,
                            &mut status,
                            &mut client,
                            &mut stream_rx,
                            &cfg,
                            dev_mode,
                        )
                        .await
                        {
                            Action::Continue => {}
                            Action::Quit => {
                                quit = true;
                                break;
                            }
                            Action::ScrollToBottom => chat_view.scroll_to_bottom(),
                        }
                    }

                    Event::Mouse(mouse) => match mouse.kind {
                        // ── Scroll wheel ──────────────────────────────
                        MouseEventKind::ScrollUp => {
                            chat_view.scroll_up(3);
                        }
                        MouseEventKind::ScrollDown => {
                            chat_view.scroll_down(3);
                        }

                        // ── Drag-to-select ────────────────────────────
                        MouseEventKind::Down(MouseButton::Left) => {
                            selection = None;
                            drag_start = Some((mouse.column, mouse.row));
                        }
                        MouseEventKind::Drag(MouseButton::Left) => {
                            if let Some(start) = drag_start {
                                let end = (mouse.column, mouse.row);
                                if start != end {
                                    selection = Some((start, end));
                                }
                            }
                        }
                        MouseEventKind::Up(MouseButton::Left) => {
                            if let Some(start) = drag_start.take() {
                                let end = (mouse.column, mouse.row);
                                if start != end {
                                    let text = extract_selection(&buf_snapshot, start, end);
                                    if !text.trim().is_empty() {
                                        copy_to_clipboard(&text);
                                        status.notification = Some("copied".into());
                                        copy_notif_at = Some(std::time::Instant::now());
                                        // Keep highlight visible until next keypress
                                        selection = Some((start, end));
                                    } else {
                                        selection = None;
                                    }
                                } else {
                                    // Plain click — clear selection
                                    selection = None;
                                }
                            }
                        }

                        _ => {}
                    },

                    _ => {}
                }

                if !event::poll(std::time::Duration::ZERO)? {
                    break;
                }
            }
            if quit {
                break;
            }
        }
    }

    tui::restore(&mut terminal)?;
    Ok(())
}

// ── Selection helpers ─────────────────────────────────────────────────

/// Normalise a selection so (r1, c1) is always the earlier position.
fn normalize_sel(start: (u16, u16), end: (u16, u16)) -> (u16, u16, u16, u16) {
    let (c1, r1) = start;
    let (c2, r2) = end;
    if r1 < r2 || (r1 == r2 && c1 <= c2) {
        (r1, c1, r2, c2)
    } else {
        (r2, c2, r1, c1)
    }
}

/// Extract the text visible at the given screen coordinates from the buffer
/// snapshot.  Each row of the snapshot is the full width of the terminal;
/// we trim trailing spaces so copied text is clean.
fn extract_selection(buf: &[String], start: (u16, u16), end: (u16, u16)) -> String {
    let (r1, c1, r2, c2) = normalize_sel(start, end);
    let mut lines: Vec<String> = Vec::new();
    for r in r1..=r2 {
        if let Some(row) = buf.get(r as usize) {
            let chars: Vec<char> = row.chars().collect();
            let x_start = (if r == r1 { c1 as usize } else { 0 }).min(chars.len());
            let x_end = (if r == r2 {
                c2 as usize + 1
            } else {
                chars.len()
            })
            .min(chars.len());
            let line = chars[x_start..x_end]
                .iter()
                .collect::<String>()
                .trim_end()
                .to_string();
            lines.push(line);
        }
    }
    // Drop trailing blank lines
    while lines.last().is_some_and(|l| l.is_empty()) {
        lines.pop();
    }
    lines.join("\n")
}

/// Write `text` to the system clipboard using whichever helper is available.
/// Falls back to OSC 52 escape sequence for remote/SSH sessions (supported by
/// VS Code, iTerm2, Alacritty, kitty, WezTerm, and most modern terminals).
fn copy_to_clipboard(text: &str) {
    use std::io::Write;
    let commands: &[(&str, &[&str])] = &[
        ("wl-copy", &[]),                        // Wayland
        ("xclip", &["-selection", "clipboard"]), // X11
        ("xsel", &["--clipboard", "--input"]),   // X11 alt
        ("pbcopy", &[]),                         // macOS
    ];
    for (cmd, args) in commands {
        if let Ok(mut child) = std::process::Command::new(cmd)
            .args(*args)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
        {
            if let Some(stdin) = child.stdin.as_mut() {
                let _ = stdin.write_all(text.as_bytes());
            }
            let _ = child.wait();
            return;
        }
    }
    // Fallback: OSC 52 escape sequence — tells the terminal emulator to set
    // the system clipboard.  Works over SSH when the outer terminal supports it.
    use base64::Engine;
    let encoded = base64::engine::general_purpose::STANDARD.encode(text.as_bytes());
    let _ = std::io::stdout().write_all(format!("\x1b]52;c;{encoded}\x07").as_bytes());
    let _ = std::io::stdout().flush();
}

// ── Action enum ───────────────────────────────────────────────────────

enum Action {
    Continue,
    Quit,
    ScrollToBottom,
}

// ── Key handler ───────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
async fn handle_key(
    key: KeyEvent,
    input: &mut InputBox,
    chat: &mut ChatView,
    header: &mut Header,
    status: &mut StatusBar,
    client: &mut ChatClient,
    stream_rx: &mut Option<mpsc::Receiver<ChatEvent>>,
    cfg: &Config,
    dev_mode: bool,
) -> Action {
    let text_before = input.text.clone();

    match (key.code, key.modifiers) {
        // Quit
        (KeyCode::Char('c'), KeyModifiers::CONTROL)
        | (KeyCode::Char('d'), KeyModifiers::CONTROL) => return Action::Quit,

        // Escape: dismiss suggestions
        (KeyCode::Esc, _) => {
            input.dismiss_suggestions();
        }

        // Submit
        (KeyCode::Enter, _) => {
            if input.has_suggestions() {
                input.accept_suggestion();
                return Action::Continue;
            }

            let text = input.take();
            let text = text.trim().to_string();
            if text.is_empty() {
                return Action::Continue;
            }

            if text.starts_with('/') {
                return handle_command(
                    &text, chat, header, status, client, stream_rx, cfg, dev_mode,
                )
                .await;
            }

            if !header.connected {
                chat.push_system("Not connected. Try /reconnect or make serve");
                return Action::Continue;
            }

            chat.push_user(&text);
            chat.following = true;
            match client.send(&text).await {
                Ok(rx) => *stream_rx = Some(rx),
                Err(e) => chat.push_error(&format!("Send failed: {e}")),
            }
            return Action::ScrollToBottom;
        }

        // Tab: accept suggestion
        (KeyCode::Tab, _) => {
            input.accept_suggestion();
        }

        // Up/Down: suggestions or history
        (KeyCode::Up, _) => {
            if input.has_suggestions() {
                input.suggestion_prev();
            } else {
                input.history_prev();
            }
        }
        (KeyCode::Down, _) => {
            if input.has_suggestions() {
                input.suggestion_next();
            } else {
                input.history_next();
            }
        }

        // Ctrl+G: open $EDITOR
        (KeyCode::Char('g'), KeyModifiers::CONTROL) => {
            if let Some(text) = open_editor() {
                let text = text.trim().to_string();
                if !text.is_empty() {
                    input.text = text;
                    input.cursor = input.text.len();
                }
            }
        }

        // Readline shortcuts
        (KeyCode::Char('u'), KeyModifiers::CONTROL) => input.clear(),
        (KeyCode::Char('w'), KeyModifiers::CONTROL) => input.delete_word_back(),
        (KeyCode::Char('a'), KeyModifiers::CONTROL) => input.home(),
        (KeyCode::Char('e'), KeyModifiers::CONTROL) => input.end(),

        // Text editing
        (KeyCode::Char(c), KeyModifiers::NONE | KeyModifiers::SHIFT) => input.insert(c),
        (KeyCode::Backspace, _) => input.backspace(),
        (KeyCode::Delete, _) => input.delete(),
        (KeyCode::Left, _) => input.move_left(),
        (KeyCode::Right, _) => input.move_right(),
        (KeyCode::Home, _) => input.home(),
        (KeyCode::End, _) => input.end(),

        // Scroll chat (keyboard)
        (KeyCode::PageUp, _) => chat.scroll_up(10),
        (KeyCode::PageDown, _) => chat.scroll_down(10),

        _ => {}
    }

    if input.text != text_before {
        input.update_suggestions(COMMANDS);
    }

    Action::Continue
}

/// Open $EDITOR with a temp file, return its contents on save.
fn open_editor() -> Option<String> {
    let editor = std::env::var("EDITOR")
        .or_else(|_| std::env::var("VISUAL"))
        .unwrap_or_else(|_| "nano".into());

    let tmp = std::env::temp_dir().join("marcel-input.md");
    let _ = std::fs::write(&tmp, "");

    crossterm::terminal::disable_raw_mode().ok();
    crossterm::execute!(io::stdout(), crossterm::terminal::LeaveAlternateScreen).ok();

    let status = std::process::Command::new(&editor).arg(&tmp).status().ok();

    crossterm::terminal::enable_raw_mode().ok();
    crossterm::execute!(io::stdout(), crossterm::terminal::EnterAlternateScreen).ok();

    if status.is_some_and(|s| s.success()) {
        std::fs::read_to_string(&tmp).ok()
    } else {
        None
    }
}

// ── Command handler ───────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
async fn handle_command(
    text: &str,
    chat: &mut ChatView,
    header: &mut Header,
    status: &mut StatusBar,
    client: &mut ChatClient,
    stream_rx: &mut Option<mpsc::Receiver<ChatEvent>>,
    cfg: &Config,
    dev_mode: bool,
) -> Action {
    let parts: Vec<&str> = text.split_whitespace().collect();
    let cmd = parts[0].to_lowercase();

    match cmd.as_str() {
        "/exit" | "/quit" => return Action::Quit,

        "/clear" => chat.clear(),

        "/help" => {
            for (c, desc) in COMMANDS {
                chat.push_system(&format!("{c:<14}  {desc}"));
            }
        }

        "/model" => {
            if parts.len() > 1 {
                let model = parts[1];
                header.model = model.into();
                status.model = model.into();
                client.set_model(model);
                chat.push_system(&format!("model set to: {model}"));
            } else {
                chat.push_system(&format!("model: {}", header.model));
            }
        }

        "/status" => {
            let conn = if header.connected {
                "connected"
            } else {
                "offline"
            };
            let port = cfg.effective_port(dev_mode);
            let mode = if dev_mode { "dev" } else { "prod" };
            chat.push_system(&format!("server:  {}:{} ({})", cfg.host, port, mode));
            chat.push_system(&format!("status:  {conn}"));
            chat.push_system("cli:     v0.2.0");
            chat.push_system(&format!("backend: v{}", header.server_version));
            chat.push_system(&format!("model:   {}", header.model));
            chat.push_system(&format!("user:    {}", cfg.user));
        }

        "/reconnect" => {
            chat.push_system("Reconnecting…");
            let version = chat::fetch_server_version(&cfg.health_url(dev_mode)).await;
            header.server_version = version.clone();
            header.connected = version != "offline";
            status.connected = header.connected;
            if header.connected {
                chat.push_system("Reconnected.");
            } else {
                chat.push_error("Reconnect failed.");
            }
        }

        "/config" => {
            if parts.len() == 1 {
                chat.push_system(&format!(
                    "host:  {}:{}",
                    cfg.host,
                    cfg.effective_port(dev_mode)
                ));
                chat.push_system(&format!("user:  {}", cfg.user));
                chat.push_system(&format!("model: {}", header.model));
            } else {
                chat.push_system(
                    "Config editing not yet supported in Rust CLI. Edit ~/.marcel/config.toml directly.",
                );
            }
        }

        "/new" | "/forget" => {
            if !header.connected {
                chat.push_error(&format!("{cmd} requires a running server."));
            } else {
                chat.push_system("Compressing conversation...");
                match forget_conversation(cfg, dev_mode).await {
                    Ok(msg) => {
                        chat.clear();
                        chat.push_system(&msg);
                    }
                    Err(e) => chat.push_error(&format!("Failed: {e}")),
                }
            }
        }

        "/sessions" => {
            if !header.connected {
                chat.push_error("/sessions requires a running server.");
            } else {
                match fetch_conversations(cfg, dev_mode).await {
                    Ok(convs) if convs.is_empty() => chat.push_system("No conversations found."),
                    Ok(convs) => {
                        chat.push_system("Recent conversations:");
                        for c in &convs {
                            chat.push_system(&format!("  {}  ({})", c.id, c.channel));
                        }
                    }
                    Err(e) => chat.push_error(&format!("Failed: {e}")),
                }
            }
        }

        "/resume" => {
            if parts.len() > 1 {
                let id = parts[1];
                client.set_conversation_id(id);
                crate::state::set_last_conversation(&cfg.user, id);
                chat.push_system(&format!("Resumed conversation: {id}"));
            } else {
                chat.push_system("Usage: /resume <conversation-id>");
                chat.push_system("Use /sessions to list available conversations.");
            }
        }

        "/export" => {
            let path = if parts.len() > 1 {
                parts[1].to_string()
            } else {
                "marcel-export.md".to_string()
            };
            match export_conversation(chat, &path) {
                Ok(n) => chat.push_system(&format!("Exported {n} messages to {path}")),
                Err(e) => chat.push_error(&format!("Export failed: {e}")),
            }
        }

        "/compact" | "/cost" | "/memory" => {
            if !header.connected {
                chat.push_error(&format!("{cmd} requires a running server."));
            } else {
                chat.push_system(&format!("{cmd}: server command — sending to backend…"));
                match client.send(text).await {
                    Ok(rx) => *stream_rx = Some(rx),
                    Err(e) => chat.push_error(&format!("Send failed: {e}")),
                }
            }
        }

        _ => {
            chat.push_error(&format!("Unknown command: {cmd}. Type /help for a list."));
        }
    }

    Action::Continue
}

fn export_conversation(chat: &ChatView, path: &str) -> Result<usize, io::Error> {
    use crate::ui::MessageKind;
    use std::io::Write;

    let mut file = std::fs::File::create(path)?;
    writeln!(file, "# Marcel Conversation Export\n")?;

    let count = chat.messages.len();
    for msg in &chat.messages {
        match msg.kind {
            MessageKind::User => writeln!(file, "**User:** {}\n", msg.text)?,
            MessageKind::Assistant => writeln!(file, "**Marcel:** {}\n", msg.text)?,
            MessageKind::System => writeln!(file, "*{system}*\n", system = msg.text)?,
            MessageKind::Error => writeln!(file, "*Error: {err}*\n", err = msg.text)?,
        }
    }
    Ok(count)
}
