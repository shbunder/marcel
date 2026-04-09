use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Borders, Paragraph, Widget, Wrap};

use crate::render::Renderable;

const ROSE: Color = Color::Rgb(0xcc, 0x5e, 0x76);
const RED: Color = Color::Rgb(0xff, 0x6b, 0x6b);
const GREEN: Color = Color::Rgb(0x4c, 0xaf, 0x50);
const YELLOW: Color = Color::Rgb(0xff, 0xc1, 0x07);
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

/// A tool invocation currently in progress.
#[derive(Clone, Debug)]
pub struct ToolActivity {
    pub tool_call_id: String,
    pub tool_name: String,
}

pub struct ChatView {
    pub messages: Vec<ChatMessage>,
    /// Tokens of the in-flight streaming response.
    pub streaming_tokens: Vec<String>,
    pub scroll_offset: u16,
    /// Tools currently executing (shown as activity indicators).
    pub active_tools: Vec<ToolActivity>,
    /// Visible height of the chat area (rows); updated each frame by the main loop.
    pub area_height: u16,
    /// Visible width of the chat area (cols); updated each frame by the main loop.
    pub area_width: u16,
    /// When true, new content automatically scrolls to the bottom.
    /// Disabled when the user scrolls up; re-enabled when they reach the bottom or send.
    pub following: bool,
}

impl ChatView {
    pub fn new() -> Self {
        Self {
            messages: Vec::new(),
            streaming_tokens: Vec::new(),
            scroll_offset: 0,
            active_tools: Vec::new(),
            area_height: 24,
            area_width: 80,
            following: true,
        }
    }

    pub fn start_tool(&mut self, tool_call_id: String, tool_name: String) {
        self.active_tools.push(ToolActivity {
            tool_call_id,
            tool_name,
        });
    }

    pub fn end_tool(&mut self, tool_call_id: &str) {
        self.active_tools.retain(|t| t.tool_call_id != tool_call_id);
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
        self.active_tools.clear();
    }

    pub fn clear(&mut self) {
        self.messages.clear();
        self.streaming_tokens.clear();
        self.active_tools.clear();
        self.scroll_offset = 0;
        self.following = true;
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

        // Active tool call indicators
        for tool in &self.active_tools {
            lines.push(Line::from(vec![
                Span::styled("  ⚙ ", Style::default().fg(YELLOW)),
                Span::styled(&tool.tool_name, Style::default().fg(YELLOW)),
                Span::styled(" …", Style::default().fg(DIM)),
            ]));
        }

        lines
    }

    /// Scroll up by `lines`, disabling auto-follow.
    pub fn scroll_up(&mut self, lines: u16) {
        self.following = false;
        self.scroll_offset = self.scroll_offset.saturating_sub(lines);
    }

    /// Scroll down by `lines`. Re-enables auto-follow when the bottom is reached.
    pub fn scroll_down(&mut self, lines: u16) {
        let max = self
            .content_height(self.area_width)
            .saturating_sub(self.area_height);
        let new = self.scroll_offset.saturating_add(lines).min(max);
        self.scroll_offset = new;
        if new >= max {
            self.following = true;
        }
    }

    /// Scroll to the bottom and re-enable auto-follow.
    pub fn scroll_to_bottom(&mut self) {
        self.following = true;
        let total = self.content_height(self.area_width);
        self.scroll_offset = total.saturating_sub(self.area_height);
    }

