//! Allocates, fills, diffs and renders full-screen buffers and prints the
//! elapsed milliseconds.

use std::{hint::black_box, time::Instant};

use ratatui::{backend::TestBackend, prelude::*, widgets::Paragraph};

const WIDTH: u16 = 318;
const HEIGHT: u16 = 82;

fn main() {
    let area = Rect::new(0, 0, WIDTH, HEIGHT);
    let line = "The quick brown fox jumps over the lazy dog 0123456789 ";
    let start = Instant::now();
    for _ in 0..300 {
        let mut buf = Buffer::empty(area);
        for y in 0..HEIGHT {
            buf.set_string(0, y, line, Style::default());
        }
        let next = buf.clone();
        black_box(buf.diff(&next).len());
    }
    let text = line.repeat(6);
    let lines: Vec<Line> = (0..HEIGHT).map(|_| Line::from(text.as_str())).collect();
    let mut terminal = Terminal::new(TestBackend::new(WIDTH, HEIGHT)).unwrap();
    for _ in 0..100 {
        terminal
            .draw(|f| f.render_widget(Paragraph::new(lines.clone()), f.size()))
            .unwrap();
    }
    println!("{} ms", start.elapsed().as_millis());
}
