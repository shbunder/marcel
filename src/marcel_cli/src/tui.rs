use std::io::{self, Stdout};

use crossterm::{
    event::{DisableBracketedPaste, EnableBracketedPaste},
    execute,
    terminal::{self, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{backend::CrosstermBackend, Terminal};

pub type Term = Terminal<CrosstermBackend<Stdout>>;

pub fn init() -> io::Result<Term> {
    terminal::enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableBracketedPaste)?;
    let backend = CrosstermBackend::new(stdout);
    Terminal::new(backend)
}

pub fn restore(terminal: &mut Term) -> io::Result<()> {
    terminal::disable_raw_mode()?;
    execute!(
        terminal.backend_mut(),
        LeaveAlternateScreen,
        DisableBracketedPaste,
    )?;
    terminal.show_cursor()?;
    Ok(())
}
