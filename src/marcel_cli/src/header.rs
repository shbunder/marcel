use ratatui::buffer::Buffer;
use ratatui::layout::{Alignment, Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph, Widget};

use crate::render::Renderable;

const ROSE: Color = Color::Rgb(0xcc, 0x5e, 0x76);
const TEAL: Color = Color::Rgb(0x2e, 0xc4, 0xb6);
const DIM: Color = Color::Rgb(0x55, 0x55, 0x55);
const MID: Color = Color::Rgb(0x88, 0x88, 0x88);
const RULE: Color = Color::Rgb(0x33, 0x33, 0x33);
const SEP: Color = Color::Rgb(0x44, 0x44, 0x44);

// Top 6 lines of mascot (head + neck) from design/mascot.txt.
//
// IMPORTANT: Do NOT use backslash-newline (`"\↵`) for the opening — Rust's
// string continuation strips all leading whitespace from the next line, which
// silently removes the 5-space indent on the ear line.  Start the content on
// the same line as the opening quote to preserve leading spaces.
const MASCOT: &str = "     ▖▄
▄▄▄▄▄▙▙
▛███ ██
▀▀▀▀▀██
     ▛█
     █▜";

const MASCOT_LINES: u16 = 6;

const WELCOMES: &[&str] = &[
    "Welcome back!",
    "Ready when you are.",
    "At your service.",
    "What can I do for you today?",
    "Let's get to work.",
    "How can I help?",
];

pub struct Header {
    pub user: String,
    pub model: String,
    pub host: String,
    pub port: u16,
    pub server_version: String,
    pub connected: bool,
    pub welcome: String,
}

impl Header {
    pub fn new(user: &str, model: &str, host: &str, port: u16) -> Self {
        use std::time::{SystemTime, UNIX_EPOCH};
        let idx = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as usize
            % WELCOMES.len();
        Self {
            user: user.into(),
            model: model.into(),
            host: host.into(),
            port,
            server_version: "offline".into(),
            connected: false,
            welcome: WELCOMES[idx].into(),
        }
    }

    fn col2_lines(&self) -> Vec<Line<'_>> {
        vec![
            Line::from(Span::styled(
                "Runtime",
                Style::default().fg(MID).add_modifier(Modifier::BOLD),
            )),
            Line::from(Span::styled(
                "────────────────────",
                Style::default().fg(RULE),
            )),
            Line::from(vec![
                Span::styled("cli    ", Style::default().fg(DIM)),
                Span::styled("v0.1.0", Style::default().fg(MID)),
            ]),
            Line::from(vec![
                Span::styled("user   ", Style::default().fg(DIM)),
                Span::styled(&self.user, Style::default().fg(TEAL)),
            ]),
            Line::from(vec![
                Span::styled("model  ", Style::default().fg(DIM)),
                Span::styled(&self.model, Style::default().fg(MID)),
            ]),
        ]
    }

    fn col3_lines(&self) -> Vec<Line<'_>> {
        let srv_color = if self.server_version == "offline" {
            Color::Rgb(0xff, 0x6b, 0x6b)
        } else {
            MID
        };
        vec![
            Line::from(Span::styled(
                "Server",
                Style::default().fg(MID).add_modifier(Modifier::BOLD),
            )),
            Line::from(Span::styled(
                "────────────────────",
                Style::default().fg(RULE),
            )),
            Line::from(vec![
                Span::styled("version  ", Style::default().fg(DIM)),
                Span::styled(&self.server_version, Style::default().fg(srv_color)),
            ]),
            Line::from(vec![
                Span::styled("host     ", Style::default().fg(DIM)),
                Span::styled(&self.host, Style::default().fg(MID)),
            ]),
            Line::from(vec![
                Span::styled("port     ", Style::default().fg(DIM)),
                Span::styled(self.port.to_string(), Style::default().fg(MID)),
            ]),
        ]
    }
}

