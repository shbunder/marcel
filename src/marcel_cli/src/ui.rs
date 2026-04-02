use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Borders, Paragraph, Widget, Wrap};

use crate::render::Renderable;

const ROSE: Color = Color::Rgb(0xcc, 0x5e, 0x76);
const RED: Color = Color::Rgb(0xff, 0x6b, 0x6b);
const GREEN: Color = Color::Rgb(0x4c, 0xaf, 0x50);
const DIM: Color = Color::Rgb(0x55, 0x55, 0x55);
const MID: Color = Color::Rgb(0x88, 0x88, 0x88);

// ── ChatMessage ────────────────────────────────────────────────────────

#[derive(Clone)]
pub enum MessageKind {
    User,
    Assistant,
    System,
    Error,
}

#[derive(Clone)]
pub struct ChatMessage {
    pub kind: MessageKind,
    pub text: String,
}

// ── ChatView ──────────────────────────────────────────────────────────

pub struct ChatView {
    pub messages: Vec<ChatMessage>,
    /// Tokens of the in-flight streaming response.
    pub streaming_tokens: Vec<String>,
    pub scroll_offset: u16,
}

impl ChatView {
    pub fn new() -> Self {
        Self {
            messages: Vec::new(),
            streaming_tokens: Vec::new(),
            scroll_offset: 0,
        }
    }

    pub fn push_user(&mut self, text: &str) {
        self.messages.push(ChatMessage {
            kind: MessageKind::User,
            text: text.into(),
        });
    }

    pub fn push_assistant(&mut self, text: &str) {
        self.messages.push(ChatMessage {
            kind: MessageKind::Assistant,
            text: text.into(),
        });
    }

    pub fn push_system(&mut self, text: &str) {
        self.messages.push(ChatMessage {
            kind: MessageKind::System,
            text: text.into(),
        });
    }

    pub fn push_error(&mut self, text: &str) {
        self.messages.push(ChatMessage {
            kind: MessageKind::Error,
            text: text.into(),
        });
    }

    pub fn push_token(&mut self, token: &str) {
        self.streaming_tokens.push(token.into());
    }

    pub fn finish_stream(&mut self) {
        if !self.streaming_tokens.is_empty() {
            let full: String = self.streaming_tokens.drain(..).collect();
            if !full.trim().is_empty() {
                self.push_assistant(&full);
            }
        }
    }

    pub fn clear(&mut self) {
        self.messages.clear();
        self.streaming_tokens.clear();
        self.scroll_offset = 0;
    }

    fn to_lines(&self, _width: u16) -> Vec<Line<'_>> {
        let mut lines: Vec<Line> = Vec::new();

        for msg in &self.messages {
            match msg.kind {
                MessageKind::User => {
                    lines.push(Line::from(vec![
                        Span::styled(
                            " ❯  ",
                            Style::default()
                                .fg(Color::White)
                                .add_modifier(Modifier::BOLD),
                        ),
                        Span::styled(&msg.text, Style::default().fg(Color::White)),
                    ]));
                    lines.push(Line::from(""));
                }
                MessageKind::Assistant => {
                    // Simple markdown-ish rendering: code blocks get dim bg, rest is white
                    let mut in_code = false;
                    for line in msg.text.lines() {
                        if line.starts_with("```") {
                            in_code = !in_code;
                            lines.push(Line::from(Span::styled(
                                format!("  {line}"),
                                Style::default().fg(DIM),
                            )));
                        } else if in_code {
                            lines.push(Line::from(Span::styled(
                                format!("  {line}"),
                                Style::default().fg(Color::Cyan),
                            )));
                        } else if let Some(heading) = line.strip_prefix("# ") {
                            lines.push(Line::from(Span::styled(
                                format!("  {heading}"),
                                Style::default().fg(ROSE).add_modifier(Modifier::BOLD),
                            )));
                        } else if let Some(heading) = line.strip_prefix("## ") {
                            lines.push(Line::from(Span::styled(
                                format!("  {heading}"),
                                Style::default().fg(ROSE).add_modifier(Modifier::BOLD),
                            )));
                        } else if line.starts_with("- ") || line.starts_with("* ") {
                            lines.push(Line::from(vec![
                                Span::styled("  • ", Style::default().fg(ROSE)),
                                Span::styled(&line[2..], Style::default().fg(Color::White)),
                            ]));
                        } else {
                            lines.push(Line::from(Span::styled(
                                format!("  {line}"),
                                Style::default().fg(Color::White),
                            )));
                        }
                    }
                    lines.push(Line::from(""));
                }
                MessageKind::System => {
                    lines.push(Line::from(Span::styled(
                        format!("  {}", msg.text),
                        Style::default().fg(MID),
                    )));
                }
                MessageKind::Error => {
                    lines.push(Line::from(Span::styled(
                        format!("  {}", msg.text),
                        Style::default().fg(RED),
                    )));
                }
            }
        }

