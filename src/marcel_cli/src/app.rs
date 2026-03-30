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

const COMMANDS: &[(&str, &str)] = &[
    ("/clear", "Clear the chat history"),
    (
        "/compact",
        "Compact conversation context  [requires server]",
    ),
    ("/config", "Show or set config  (/config host <value>)"),
    ("/cost", "Show token usage and cost     [requires server]"),
    ("/help", "Show available commands"),
    ("/memory", "Show Marcel's memory          [requires server]"),
    ("/model", "Show or set the current model"),
    ("/reconnect", "Reconnect to the Marcel server"),
    ("/status", "Show connection and server status"),
    ("/exit", "Exit Marcel"),
    ("/quit", "Exit Marcel"),
];

pub async fn run(cfg: Config) -> io::Result<()> {
    let mut terminal = tui::init()?;

    // State
    let mut header = Header::new(&cfg.user, &cfg.model, &cfg.host, cfg.port);
    let mut chat_view = ChatView::new();
    let mut input = InputBox::new();
    let mut status = StatusBar::new(&cfg.model);
    let mut client = ChatClient::new(&cfg.ws_url(), &cfg.user, &cfg.model);

    // Check server health
    let version = chat::fetch_server_version(&cfg.health_url()).await;
    header.server_version = version.clone();
    header.connected = version != "offline";
    status.connected = header.connected;

    if !header.connected {
        chat_view.push_error("Could not connect to Marcel server.");
        chat_view.push_system("Start the server with: make serve");
    }

    // Channel for receiving streaming events
    let mut stream_rx: Option<mpsc::Receiver<ChatEvent>> = None;

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
                    Ok(ChatEvent::Done) => {
                        chat_view.finish_stream();
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
                    Ok(ChatEvent::Connected) => {}
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
        if event::poll(timeout)? {
            if let Event::Key(key) = event::read()? {
                match handle_key(
                    key,
                    &mut input,
                    &mut chat_view,
                    &mut header,
                    &mut status,
                    &mut client,
                    &mut stream_rx,
                    &cfg,
                )
                .await
                {
                    Action::Continue => {}
                    Action::Quit => break,
                }
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

async fn handle_key(
    key: KeyEvent,
    input: &mut InputBox,
    chat: &mut ChatView,
    header: &mut Header,
    status: &mut StatusBar,
    client: &mut ChatClient,
    stream_rx: &mut Option<mpsc::Receiver<ChatEvent>>,
    cfg: &Config,
) -> Action {
    match (key.code, key.modifiers) {
        // Quit
        (KeyCode::Char('c'), KeyModifiers::CONTROL)
        | (KeyCode::Char('d'), KeyModifiers::CONTROL) => return Action::Quit,

        // Submit
        (KeyCode::Enter, _) => {
            let text = input.take();
            let text = text.trim().to_string();
            if text.is_empty() {
                return Action::Continue;
            }

            if text.starts_with('/') {
                return handle_command(&text, chat, header, status, client, stream_rx, cfg).await;
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

    Action::Continue
}

async fn handle_command(
    text: &str,
    chat: &mut ChatView,
    header: &mut Header,
    status: &mut StatusBar,
    client: &mut ChatClient,
    stream_rx: &mut Option<mpsc::Receiver<ChatEvent>>,
    cfg: &Config,
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
            chat.push_system(&format!("server:  {}:{}", cfg.host, cfg.port));
            chat.push_system(&format!("status:  {conn}"));
            chat.push_system("cli:     v0.1.0");
            chat.push_system(&format!("backend: v{}", header.server_version));
            chat.push_system(&format!("model:   {}", header.model));
            chat.push_system(&format!("user:    {}", cfg.user));
        }

        "/reconnect" => {
            chat.push_system("Reconnecting…");
            let version = chat::fetch_server_version(&cfg.health_url()).await;
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
                chat.push_system(&format!("host:  {}:{}", cfg.host, cfg.port));
                chat.push_system(&format!("user:  {}", cfg.user));
                chat.push_system(&format!("model: {}", header.model));
            } else {
                chat.push_system("Config editing not yet supported in Rust CLI. Edit ~/.marcel/config.toml directly.");
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
