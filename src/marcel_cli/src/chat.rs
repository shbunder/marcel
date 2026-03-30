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
}

#[derive(Debug, Clone, Deserialize)]
struct ChatResponse {
    #[serde(rename = "type")]
    msg_type: String,
    #[serde(default)]
    text: Option<String>,
    #[allow(dead_code)] // used for conversation tracking
    #[serde(default)]
    conversation: Option<String>,
    #[serde(default)]
    message: Option<String>,
}

/// Events emitted by the WebSocket client.
#[derive(Debug, Clone)]
pub enum ChatEvent {
    Connected,
    Token(String),
    Done,
    Error(String),
    Disconnected,
}

/// Async WebSocket chat client.
pub struct ChatClient {
    ws_url: String,
    user: String,
    model: String,
    conversation_id: Option<String>,
}

impl ChatClient {
    pub fn new(ws_url: &str, user: &str, model: &str) -> Self {
        Self {
            ws_url: ws_url.into(),
            user: user.into(),
            model: model.into(),
            conversation_id: None,
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
                                    let _ = event_tx.send(ChatEvent::Connected).await;
                                }
                                "token" => {
                                    if let Some(t) = resp.text {
                                        let _ = event_tx.send(ChatEvent::Token(t)).await;
                                    }
                                }
                                "done" => {
                                    let _ = event_tx.send(ChatEvent::Done).await;
                                    break;
                                }
                                "error" => {
                                    let msg = resp.message.unwrap_or_else(|| "unknown".into());
                                    let _ = event_tx.send(ChatEvent::Error(msg)).await;
                                    break;
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