        // Streaming in-flight text
        if !self.streaming_tokens.is_empty() {
            let full: String = self.streaming_tokens.iter().map(|s| s.as_str()).collect();
            lines.push(Line::from(vec![
                Span::styled("● ", Style::default().fg(Color::White)),
                Span::styled(full, Style::default().fg(Color::White)),
            ]));
        }

        lines
    }

    pub fn scroll_to_bottom(&mut self, area_height: u16) {
        let total = self.content_height(area_height.saturating_sub(2));
        let visible = area_height.saturating_sub(2);
        self.scroll_offset = total.saturating_sub(visible);
    }

    fn content_height(&self, width: u16) -> u16 {
        self.to_lines(width).len() as u16
    }
}

impl Renderable for ChatView {
    fn render(&self, area: Rect, buf: &mut Buffer) {
        let lines = self.to_lines(area.width);
        let text = Text::from(lines);
        let paragraph = Paragraph::new(text)
            .scroll((self.scroll_offset, 0))
            .wrap(Wrap { trim: false });
        paragraph.render(area, buf);
    }

    fn desired_height(&self, width: u16) -> u16 {
        self.to_lines(width).len() as u16 + 1
    }
}

// ── InputBox ──────────────────────────────────────────────────────────

pub struct InputBox {
    pub text: String,
    pub cursor: usize,
    history: Vec<String>,
    history_idx: Option<usize>,
    stashed: String,
    /// Tab-completion state: (original prefix, matching commands, current index).
    completion: Option<(String, Vec<String>, usize)>,
}

impl InputBox {
    pub fn new() -> Self {
        Self {
            text: String::new(),
            cursor: 0,
            history: Vec::new(),
            history_idx: None,
            stashed: String::new(),
            completion: None,
        }
    }

    /// Start or cycle through tab completions for the given command list.
    /// Returns true if a completion was applied.
    pub fn tab_complete(&mut self, commands: &[(&str, &str)]) -> bool {
        if !self.text.starts_with('/') {
            return false;
        }

        if let Some((_, ref matches, ref mut idx)) = self.completion {
            // Already completing — cycle to next
            *idx = (*idx + 1) % matches.len();
            let next = matches[*idx].clone();
            self.text = format!("{next} ");
            self.cursor = self.text.len();
            return true;
        }

        // Start new completion
        let prefix = self.text.to_lowercase();
        let matches: Vec<String> = commands
            .iter()
            .map(|(c, _)| c.to_string())
            .filter(|c| c.starts_with(&prefix))
            .collect();

        match matches.len() {
            0 => false,
            1 => {
                self.text = format!("{} ", matches[0]);
                self.cursor = self.text.len();
                // Don't enter cycling mode for single match
                true
            }
            _ => {
                let first = matches[0].clone();
                self.completion = Some((prefix, matches, 0));
                self.text = format!("{first} ");
                self.cursor = self.text.len();
                true
            }
        }
    }

    /// Cancel any active tab-completion cycle (call on any non-Tab keypress).
    pub fn cancel_completion(&mut self) {
        self.completion = None;
    }

    /// Navigate to the previous history entry (older).
    pub fn history_prev(&mut self) {
        if self.history.is_empty() {
            return;
        }
        let idx = match self.history_idx {
            None => {
                // Stash current input before entering history
                self.stashed = self.text.clone();
                self.history.len() - 1
            }
            Some(0) => return, // already at oldest
            Some(i) => i - 1,
        };
        self.history_idx = Some(idx);
        self.text = self.history[idx].clone();
        self.cursor = self.text.len();
    }

    /// Navigate to the next history entry (newer).
    pub fn history_next(&mut self) {
        let idx = match self.history_idx {
            None => return, // not browsing history
            Some(i) => i,
        };
        if idx + 1 >= self.history.len() {
            // Return to stashed input
            self.history_idx = None;
            self.text = self.stashed.clone();
            self.stashed.clear();
        } else {
            self.history_idx = Some(idx + 1);
            self.text = self.history[idx + 1].clone();
        }
        self.cursor = self.text.len();
    }

    pub fn insert(&mut self, c: char) {
        self.text.insert(self.cursor, c);
        self.cursor += c.len_utf8();
    }

    pub fn backspace(&mut self) {
        if self.cursor > 0 {
            let prev = self.text[..self.cursor]
                .char_indices()
                .next_back()
                .map(|(i, _)| i)
                .unwrap_or(0);
            self.text.drain(prev..self.cursor);
            self.cursor = prev;
        }
    }

    pub fn delete(&mut self) {
        if self.cursor < self.text.len() {
            let next = self.text[self.cursor..]
                .char_indices()
                .nth(1)
                .map(|(i, _)| self.cursor + i)
                .unwrap_or(self.text.len());
            self.text.drain(self.cursor..next);
        }
    }