    /// Count visual rows after word-wrapping at `width` columns.
    fn content_height(&self, width: u16) -> u16 {
        if width == 0 {
            return 0;
        }
        self.to_lines(width)
            .iter()
            .map(|line| {
                let line_width: usize = line
                    .spans
                    .iter()
                    .map(|span| unicode_width::UnicodeWidthStr::width(span.content.as_ref()))
                    .sum();
                // An empty line still occupies one visual row.
                let rows = if line_width == 0 {
                    1
                } else {
                    line_width.div_ceil(width as usize)
                };
                rows as u16
            })
            .sum()
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

/// A single suggestion entry (command name + description).
#[derive(Clone)]
pub struct Suggestion {
    pub name: String,
    pub desc: String,
}

pub struct InputBox {
    pub text: String,
    pub cursor: usize,
    history: Vec<String>,
    history_idx: Option<usize>,
    stashed: String,
    /// Live suggestions shown as a dropdown.
    pub suggestions: Vec<Suggestion>,
    /// Index of the highlighted suggestion (0-based).
    pub selected_suggestion: usize,
}

impl InputBox {
    pub fn new() -> Self {
        Self {
            text: String::new(),
            cursor: 0,
            history: Vec::new(),
            history_idx: None,
            stashed: String::new(),
            suggestions: Vec::new(),
            selected_suggestion: 0,
        }
    }

    /// Returns true when the suggestion dropdown is visible.
    pub fn has_suggestions(&self) -> bool {
        !self.suggestions.is_empty()
    }

    /// Recompute suggestions based on current input and the command list.
    pub fn update_suggestions(&mut self, commands: &[(&str, &str)]) {
        let prefix = self.text.to_lowercase();
        if prefix.starts_with('/') && !prefix.contains(' ') {
            self.suggestions = commands
                .iter()
                .filter(|(c, _)| c.starts_with(&prefix))
                .map(|(c, d)| Suggestion {
                    name: c.to_string(),
                    desc: d.to_string(),
                })
                .collect();
            // Clamp selection
            if self.selected_suggestion >= self.suggestions.len() {
                self.selected_suggestion = 0;
            }
        } else {
            self.suggestions.clear();
            self.selected_suggestion = 0;
        }
    }

    /// Move selection up (wraps around).
    pub fn suggestion_prev(&mut self) {
        if self.suggestions.is_empty() {
            return;
        }
        if self.selected_suggestion == 0 {
            self.selected_suggestion = self.suggestions.len() - 1;
        } else {
            self.selected_suggestion -= 1;
        }
    }

    /// Move selection down (wraps around).
    pub fn suggestion_next(&mut self) {
        if self.suggestions.is_empty() {
            return;
        }
        self.selected_suggestion = (self.selected_suggestion + 1) % self.suggestions.len();
    }

    /// Accept the currently selected suggestion — replaces input text.
    /// Returns true if a suggestion was accepted.
    pub fn accept_suggestion(&mut self) -> bool {
        if let Some(s) = self.suggestions.get(self.selected_suggestion) {
            self.text = format!("{} ", s.name);
            self.cursor = self.text.len();
            self.suggestions.clear();
            self.selected_suggestion = 0;
            true
        } else {
            false
        }
    }

    /// Dismiss the suggestion dropdown.
    pub fn dismiss_suggestions(&mut self) {
        self.suggestions.clear();
        self.selected_suggestion = 0;
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

const MAX_VISIBLE_SUGGESTIONS: usize = 6;

impl Renderable for InputBox {
    fn render(&self, area: Rect, buf: &mut Buffer) {
        let suggestion_count = self.suggestions.len().min(MAX_VISIBLE_SUGGESTIONS);
        let suggestion_height = suggestion_count as u16;

        // ── Suggestion dropdown (rendered above the input box) ──
        if suggestion_height > 0 {
            let dropdown_area = Rect::new(area.x, area.y, area.width, suggestion_height);

            // Scroll window: keep the selected item visible
            let max_visible = MAX_VISIBLE_SUGGESTIONS;
            let scroll_start = if self.selected_suggestion >= max_visible {
                self.selected_suggestion - max_visible + 1
            } else {
                0
            };

            // Background fill
            let bg = Style::default().bg(Color::Rgb(0x1e, 0x1e, 0x1e));
            for y in dropdown_area.y..dropdown_area.y + dropdown_area.height {
                for x in dropdown_area.x..dropdown_area.x + dropdown_area.width {
                    buf[(x, y)].set_style(bg);
                }
            }

            for (row, abs_idx) in (scroll_start..scroll_start + max_visible)
                .filter(|&i| i < self.suggestions.len())
                .enumerate()
            {
                let suggestion = &self.suggestions[abs_idx];
                let y = dropdown_area.y + row as u16;
                let is_selected = abs_idx == self.selected_suggestion;

                let (name_style, desc_style, row_bg) = if is_selected {
                    (
                        Style::default()
                            .fg(Color::Cyan)
                            .add_modifier(Modifier::BOLD),
                        Style::default().fg(Color::White),
                        Style::default().bg(Color::Rgb(0x2a, 0x2a, 0x2a)),
                    )
                } else {
                    (Style::default().fg(MID), Style::default().fg(DIM), bg)
                };

                // Fill row background
                for x in area.x..area.x + area.width {
                    buf[(x, y)].set_style(row_bg);
                }

                let line = Line::from(vec![
                    Span::styled(if is_selected { " ▸ " } else { "   " }, name_style),
                    Span::styled(&suggestion.name, name_style),
                    Span::styled("  ", desc_style),
                    Span::styled(&suggestion.desc, desc_style),
                ]);
                buf.set_line(area.x, y, &line, area.width);
            }
        }

        // ── Input box (rendered below suggestions) ──
        let input_area = Rect::new(
            area.x,
            area.y + suggestion_height,
            area.width,
            3, // border top + text + border bottom
        );

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
        paragraph.render(input_area, buf);
    }

    fn desired_height(&self, _width: u16) -> u16 {
        let suggestions = self.suggestions.len().min(MAX_VISIBLE_SUGGESTIONS) as u16;
        3 + suggestions // input box (3) + suggestion rows
    }

    fn cursor_pos(&self, area: Rect) -> Option<(u16, u16)> {
        let suggestion_height = self.suggestions.len().min(MAX_VISIBLE_SUGGESTIONS) as u16;
        let x = area.x + 1 + self.cursor as u16;
        let y = area.y + suggestion_height + 1; // below suggestions, inside border
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
    /// Brief flash message (e.g. "copied") shown until the caller clears it.
    pub notification: Option<String>,
}

impl StatusBar {
    pub fn new(model: &str) -> Self {
        Self {
            connected: false,
            model: model.into(),
            session_cost: 0.0,
            turn_count: 0,
            notification: None,
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

        if let Some(notif) = &self.notification {
            spans.push(sep.clone());
            spans.push(Span::styled(notif.as_str(), Style::default().fg(GREEN)));
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

#[cfg(test)]
mod tests {
    use ratatui::{buffer::Buffer, layout::Rect};

    use super::*;
    use crate::render::Renderable;

    // ── helpers ──────────────────────────────────────────────────────────

    fn make_chat(area_width: u16, area_height: u16) -> ChatView {
        let mut chat = ChatView::new();
        chat.area_width = area_width;
        chat.area_height = area_height;
        chat
    }

    /// Render the chat into a fresh buffer and return each row as a trimmed string.
    fn render_rows(chat: &ChatView) -> Vec<String> {
        let area = Rect::new(0, 0, chat.area_width, chat.area_height);
        let mut buf = Buffer::empty(area);
        chat.render(area, &mut buf);
        (0..chat.area_height)
            .map(|y| {
                (0..chat.area_width)
                    .map(|x| buf[(x, y)].symbol().to_string())
                    .collect::<String>()
                    .trim_end()
                    .to_string()
            })
            .collect()
    }

    fn visible(chat: &ChatView) -> String {
        render_rows(chat).join("\n")
    }

    // ── logic tests (no rendering) ────────────────────────────────────────

    #[test]
    fn new_starts_following_at_top() {
        let chat = ChatView::new();
        assert_eq!(chat.scroll_offset, 0);
        assert!(chat.following);
    }

    #[test]
    fn scroll_up_disables_following() {
        let mut chat = make_chat(80, 10);
        for i in 0..30 {
            chat.push_assistant(&format!("msg{i}"));
        }
        chat.scroll_to_bottom();
        assert!(chat.following);

        chat.scroll_up(5);
        assert!(!chat.following);
        assert!(chat.scroll_offset < chat.content_height(chat.area_width).saturating_sub(10));
    }

    #[test]
    fn scroll_to_bottom_sets_correct_offset() {
        let mut chat = make_chat(80, 10);
        for i in 0..30 {
            chat.push_assistant(&format!("msg{i}"));
        }
        chat.scroll_to_bottom();
        let total = chat.content_height(chat.area_width);
        assert_eq!(chat.scroll_offset, total.saturating_sub(10));
        assert!(chat.following);
    }

    #[test]
    fn scroll_down_to_bottom_re_enables_following() {
        let mut chat = make_chat(80, 10);
        for i in 0..30 {
            chat.push_assistant(&format!("msg{i}"));
        }
        chat.scroll_to_bottom();
        chat.scroll_up(20);
        assert!(!chat.following);

        chat.scroll_down(100);
        assert!(chat.following);
    }

    #[test]
    fn scroll_down_mid_content_stays_not_following() {
        let mut chat = make_chat(80, 10);
        for i in 0..50 {
            chat.push_assistant(&format!("msg{i}"));
        }
        chat.scroll_to_bottom();
        chat.scroll_up(30);
        assert!(!chat.following);

        chat.scroll_down(5);
        assert!(!chat.following);
    }

    #[test]
    fn following_false_caller_skips_auto_scroll() {
        let mut chat = make_chat(80, 10);
        for i in 0..20 {
            chat.push_assistant(&format!("msg{i}"));
        }
        chat.scroll_to_bottom();
        chat.scroll_up(10);
        let saved = chat.scroll_offset;
        assert!(!chat.following);

        chat.push_token("new token");
        if chat.following {
            chat.scroll_to_bottom();
        }
        assert_eq!(chat.scroll_offset, saved);
    }

    #[test]
    fn clear_resets_following_and_offset() {
        let mut chat = make_chat(80, 10);
        for i in 0..30 {
            chat.push_assistant(&format!("msg{i}"));
        }
        chat.scroll_to_bottom();
        chat.scroll_up(5);
        assert!(!chat.following);

        chat.clear();
        assert!(chat.following);
        assert_eq!(chat.scroll_offset, 0);
    }

    // ── content_height: wrapping arithmetic ──────────────────────────────

    #[test]
    fn short_message_is_two_visual_rows() {
        // Each assistant message → "  {text}" + blank line = 2 rows when text fits width.
        let mut chat = make_chat(40, 20);
        chat.push_assistant("hello");
        assert_eq!(chat.content_height(40), 2, "short msg should be 2 rows");
    }

    #[test]
    fn long_message_wraps_into_extra_rows() {
        // "  " (2) + 38 a's = 40 chars → exactly 2 visual rows + 1 blank = 3
        let mut chat = make_chat(20, 20);
        chat.push_assistant(&"a".repeat(18)); // "  " + 18 = 20 → 1 row + blank = 2
        assert_eq!(chat.content_height(20), 2, "fits exactly in 1 row");

        let mut chat2 = make_chat(20, 20);
        chat2.push_assistant(&"a".repeat(19)); // "  " + 19 = 21 → ceil(21/20)=2 rows + blank = 3
        assert_eq!(chat2.content_height(20), 3, "one char over wraps to 3 rows");
    }

    #[test]
    fn content_height_matches_render_line_count() {
        // Verify our estimate equals what ratatui actually renders by checking
        // that scroll_to_bottom lands the last message at the bottom of the viewport.
        let mut chat = make_chat(30, 4);
        chat.push_assistant("short"); // 2 rows
        chat.push_assistant(&"b".repeat(29)); // "  " + 29 = 31 → 2 rows + blank = 3

        // total = 5, area_height = 4 → max_scroll = 1
        assert_eq!(chat.content_height(30), 5);
        chat.scroll_to_bottom();
        assert_eq!(chat.scroll_offset, 1);

        let screen = visible(&chat);
        assert!(
            screen.contains("bbb"),
            "long msg should be visible: {screen}"
        );
    }

    // ── rendering: what's actually on screen ─────────────────────────────

    #[test]
    fn renders_first_message_at_top_when_no_scroll() {
        let mut chat = make_chat(40, 6);
        chat.push_assistant("hello world");

        let rows = render_rows(&chat);
        assert!(
            rows[0].contains("hello world"),
            "expected 'hello world' in row 0, got: {:?}",
            rows[0]
        );
    }

    #[test]
    fn scroll_to_bottom_shows_last_message_on_screen() {
        // 10 messages × 2 rows = 20 total; area_height = 4; max_scroll = 16
        let mut chat = make_chat(40, 4);
        for i in 0..10 {
            chat.push_assistant(&format!("msg{i:02}"));
        }
        chat.scroll_to_bottom();
        assert_eq!(chat.scroll_offset, 16);

        let screen = visible(&chat);
        assert!(screen.contains("msg09"), "last msg not visible:\n{screen}");
        assert!(
            !screen.contains("msg00"),
            "first msg wrongly visible:\n{screen}"
        );
    }

    #[test]
    fn scroll_up_reveals_earlier_content() {
        // 10 messages × 2 rows = 20; area = 4; max_scroll = 16
        // After scroll_up(10) from bottom: offset = 6 → rows 6-9 visible
        // Row 6 = msg03 text, row 7 = blank, row 8 = msg04 text, row 9 = blank
        let mut chat = make_chat(40, 4);
        for i in 0..10 {
            chat.push_assistant(&format!("msg{i:02}"));
        }
        chat.scroll_to_bottom();
        chat.scroll_up(10);
        assert_eq!(chat.scroll_offset, 6);

        let screen = visible(&chat);
        assert!(screen.contains("msg03"), "msg03 not visible:\n{screen}");
    }

    #[test]
    fn scroll_to_bottom_after_long_message_shows_long_message() {
        let mut chat = make_chat(20, 4);
        chat.push_assistant("short"); // 2 rows
        let long = "b".repeat(19); // "  " + 19 = 21 → 2 rows + blank = 3
        chat.push_assistant(&long);
        // total = 5, max_scroll = 5 - 4 = 1
        chat.scroll_to_bottom();

        let screen = visible(&chat);
        assert!(screen.contains("bbb"), "long msg not visible:\n{screen}");
    }

    #[test]
    fn user_message_visible_at_top() {
        let mut chat = make_chat(40, 6);
        chat.push_user("what is the weather?");

        let rows = render_rows(&chat);
        assert!(
            rows[0].contains("what is the weather?"),
            "user msg not in row 0: {:?}",
            rows[0]
        );
    }

    #[test]
    fn no_content_overflow_past_max_scroll() {
        // scroll_down(u16::MAX) should clamp at max, not wrap around
        let mut chat = make_chat(40, 4);
        for i in 0..5 {
            chat.push_assistant(&format!("m{i}"));
        }
        let max = chat.content_height(40).saturating_sub(4);
        chat.scroll_down(u16::MAX);
        assert_eq!(chat.scroll_offset, max, "offset clamped to max");
    }
}
