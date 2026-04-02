use std::io;

use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyModifiers};
use ratatui::layout::Rect;
use tokio::sync::mpsc;

use crate::chat::{self, ChatClient, ChatEvent};
use crate::config::Config;
use crate::header::Header;
use crate::render::{FlexLayout, Renderable};
use crate::tui;
use crate::ui::{ChatView, InputBox, StatusBar};
use crate::Cli;

const COMMANDS: &[(&str, &str)] = &[
    ("/clear", "Clear the chat history"),
    (
        "/compact",
        "Compact conversation context  [requires server]",
    ),
    ("/config", "Show or set config  (/config host <value>)"),
    ("/cost", "Show token usage and cost     [requires server]"),
    ("/export", "Export conversation to file   (/export [path])"),
    ("/help", "Show available commands"),
    ("/memory", "Show Marcel's memory          [requires server]"),
    ("/model", "Show or set the current model"),
    ("/new", "Start a new conversation"),
    ("/reconnect", "Reconnect to the Marcel server"),
    ("/resume", "Resume a conversation        (/resume <id>)"),
    ("/sessions", "List recent conversations    [requires server]"),
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

async fn fetch_conversations(
    cfg: &Config,
    dev_mode: bool,
) -> Result<Vec<ConversationInfo>, String> {
    let base = cfg.health_url(dev_mode).replace("/health", "");
    let url = format!("{base}/conversations?user={}&limit=20", cfg.user);

    let mut req = reqwest::Client::new().get(&url);
    if !cfg.token.is_empty() {
        req = req.header("Authorization", format!("Bearer {}", cfg.token));
    }

    let resp = req.send().await.map_err(|e| e.to_string())?;
    let body: ConversationListResponse = resp.json().await.map_err(|e| e.to_string())?;
    Ok(body.conversations)
}

pub async fn run(cfg: Config, cli: &Cli) -> io::Result<()> {
    let dev_mode = cli.dev;
    let mut terminal = tui::init()?;

    // State
    let port = cfg.effective_port(dev_mode);
    let mut header = Header::new(&cfg.user, &cfg.model, &cfg.host, port);
    let mut chat_view = ChatView::new();
    let mut input = InputBox::new();
    let mut status = StatusBar::new(&cfg.model);
    let mut client = ChatClient::new(&cfg.ws_url(dev_mode), &cfg.user, &cfg.model, &cfg.token);

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

    // Resume a previous conversation if requested
    if cli.r#continue {
        if let Some(id) = crate::state::get_last_conversation(&cfg.user) {
            client.set_conversation_id(&id);
            chat_view.push_system(&format!("Continuing conversation: {id}"));
        } else {
            chat_view.push_system("No previous conversation to continue.");
        }
    } else if let Some(ref resume) = cli.resume {
        match resume {
            Some(id) => {
                client.set_conversation_id(id);
                chat_view.push_system(&format!("Resumed conversation: {id}"));
            }
            None => {
                // No ID given — fetch list and show picker
                if header.connected {
                    match fetch_conversations(&cfg, dev_mode).await {
                        Ok(convs) if convs.is_empty() => {
                            chat_view.push_system("No conversations found.");
                        }
                        Ok(convs) => {
                            chat_view.push_system("Recent conversations (use /resume <id> to pick):");
                            for c in &convs {
                                chat_view.push_system(&format!("  {}  ({})", c.id, c.channel));
                            }
                            // Auto-resume the most recent
                            if let Some(first) = convs.first() {
                                client.set_conversation_id(&first.id);
                                chat_view.push_system(&format!("Resumed: {}", first.id));
                            }
                        }
                        Err(e) => {
                            chat_view.push_error(&format!("Failed to list conversations: {e}"));
                        }
                    }
                }
            }
        }
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

    loop {
        // ── draw ───────────────────────────────────────────────────────
        terminal.draw(|frame| {
            let area = frame.area();

            let mut layout = FlexLayout::new();
            layout.push(0, &header);
            layout.push(1, &chat_view);
            layout.push(0, &input);
            layout.push(0, &status);

            layout.render(area, frame.buffer_mut());

            // Show cursor inside input box
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
        })?;

        // ── poll for events (both keyboard and streaming) ──────────────
        let timeout = std::time::Duration::from_millis(50);

        // Drain any pending stream events
        if let Some(rx) = &mut stream_rx {
            loop {
                match rx.try_recv() {
                    Ok(ChatEvent::Token(t)) => {
                        chat_view.push_token(&t);
                        let h = terminal.size()?.height;
                        chat_view.scroll_to_bottom(h.saturating_sub(
                            header.desired_height(80)
                                + input.desired_height(80)
                                + status.desired_height(80),
                        ));
                    }
                    Ok(ChatEvent::Done(meta)) => {
                        chat_view.finish_stream();
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
                    Err(mpsc::error::TryRecvError::Empty) => break,
                    Err(mpsc::error::TryRecvError::Disconnected) => {
                        chat_view.finish_stream();
                        stream_rx = None;
                        break;
                    }
                }
            }
        }

        // Poll keyboard
        if event::poll(timeout)?
            && let Event::Key(key) = event::read()?
        {
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
                Action::Quit => break,
            }
        }
    }

    tui::restore(&mut terminal)?;
    Ok(())
}

enum Action {
    Continue,
    Quit,
}

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
    // Track whether input text changes (to refresh suggestions)
    let text_before = input.text.clone();

    match (key.code, key.modifiers) {
        // Quit
        (KeyCode::Char('c'), KeyModifiers::CONTROL)
        | (KeyCode::Char('d'), KeyModifiers::CONTROL) => return Action::Quit,

        // Escape: dismiss suggestions if visible
        (KeyCode::Esc, _) => {
            input.dismiss_suggestions();
        }

        // Submit
        (KeyCode::Enter, _) => {
            // If suggestions are visible, accept the selected one instead of submitting
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

            // Send message
            if !header.connected {
                chat.push_system("Not connected. Try /reconnect or make serve");
                return Action::Continue;
            }

            chat.push_user(&text);
            match client.send(&text).await {
                Ok(rx) => {
                    *stream_rx = Some(rx);
                }
                Err(e) => {
                    chat.push_error(&format!("Send failed: {e}"));
                }
            }
        }

        // Tab: accept the selected suggestion
        (KeyCode::Tab, _) => {
            input.accept_suggestion();
        }

        // Up/Down: navigate suggestions when visible, otherwise cycle history
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

        // Ctrl+G: open $EDITOR for multi-line input
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

        // Scroll chat
        (KeyCode::PageUp, _) => {
            chat.scroll_offset = chat.scroll_offset.saturating_sub(10);
        }
        (KeyCode::PageDown, _) => {
            chat.scroll_offset = chat.scroll_offset.saturating_add(10);
        }

        _ => {}
    }

    // Refresh suggestions whenever the input text changes
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
    // Write existing empty file so editor opens cleanly
    let _ = std::fs::write(&tmp, "");

    // Temporarily restore terminal for the editor
    crossterm::terminal::disable_raw_mode().ok();
    crossterm::execute!(io::stdout(), crossterm::terminal::LeaveAlternateScreen).ok();

    let status = std::process::Command::new(&editor)
        .arg(&tmp)
        .status()
        .ok();

    // Re-enter TUI mode
    crossterm::terminal::enable_raw_mode().ok();
    crossterm::execute!(io::stdout(), crossterm::terminal::EnterAlternateScreen).ok();

    if status.is_some_and(|s| s.success()) {
        std::fs::read_to_string(&tmp).ok()
    } else {
        None
    }
}

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

        "/clear" => {
            chat.clear();
        }

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
                chat.push_system("Config editing not yet supported in Rust CLI. Edit ~/.marcel/config.toml directly.");
            }
        }

        "/new" => {
            client.clear_conversation();
            status.session_cost = 0.0;
            status.turn_count = 0;
            chat.clear();
            chat.push_system("New conversation started.");
        }

        "/sessions" => {
            if !header.connected {
                chat.push_error("/sessions requires a running server.");
            } else {
                match fetch_conversations(cfg, dev_mode).await {
                    Ok(convs) if convs.is_empty() => {
                        chat.push_system("No conversations found.");
                    }
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