    pub fn move_left(&mut self) {
        if self.cursor > 0 {
            self.cursor = self.text[..self.cursor]
                .char_indices()
                .next_back()
                .map(|(i, _)| i)
                .unwrap_or(0);
        }
    }

    pub fn move_right(&mut self) {
        if self.cursor < self.text.len() {
            self.cursor = self.text[self.cursor..]
                .char_indices()
                .nth(1)
                .map(|(i, _)| self.cursor + i)
                .unwrap_or(self.text.len());
        }
    }

    pub fn home(&mut self) {
        self.cursor = 0;
    }
    pub fn end(&mut self) {
        self.cursor = self.text.len();
    }

    pub fn clear(&mut self) {
        self.text.clear();
        self.cursor = 0;
    }

    pub fn delete_word_back(&mut self) {
        if self.cursor == 0 {
            return;
        }
        // Skip trailing whitespace, then delete to previous whitespace
        let before = &self.text[..self.cursor];
        let trimmed = before.trim_end();
        let new_end = trimmed
            .rfind(char::is_whitespace)
            .map(|i| i + 1)
            .unwrap_or(0);
        self.text.drain(new_end..self.cursor);
        self.cursor = new_end;
    }

    pub fn take(&mut self) -> String {
        let t = self.text.clone();
        if !t.trim().is_empty() {
            self.history.push(t.clone());
        }
        self.text.clear();
        self.cursor = 0;
        self.history_idx = None;
        self.stashed.clear();
        t
    }
}

impl Renderable for InputBox {
    fn render(&self, area: Rect, buf: &mut Buffer) {
        let display = if self.text.is_empty() {
            vec![Line::from(Span::styled(
                "Message Marcel or type / for commands…",
                Style::default().fg(DIM),
            ))]
        } else {
            vec![Line::from(Span::styled(
                &self.text,
                Style::default().fg(Color::White),
            ))]
        };

        let block = Block::default()
            .borders(Borders::ALL)
            .border_style(Style::default().fg(ROSE))
            .title(Span::styled(
                " ❯ ",
                Style::default()
                    .fg(Color::White)
                    .add_modifier(Modifier::BOLD),
            ));

        let paragraph = Paragraph::new(display).block(block);
        paragraph.render(area, buf);
    }

    fn desired_height(&self, _width: u16) -> u16 {
        3 // 1 line of text + 2 for border
    }

    fn cursor_pos(&self, area: Rect) -> Option<(u16, u16)> {
        // Cursor inside the block (1 for left border + 1 for title spacing)
        let x = area.x + 1 + self.cursor as u16;
        let y = area.y + 1;
        if x < area.x + area.width - 1 {
            Some((x, y))
        } else {
            None
        }
    }
}

// ── StatusBar ─────────────────────────────────────────────────────────

pub struct StatusBar {
    pub connected: bool,
    pub model: String,
    pub session_cost: f64,
    pub turn_count: u32,
}

impl StatusBar {
    pub fn new(model: &str) -> Self {
        Self {
            connected: false,
            model: model.into(),
            session_cost: 0.0,
            turn_count: 0,
        }
    }
}

impl Renderable for StatusBar {
    fn render(&self, area: Rect, buf: &mut Buffer) {
        let sep = Span::styled("  │  ", Style::default().fg(Color::Rgb(0x44, 0x44, 0x44)));

        let dot = if self.connected { "●" } else { "○" };
        let conn_color = if self.connected { GREEN } else { RED };
        let conn_text = if self.connected {
            "connected"
        } else {
            "offline"
        };

        let mut spans = vec![
            Span::styled(format!(" {dot} "), Style::default().fg(conn_color)),
            Span::styled(conn_text, Style::default().fg(conn_color)),
            sep.clone(),
            Span::styled("model: ", Style::default().fg(DIM)),
            Span::styled(&self.model, Style::default().fg(MID)),
        ];

        // Show cost + turns when we have data
        if self.session_cost > 0.0 || self.turn_count > 0 {
            spans.push(sep.clone());
            spans.push(Span::styled(
                format!("${:.4}", self.session_cost),
                Style::default().fg(MID),
            ));
            spans.push(Span::styled(
                format!("  {} turns", self.turn_count),
                Style::default().fg(DIM),
            ));
        }

        spans.push(sep);
        spans.push(Span::styled("/help", Style::default().fg(ROSE)));
        spans.push(Span::styled(" for commands", Style::default().fg(DIM)));

        let line = Line::from(spans);

        let bg = Style::default().bg(Color::Rgb(0x19, 0x18, 0x19));
        for x in area.x..area.x + area.width {
            buf[(x, area.y)].set_style(bg);
        }
        buf.set_line(area.x, area.y, &line, area.width);
    }

    fn desired_height(&self, _width: u16) -> u16 {
        1
    }
}
