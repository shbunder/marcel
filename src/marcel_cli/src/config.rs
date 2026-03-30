use dirs::home_dir;
use serde::Deserialize;
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    #[serde(default = "default_host")]
    pub host: String,
    #[serde(default = "default_port")]
    pub port: u16,
    #[serde(default = "default_user")]
    pub user: String,
    #[allow(dead_code)] // used by future auth
    #[serde(default)]
    pub token: String,
    #[serde(default = "default_model")]
    pub model: String,
}

fn default_host() -> String {
    "localhost".into()
}
fn default_port() -> u16 {
    7420
}
fn default_user() -> String {
    "shaun".into()
}
fn default_model() -> String {
    "claude-sonnet-4-6".into()
}

impl Default for Config {
    fn default() -> Self {
        Self {
            host: default_host(),
            port: default_port(),
            user: default_user(),
            token: String::new(),
            model: default_model(),
        }
    }
}

impl Config {
    pub fn ws_url(&self) -> String {
        format!("ws://{}:{}/ws/chat", self.host, self.port)
    }

    pub fn health_url(&self) -> String {
        format!("http://{}:{}/health", self.host, self.port)
    }
}

fn config_path() -> PathBuf {
    home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".marcel")
        .join("config.toml")
}

pub fn load() -> Config {
    let path = config_path();
    if let Ok(contents) = fs::read_to_string(&path) {
        toml::from_str(&contents).unwrap_or_default()
    } else {
        Config::default()
    }
}
