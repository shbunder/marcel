use std::io::{self, IsTerminal, Read, Write};

use crate::Cli;
use crate::chat::{ChatClient, ChatEvent};
use crate::config::Config;

/// Non-interactive print mode: send prompt, stream response to stdout, exit.
pub async fn run(cli: &Cli, cfg: &Config) -> io::Result<()> {
    let dev_mode = cli.dev;
    let use_v2 = true; // Always use v2 harness; --v2 flag retained for compatibility

    // Resolve prompt: CLI argument or stdin
    let prompt = match &cli.prompt {
        Some(p) => p.clone(),
        None => {
            if io::stdin().is_terminal() {
                eprintln!("error: -p/--print requires a prompt argument or piped stdin");
                std::process::exit(1);
            }
            let mut buf = String::new();
            io::stdin().read_to_string(&mut buf)?;
            buf
        }
    };

    let prompt = prompt.trim().to_string();
    if prompt.is_empty() {
        eprintln!("error: empty prompt");
        std::process::exit(1);
    }

    // Connect and send
    let mut client = ChatClient::new(
        &cfg.ws_url(dev_mode, use_v2),
        &cfg.user,
        &cfg.model,
        &cfg.token,
    );
    let rx = match client.send(&prompt).await {
        Ok(rx) => rx,
        Err(e) => {
            eprintln!("error: {e}");
            std::process::exit(1);
        }
    };

    match cli.output_format.as_str() {
        "text" => stream_text(rx).await,
        "json" => stream_collect_json(rx).await,
        "stream-json" => stream_ndjson(rx).await,
        other => {
            eprintln!("error: unknown output format '{other}' (expected: text, json, stream-json)");
            std::process::exit(1);
        }
    }
}

/// Stream tokens directly to stdout as plain text.
async fn stream_text(mut rx: tokio::sync::mpsc::Receiver<ChatEvent>) -> io::Result<()> {
    let mut stdout = io::stdout().lock();
    let mut cost = None;
    let mut turns = None;

    while let Some(event) = rx.recv().await {
        match event {
            ChatEvent::Token(t) => {
                write!(stdout, "{t}")?;
                stdout.flush()?;
            }
            ChatEvent::Done(meta) => {
                cost = meta.cost_usd;
                turns = meta.turns;
                break;
            }
            ChatEvent::Error(e) => {
                drop(stdout);
                eprintln!("\nerror: {e}");
                std::process::exit(1);
            }
            ChatEvent::Disconnected => {
                drop(stdout);
                eprintln!("\nerror: disconnected");
                std::process::exit(1);
            }
            ChatEvent::Connected(_)
            | ChatEvent::ToolCallStart { .. }
            | ChatEvent::ToolCallEnd { .. } => {}
        }
    }

    // Ensure trailing newline
    writeln!(stdout)?;

    // Print cost to stderr so it doesn't pollute piped output
    if let Some(c) = cost {
        let turn_info = turns.map(|t| format!(", {t} turns")).unwrap_or_default();
        eprintln!("[${c:.4}{turn_info}]");
    }

    Ok(())
}

/// Collect full response, output as single JSON object.
async fn stream_collect_json(mut rx: tokio::sync::mpsc::Receiver<ChatEvent>) -> io::Result<()> {
    let mut parts = Vec::new();
    let mut cost = None;
    let mut turns = None;

    while let Some(event) = rx.recv().await {
        match event {
            ChatEvent::Token(t) => parts.push(t),
            ChatEvent::Done(meta) => {
                cost = meta.cost_usd;
                turns = meta.turns;
                break;
            }
            ChatEvent::Error(e) => {
                let err = serde_json::json!({ "error": e });
                println!("{}", serde_json::to_string_pretty(&err).unwrap());
                std::process::exit(1);
            }
            ChatEvent::Disconnected => {
                let err = serde_json::json!({ "error": "disconnected" });
                println!("{}", serde_json::to_string_pretty(&err).unwrap());
                std::process::exit(1);
            }
            ChatEvent::Connected(_)
            | ChatEvent::ToolCallStart { .. }
            | ChatEvent::ToolCallEnd { .. } => {}
        }
    }

    let result = serde_json::json!({
        "response": parts.join(""),
        "cost_usd": cost,
        "turns": turns,
    });
    println!("{}", serde_json::to_string_pretty(&result).unwrap());

    Ok(())
}

/// Stream NDJSON: one JSON object per event.
async fn stream_ndjson(mut rx: tokio::sync::mpsc::Receiver<ChatEvent>) -> io::Result<()> {
    let mut stdout = io::stdout().lock();

    while let Some(event) = rx.recv().await {
        let obj = match event {
            ChatEvent::Token(t) => serde_json::json!({ "type": "token", "text": t }),
            ChatEvent::Done(meta) => {
                let obj = serde_json::json!({
                    "type": "done",
                    "cost_usd": meta.cost_usd,
                    "turns": meta.turns,
                });
                writeln!(stdout, "{}", serde_json::to_string(&obj).unwrap())?;
                break;
            }
            ChatEvent::Error(e) => serde_json::json!({ "type": "error", "message": e }),
            ChatEvent::Disconnected => {
                serde_json::json!({ "type": "error", "message": "disconnected" })
            }
            ChatEvent::Connected(meta) => {
                serde_json::json!({ "type": "started", "conversation": meta.conversation_id })
            }
            ChatEvent::ToolCallStart {
                tool_call_id,
                tool_name,
            } => serde_json::json!({
                "type": "tool_call_start",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
            }),
            ChatEvent::ToolCallEnd { tool_call_id } => {
                serde_json::json!({ "type": "tool_call_end", "tool_call_id": tool_call_id })
            }
        };
        writeln!(stdout, "{}", serde_json::to_string(&obj).unwrap())?;
        stdout.flush()?;
    }

    Ok(())
}
