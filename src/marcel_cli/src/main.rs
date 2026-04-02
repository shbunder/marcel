mod app;
mod chat;
mod config;
mod header;
mod print;
mod render;
mod state;
mod tui;
mod ui;

use std::io;

use clap::Parser;

#[derive(Parser)]
#[command(name = "marcel", about = "Marcel personal agent — terminal interface")]
pub struct Cli {
    /// Send this prompt (enters interactive mode unless -p is given)
    #[arg(value_name = "PROMPT")]
    prompt: Option<String>,

    /// Print response to stdout and exit (non-interactive)
    #[arg(short, long)]
    print: bool,

    /// Continue most recent conversation
    #[arg(short, long)]
    r#continue: bool,

    /// Resume a specific conversation (or show picker if no ID given)
    #[arg(short, long, value_name = "ID")]
    resume: Option<Option<String>>,

    /// Override model from config
    #[arg(short, long, value_name = "MODEL")]
    model: Option<String>,

    /// Override user from config
    #[arg(short, long, value_name = "USER")]
    user: Option<String>,

    /// Connect to dev server
    #[arg(long)]
    dev: bool,

    /// Output format for print mode: text (default), json, stream-json
    #[arg(long, value_name = "FMT", default_value = "text")]
    output_format: String,
}

#[tokio::main]
async fn main() -> io::Result<()> {
    let cli = Cli::parse();
    let mut cfg = config::load();

    // Apply CLI overrides
    if let Some(model) = &cli.model {
        cfg.model = model.clone();
    }
    if let Some(user) = &cli.user {
        cfg.user = user.clone();
    }

    if cli.print {
        print::run(&cli, &cfg).await
    } else {
        app::run(cfg, &cli).await
    }
}
