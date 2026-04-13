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
    #[serde(default = "default_dev_port")]
    pub dev_port: u16,
}

fn default_host() -> String {
    "localhost".into()
}
fn default_port() -> u16 {
    7420
}
fn default_dev_port() -> u16 {
    7421
}
fn default_user() -> String {
    String::new()
}
fn default_model() -> String {
    "anthropic:claude-sonnet-4-6".into()
}

impl Default for Config {
    fn default() -> Self {
        Self {
            host: default_host(),
            port: default_port(),
            user: default_user(),
            token: String::new(),
            model: default_model(),
            dev_port: default_dev_port(),
        }
    }
}

impl Config {
    /// Return effective port, using dev_port when in dev mode.
    pub fn effective_port(&self, dev_mode: bool) -> u16 {
        if dev_mode { self.dev_port } else { self.port }
    }

    pub fn ws_url(&self, dev_mode: bool, _use_v2: bool) -> String {
        let path = "/ws/chat";
        format!("ws://{}:{}{path}", self.host, self.effective_port(dev_mode))
    }

    pub fn health_url(&self, dev_mode: bool) -> String {
        format!(
            "http://{}:{}/health",
            self.host,
            self.effective_port(dev_mode)
        )
    }

    pub fn base_url(&self, dev_mode: bool) -> String {
        format!("http://{}:{}", self.host, self.effective_port(dev_mode))
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
