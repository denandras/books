# Books — Interactive 3D Book Shelf

An interactive Three.js library of clothbound hardcovers. Browse a continuous shelf, pull a volume into a responsive detail view, orbit the binding, and drag through physically curved pages.

## Attribution

This project is forked from **[MengTo/complete-shelf](https://github.com/MengTo/complete-shelf)** — "An original single-file Three.js library of seven interactive clothbound hardcovers" by Meng To.

All credit for the original Three.js code, OrbitControls integration, book geometry, interaction state machine, page-turn physics, and overall architecture goes to the original author.

## Modifications

The following changes were made to the original `complete-shelf` codebase:

- **Removed hardcoded book data** — The `BOOKS` array (originally containing seven book definitions with titles, palettes, dimensions, chapters, etc.) has been emptied. Populate it with your own volume definitions.
- **Removed embedded cover atlas** — The `COVER_ATLAS_DATA` base64-encoded WebP image has been replaced with an empty string. Provide your own atlas image.
- **Removed embedded wood texture** — The `WOOD_TEXTURE_DATA` base64-encoded WebP image has been replaced with an empty string. Provide your own wood texture.
- **Removed embedded audio** — The `AUDIO_DATA` object (containing base64-encoded MP3 data for music and sound effects) has been emptied. Provide your own audio files or URLs.
- **Removed cover crop coordinates** — The `COVER_CROPS` array (defining atlas regions for each book cover) has been emptied. Define crops for your own atlas layout.

The Three.js engine code, OrbitControls integration, book geometry construction, interaction state machine, page-turn physics, shaders, materials, and all rendering logic are preserved from the original.

## Run locally

The page uses JavaScript modules, so serve it over HTTP:

```bash
python3 -m http.server 4173
```

Then visit http://localhost:4173.

No install or build step is required. An internet connection is needed for the pinned Three.js modules and Inter font.

## Project structure

```
books/
├── index.html   # Main experience (Three.js + OrbitControls)
├── PROMPT.md    # Original build prompt from complete-shelf
├── README.md    # This file
├── LICENSE      # MIT License (matching original project)
└── assets/
    └── complete-shelf-preview.jpg
```

## License

This project uses the MIT License, consistent with the original complete-shelf project. See [LICENSE](LICENSE) for details.

## Links

- **Original project:** [github.com/MengTo/complete-shelf](https://github.com/MengTo/complete-shelf)
- **Live demo (original):** [mengto.github.io/complete-shelf](https://mengto.github.io/complete-shelf/)
- **Three.js:** [threejs.org](https://threejs.org/)