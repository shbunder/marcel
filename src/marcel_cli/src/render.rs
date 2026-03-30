/// Renderable trait and layout primitives, modeled after codex-cli's composition system.
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;

// ── Renderable trait ──────────────────────────────────────────────────

pub trait Renderable {
    fn render(&self, area: Rect, buf: &mut Buffer);
    fn desired_height(&self, width: u16) -> u16;
    fn cursor_pos(&self, _area: Rect) -> Option<(u16, u16)> {
        None
    }
}

// ── FlexChild ─────────────────────────────────────────────────────────

pub struct FlexChild<'a> {
    pub flex: u16,
    pub child: &'a dyn Renderable,
}

// ── FlexLayout ────────────────────────────────────────────────────────
/// Vertical flex layout: children with flex=0 get their desired_height,
/// children with flex>0 share remaining space proportionally.

pub struct FlexLayout<'a> {
    children: Vec<FlexChild<'a>>,
}

impl<'a> FlexLayout<'a> {
    pub fn new() -> Self {
        Self {
            children: Vec::new(),
        }
    }

    pub fn push(&mut self, flex: u16, child: &'a dyn Renderable) {
        self.children.push(FlexChild { flex, child });
    }
}

impl Renderable for FlexLayout<'_> {
    fn render(&self, area: Rect, buf: &mut Buffer) {
        let allocs = self.allocate(area.width, area.height);
        let mut y = area.y;
        for (child, h) in self.children.iter().zip(allocs.iter()) {
            if *h == 0 {
                continue;
            }
            let rect = Rect::new(area.x, y, area.width, *h);
            child.child.render(rect, buf);
            y += h;
        }
    }

    fn desired_height(&self, width: u16) -> u16 {
        self.children
            .iter()
            .map(|c| {
                if c.flex == 0 {
                    c.child.desired_height(width)
                } else {
                    1
                }
            })
            .sum()
    }

    fn cursor_pos(&self, area: Rect) -> Option<(u16, u16)> {
        let allocs = self.allocate(area.width, area.height);
        let mut y = area.y;
        for (child, h) in self.children.iter().zip(allocs.iter()) {
            if *h == 0 {
                continue;
            }
            let rect = Rect::new(area.x, y, area.width, *h);
            if let Some(pos) = child.child.cursor_pos(rect) {
                return Some(pos);
            }
            y += h;
        }
        None
    }
}

impl FlexLayout<'_> {
    fn allocate(&self, width: u16, total: u16) -> Vec<u16> {
        let mut allocs = vec![0u16; self.children.len()];
        let mut remaining = total;
        let mut total_flex: u16 = 0;

        // First pass: fixed children (flex=0)
        for (i, child) in self.children.iter().enumerate() {
            if child.flex == 0 {
                let h = child.child.desired_height(width).min(remaining);
                allocs[i] = h;
                remaining = remaining.saturating_sub(h);
            } else {
                total_flex += child.flex;
            }
        }

        // Second pass: flex children share remaining space
        if total_flex > 0 {
            let mut flex_remaining = remaining;
            for (i, child) in self.children.iter().enumerate() {
                if child.flex > 0 {
                    let share = (remaining as u32 * child.flex as u32 / total_flex as u32) as u16;
                    let h = share.min(flex_remaining);
                    allocs[i] = h;
                    flex_remaining = flex_remaining.saturating_sub(h);
                }
            }
            // Give leftover to last flex child
            if flex_remaining > 0 {
                for i in (0..self.children.len()).rev() {
                    if self.children[i].flex > 0 {
                        allocs[i] += flex_remaining;
                        break;
                    }
                }
            }
        }
        allocs
    }
}

// ── ColumnLayout ──────────────────────────────────────────────────────
/// Simple vertical stack — each child gets its desired_height.

#[allow(dead_code)]
pub struct ColumnLayout<'a> {
    children: Vec<&'a dyn Renderable>,
}

#[allow(dead_code)]
impl<'a> ColumnLayout<'a> {
    pub fn new() -> Self {
        Self {
            children: Vec::new(),
        }
    }

    pub fn push(&mut self, child: &'a dyn Renderable) {
        self.children.push(child);
    }
}

impl Renderable for ColumnLayout<'_> {
    fn render(&self, area: Rect, buf: &mut Buffer) {
        let mut y = area.y;
        for child in &self.children {
            let h = child
                .desired_height(area.width)
                .min(area.height.saturating_sub(y - area.y));
            if h == 0 {
                continue;
            }
            let rect = Rect::new(area.x, y, area.width, h);
            child.render(rect, buf);
            y += h;
        }
    }

    fn desired_height(&self, width: u16) -> u16 {
        self.children.iter().map(|c| c.desired_height(width)).sum()
    }

    fn cursor_pos(&self, area: Rect) -> Option<(u16, u16)> {
        let mut y = area.y;
        for child in &self.children {
            let h = child.desired_height(area.width);
            let rect = Rect::new(area.x, y, area.width, h);
            if let Some(pos) = child.cursor_pos(rect) {
                return Some(pos);
            }
            y += h;
        }
        None
    }
}
