mod app;
mod chat;
mod config;
mod header;
mod render;
mod tui;
mod ui;

use std::io;

#[tokio::main]
async fn main() -> io::Result<()> {
    let cfg = config::load();
    let dev_mode = config::parse_dev_flag();
    app::run(cfg, dev_mode).await
}
