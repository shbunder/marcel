# shared/avatar/ — avatar recipes and palettes

An avatar is built from an animal **recipe** (`recipes/<animal>.json`), a **palette**
(`palettes.json`) and a **seed** (the agent's name), and comes out the same every time. The iOS
`AvatarKit` package reads these files, and a Python test validates them. Filled in by S-13.1.