impl Renderable for Header {
    fn render(&self, area: Rect, buf: &mut Buffer) {
        let title = " Marcel CLI v0.1.0 ".to_string();
        let block = Block::default()
            .borders(Borders::ALL)
            .border_style(Style::default().fg(ROSE))
            .title(Span::styled(
                title,
                Style::default().fg(ROSE).add_modifier(Modifier::BOLD),
            ));

        let inner = block.inner(area);
        block.render(area, buf);

        let width = inner.width as usize;

        if width >= 86 {
            let cols = Layout::default()
                .direction(Direction::Horizontal)
                .constraints([
                    Constraint::Length(30),
                    Constraint::Length(1),
                    Constraint::Length(24),
                    Constraint::Length(1),
                    Constraint::Min(20),
                ])
                .split(inner);

            render_mascot_col(&self.welcome, cols[0], buf);
            render_separator(cols[1], buf);
            render_col_centered(self.col2_lines(), cols[2], buf);
            render_separator(cols[3], buf);
            render_col_centered(self.col3_lines(), cols[4], buf);
        } else if width >= 56 {
            let cols = Layout::default()
                .direction(Direction::Horizontal)
                .constraints([
                    Constraint::Length(30),
                    Constraint::Length(1),
                    Constraint::Min(20),
                ])
                .split(inner);

            render_mascot_col(&self.welcome, cols[0], buf);
            render_separator(cols[1], buf);
            render_col_centered(self.col2_lines(), cols[2], buf);
        } else {
            render_mascot_col(&self.welcome, inner, buf);
        }
    }

    fn desired_height(&self, _width: u16) -> u16 {
        // welcome + blank + mascot (6 lines) = 8; plus 2 for border
        (MASCOT_LINES + 2) + 2
    }
}

/// Render welcome text + mascot art, both centered in the column.
///
/// All mascot lines have the same unicode-width (7), so `Alignment::Center`
/// gives them identical x offsets, preserving internal alignment.
fn render_mascot_col(welcome: &str, area: Rect, buf: &mut Buffer) {
    let content_h = 1 + 1 + MASCOT_LINES;
    let top_pad = area.height.saturating_sub(content_h) / 2;

    // Welcome text
    let welcome_y = area.y + top_pad;
    if welcome_y < area.y + area.height {
        let welcome_area = Rect::new(area.x, welcome_y, area.width, 1);
        Paragraph::new(Line::from(Span::styled(
            welcome,
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        )))
        .alignment(Alignment::Center)
        .render(welcome_area, buf);
    }

    // Mascot — centered Paragraph, all lines equal width
    let mascot_y = area.y + top_pad + 2;
    let mascot_h = MASCOT_LINES.min(area.height.saturating_sub(mascot_y.saturating_sub(area.y)));
    if mascot_y < area.y + area.height {
        let mascot_area = Rect::new(area.x, mascot_y, area.width, mascot_h);
        let style = Style::default().fg(ROSE);
        let lines: Vec<Line> = MASCOT
            .lines()
            .map(|row| Line::from(Span::styled(row, style)))
            .collect();
        Paragraph::new(lines)
            .alignment(Alignment::Center)
            .render(mascot_area, buf);
    }
}

/// Render info column lines vertically centered in the area.
fn render_col_centered(lines: Vec<Line>, area: Rect, buf: &mut Buffer) {
    let content_h = lines.len() as u16;
    let top_pad = area.height.saturating_sub(content_h) / 2;

    let col_area = Rect::new(
        area.x,
        area.y + top_pad,
        area.width,
        content_h.min(area.height.saturating_sub(top_pad)),
    );
    Paragraph::new(lines).render(col_area, buf);
}

/// Draw a vertical separator line of `│` characters down the full height.
fn render_separator(area: Rect, buf: &mut Buffer) {
    let style = Style::default().fg(SEP);
    for y in area.y..area.y + area.height {
        if let Some(cell) = buf.cell_mut((area.x, y)) {
            cell.set_symbol("│").set_style(style);
        }
    }
}
