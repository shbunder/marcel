# Look — the look picker (S-19.4, B-44)

`LookPicker(model:saved:)`: choose your helper's look from the Pixel Zoo. Onboarding (S-17.5) and Settings (S-17.6)
show it; `saved` gets the hub's answer.

- **Preview.** The look being chosen, drawn by the hub (`GET /avatar-previews`, one 32 × 32 PNG, sharp pixels), with
  the name and the look in words ("A teal fox, cool"). Each animal in the grid is drawn in the colour and personality
  chosen so far. A look is asked for once (the same look always draws the same image); offline the picker says so and
  asks again later.
- **Choices.** 11 animals, 17 colours ("Its own colours" keeps the animal's own, shown with a sparkle), 9
  personalities: the contract's `LookAnimal`, `LookColor`, `LookPersonality`. Every choice is a button VoiceOver
  reads by name, with "selected" on the chosen one.
- **Pick from my name.** `LookFromName.pick` is the Pixel Zoo's `from_name` (FNV-1a over UTF-16, JavaScript's trim,
  lower case), so the phone picks exactly the look the hub gives a helper made without one. A helper with no look
  starts from it.
- **Save.** `PATCH /agents/{id}` with `look`. The hub sends `agent.updated`; the chat's header and thinking row fetch
  the new sheet (S-19.3) and your bubbles take its colour. A refusal shows the hub's sentence and keeps the choice.
- **Colours.** `LookColors` (the Zoo's palette, and each animal's own colour) lives here; the chat's bubbles use it.

## Tests (`make -C ios look-test`, part of `make ios-check`)

`LookTests`: "Pick from my name" against the Pixel Zoo's own picks (`python -c "import zoo.core as c;
c.from_name(name)"`), the lists against the contract, a new helper starting from its name, fox / teal / cool → Save,
a refusal, previews asked once and offline, the words. `Look-picker` snapshot in light, dark and the largest text,
with previews from `Tests/MarcelLookTests/Fixtures/` (drawn by the Zoo in teal and cool). Re-record with
`make -C ios look-record`. The UI test on the mock hub (pick → Save → the header shows it) is in the Chat package:
`ChatMockHubTests.testPickingALookOnTheMockHubShowsInTheHeader`.
