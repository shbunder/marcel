//! Persistent CLI state — tracks last conversation ID per user.
//!
//! State file: `~/.marcel/cli_state.json`

use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct CliState {
    /// Last conversation ID per user slug.
    pub last_conversation: HashMap<String, String>,
}

fn state_path() -> PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".marcel")
        .join("cli_state.json")
}

pub fn load() -> CliState {
    let path = state_path();
    match fs::read_to_string(&path) {
        Ok(contents) => serde_json::from_str(&contents).unwrap_or_default(),
        Err(_) => CliState::default(),
    }
}

pub fn save(state: &CliState) {
    let path = state_path();
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(json) = serde_json::to_string_pretty(state) {
        let _ = fs::write(path, json);
    }
}

pub fn set_last_conversation(user: &str, conversation_id: &str) {
    let mut state = load();
    state
        .last_conversation
        .insert(user.to_string(), conversation_id.to_string());
    save(&state);
}

pub fn get_last_conversation(user: &str) -> Option<String> {
    let state = load();
    state.last_conversation.get(user).cloned()
}
