# PixelKit — the pixel-classic design system

The look chosen in spike SP5 (B-29a): the wireframes in `scratch/spikes/SP5-avatar/wireframes/`, as SwiftUI.

## Tokens

- **Colours** — `PixelColor` holds the six base colours (cream `#f6efe4`, ink `#2a1d24`, rose `#d87990`,
  deep `#9c3e5a`, teal `#3f6b6a`, gold `#e2a43a`). `PixelTheme.light` / `.dark` map them to roles (background, bar,
  dialog, card, text, muted, line, shadow, accent, …). Components call `PixelTheme(colorScheme)`.
- **Grid** — `PixelMetrics`: 2-pt pixel grid, 2-pt and 3-pt borders, 2-pt and 4-pt offset shadows, 44-pt tap targets.
  `.pixelBox(fill:line:shadow:)` draws a hard-edged box.
- **Type** — `PixelText(text, role:)`. Labels and headings use **Silkscreen**, in capitals, growing with Dynamic Type up to
  a cap (`PixelTextRole.maximumSize`). Body and detail text use **Pixelify Sans** and grow without a cap. Write label
  text in sentence case; VoiceOver reads it as written. `PixelText` keeps every character in pixel style:
  € in body text is drawn in Silkscreen (Pixelify's € reads as a 6 or an 8); → ← ✓ ✔, which neither font has, are
  pixel bitmaps in the text colour; Silkscreen shows − as - and ĳ as ij. A character nothing covers is the only
  thing that reaches the system font. `GlyphCoverageTests` holds both fonts to the Belgian set (é è ê ë à â ç î ï
  ô û ù ü œ « » ’ “ ” „ — – … ° €, and the capitals for Silkscreen). The `.pixelText(role)` modifier sets the
  same fonts on any view but cannot do this per-character work.
- **Fonts** — `Sources/PixelKit/Resources/Fonts/`, from github.com/google/fonts (`ofl/silkscreen`, `ofl/pixelifysans`),
  with their SIL Open Font License files. They register themselves on first use (`PixelKit.registerFonts()` does it
  early).

## Markdown

`ItemMarkdown.parse` turns Markdown into blocks (headings, paragraphs, lists, quotes, code, tables, rules) and
`MarkdownView` draws them, for the item viewer, Marcel's notebook and (ADR-007) Marcel's replies. Paragraphs, list
items and quotes keep their inline style as spans: **bold** in Pixelify's bold weight; *italic* in the rose tone (the
font has no slanted face and iOS does not fake one); `code` in the system monospaced font in teal; links underlined.
The safety rules hold everywhere: only an `http`/`https` link with a host is kept (`javascript:`, `file:`, `tel:` and
app links stay plain words); a link opens only when it is tapped, in the text or on the button listed under its block;
HTML is shown as the words it was written with, never run. A table draws at most `MarkdownLimits.tableRows` (200) rows
and counts the rest, scrolls sideways when wider than the screen, and becomes one card per row at accessibility sizes.
Tests: `MarkdownTests`.

## Chat (S-18.3, ADR-007)

`Sources/PixelKit/Chat/`: the chat's look, in the system font with Dynamic Type (the pixel font only on card labels).
`ChatPalette` (the wireframe's light and dark colours; label text and filled buttons a shade darker in light mode to
reach 4.5:1, checked by `textPairs`; `bubble(_:)` lightens a helper's colour until dark text on it reaches 4.5:1) ·
`ChatBubble` + `ChatBubbleShape` (the notch: corners 16, 16, 4, 16) · `ChatCard` (`ChatCardKind`: started, needs you,
done, failed; a 2-pt frame on all four sides, a pixel label with its `ChatGlyph`) · `ChatCardButton` · `ChatTag` ·
`ChatTimeLine` · `ChatQuote` · `ChatItemPreview`. Marcel's replies are `MarkdownView(parts:style: .chat)`
(`ChatMarkdownBody`, `ChatTable`). MarcelChat's tests check them (`ChatLookTests`).

## Components

`DialogBox` (speaker, text, blinking ▼, optional accessory) · `ChoicePrompt` (▶ follows keyboard and VoiceOver
focus; arrow keys and Return work) · `QuestCard` (S-18.4: a white rounded card framed by its state — gold needs you, teal working, rose ready, red
failed — with the title, the state word, the project tag, the detail and a thin progress bar) ·
`ArtifactCard` + `ArtifactRow` (gold header, "updated" badge) · `ItemGetBanner` · `ProjectChip` (five tones) ·
`StateBadge` (every task state) · `PixelTabBar` · `HeartsHeader` (avatar slot for AvatarKit). `PixelGallery` shows
them all on one screen. Each file has a SwiftUI preview in light and dark.

## Accessibility

- Every component reads as plain words ("Needs your OK", "Marcel says: …"); ▶ ▼ ♥ are drawn as pixel bitmaps and
  hidden from VoiceOver.
- Every text and background pair is listed in `PixelTheme.textPairs` and must reach 4.5:1 in both themes. Two
  wireframe colours failed and were changed: the working and ready badges use ink text instead of white (2.9:1), and
  the green project chip is darker than the wireframe's sage (3.38:1 on cream).
- Reduce Motion stops the ▼ blink and leaves it visible.
- At accessibility text sizes, cards stack their lines instead of squeezing them side by side.

## Tests (`make -C ios pixelkit-test`, part of `make ios-check`)

- `ComponentSnapshotTests` — every component in light, dark and Accessibility 5, rendered with `ImageRenderer` and
  compared with `Tests/PixelKitTests/__Snapshots__/*.png`. No snapshot library: one pixel moving by more than
  8/255 fails, so a single changed digit is caught. `SampleSheetSnapshotTests` draws real Dutch and French phrases
  ("−€486/jaar", "l’été", "Taak ✓", …) in body and label fonts; look at it after any font change.
- **References are tied to the simulator runtime** they were drawn on (iOS 26.4.1, written to
  `__Snapshots__/runtime.txt`). Text rendering changes between iOS versions, so on another runtime the tests stop
  with a message to re-record rather than fail image by image. When the simulator's iOS changes, re-record and
  review the images. After a deliberate change run `make -C ios pixelkit-record`, **look at
  the new PNGs**, and commit them. Failing runs leave their image in `ios/.build/pixelkit-snapshot-failures/`.
- `AccessibilityAuditTests` — shows the gallery in a real window and walks the accessibility tree SwiftUI builds:
  labels present and in plain words, buttons at least 44 × 44 pt, nothing clipped (no element off the screen or
  over another), selected tab and header traits, body text that grows. It runs in light and dark, at the default
  and at the largest Dynamic Type size. This stands in for XCUITest's `performAccessibilityAudit()`, which needs the iOS 26.5 platform this Mac's
  Xcode lacks (see ../README.md).
- `ContrastTests`, `TypeAndMotionTests`, `GlyphCoverageTests` — WCAG ratios, fonts and licences, Dynamic Type
  sizes, the grid, Reduce Motion (including the system setting itself reaching the ▼), choice-prompt keys, and
  character coverage.

`ImageRenderer` draws nothing for a view that binds VoiceOver focus, so snapshots set `pixelRendersOffscreen` and
the choice prompt skips that binding there. The audit test renders it in a window with the binding on.
