use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use tokio::sync::mpsc;
use tokio_tungstenite::{connect_async, tungstenite::Message};

#[derive(Debug, Clone, Serialize)]
struct ChatRequest {
    text: String,
    user: String,
    conversation: Option<String>,
    model: Option<String>,
    #[serde(skip_serializing_if = "String::is_empty")]
    token: String,
    /// Current working directory of the CLI process — used by admin users so
    /// Marcel's bash/file tools operate relative to where the CLI was invoked.
    #[serde(skip_serializing_if = "Option::is_none")]
    cwd: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[allow(dead_code)]
struct ChatResponse {
    #[serde(rename = "type")]
    msg_type: String,
    #[serde(default)]
    text: Option<String>,
    #[serde(default)]
    conversation: Option<String>,
    #[serde(default)]
    message: Option<String>,
    #[serde(default)]
    cost_usd: Option<f64>,
    #[serde(default)]
    turns: Option<u32>,
    #[serde(default)]
    tool_call_id: Option<String>,
    #[serde(default)]
    tool_name: Option<String>,
    #[serde(default)]
    is_error: Option<bool>,
    #[serde(default)]
    summary: Option<String>,
}

/// Metadata returned with a completed turn.
#[derive(Debug, Clone, Default)]
pub struct TurnMeta {
    pub cost_usd: Option<f64>,
    pub turns: Option<u32>,
    pub conversation_id: Option<String>,
}

/// Events emitted by the WebSocket client.
#[derive(Debug, Clone)]
pub enum ChatEvent {
    Connected(TurnMeta),
    Token(String),
    Done(TurnMeta),
    Error(String),
    Disconnected,
    /// AG-UI: a tool invocation has started.
    ToolCallStart {
        tool_call_id: String,
        tool_name: String,
    },
    /// AG-UI: a tool invocation has completed.
    ToolCallEnd {
        tool_call_id: String,
    },
}

/// Async WebSocket chat client.
pub struct ChatClient {
    ws_url: String,
    user: String,
    model: String,
    token: String,
    conversation_id: Option<String>,
    /// Working directory captured at startup — sent with every message.
    cwd: Option<String>,
}

impl ChatClient {
    pub fn new(ws_url: &str, user: &str, model: &str, token: &str) -> Self {
        let cwd = std::env::current_dir()
            .ok()
            .and_then(|p| p.to_str().map(|s| s.to_string()));
        Self {
            ws_url: ws_url.into(),
            user: user.into(),
            model: model.into(),
            token: token.into(),
            conversation_id: None,
            cwd,
        }
    }

    /// Send a message and receive streaming events via the returned receiver.
    pub async fn send(&mut self, text: &str) -> Result<mpsc::Receiver<ChatEvent>, String> {
        let (event_tx, event_rx) = mpsc::channel(256);

        let url = &self.ws_url;
        let (ws_stream, _) = connect_async(url)
            .await
            .map_err(|e| format!("WebSocket connect failed: {e}"))?;

        let (mut write, mut read) = ws_stream.split();

        let req = ChatRequest {
            text: text.into(),
            user: self.user.clone(),
            conversation: self.conversation_id.clone(),
            model: Some(self.model.clone()),
            token: self.token.clone(),
            cwd: self.cwd.clone(),
        };

        let payload = serde_json::to_string(&req).unwrap();
        write
            .send(Message::Text(payload.into()))
            .await
            .map_err(|e| format!("WebSocket send failed: {e}"))?;

        tokio::spawn(async move {
            while let Some(msg) = read.next().await {
                match msg {
                    Ok(Message::Text(text)) => {
                        if let Ok(resp) = serde_json::from_str::<ChatResponse>(&text) {
                            match resp.msg_type.as_str() {
                                "started" => {
                                    let meta = TurnMeta {
                                        conversation_id: resp.conversation,
                                        ..Default::default()
                                    };
                                    let _ = event_tx.send(ChatEvent::Connected(meta)).await;
                                }
                                "token" => {
                                    if let Some(t) = resp.text {
                                        let _ = event_tx.send(ChatEvent::Token(t)).await;
                                    }
                                }
                                "done" => {
                                    let meta = TurnMeta {
                                        cost_usd: resp.cost_usd,
                                        turns: resp.turns,
                                        ..Default::default()
                                    };
                                    let _ = event_tx.send(ChatEvent::Done(meta)).await;
                                    break;
                                }
                                "error" => {
                                    let msg = resp.message.unwrap_or_else(|| "unknown".into());
                                    let _ = event_tx.send(ChatEvent::Error(msg)).await;
                                    break;
                                }
                                "tool_call_start" => {
                                    let _ = event_tx
                                        .send(ChatEvent::ToolCallStart {
                                            tool_call_id: resp.tool_call_id.unwrap_or_default(),
                                            tool_name: resp.tool_name.unwrap_or_default(),
                                        })
                                        .await;
                                }
                                "tool_call_end" => {
                                    let _ = event_tx
                                        .send(ChatEvent::ToolCallEnd {
                                            tool_call_id: resp.tool_call_id.unwrap_or_default(),
                                        })
                                        .await;
                                }
                                _ => {}
                            }
                        }
                    }
                    Ok(Message::Close(_)) | Err(_) => {
                        let _ = event_tx.send(ChatEvent::Disconnected).await;
                        break;
                    }
                    _ => {}
                }
            }
        });

        Ok(event_rx)
    }

    pub fn set_model(&mut self, model: &str) {
        self.model = model.into();
    }

    pub fn set_conversation_id(&mut self, id: &str) {
        self.conversation_id = Some(id.into());
    }

    pub fn clear_conversation(&mut self) {
        self.conversation_id = None;
    }
}

/// Check server health, returns version string or "offline".
pub async fn fetch_server_version(health_url: &str) -> String {
    #[derive(Deserialize)]
    struct Health {
        #[serde(default)]
        version: Option<String>,
    }

    match reqwest::get(health_url).await {
        Ok(resp) => match resp.json::<Health>().await {
            Ok(h) => h.version.unwrap_or_else(|| "?".into()),
            Err(_) => "offline".into(),
        },
        Err(_) => "offline".into(),
    }
}
