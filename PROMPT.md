# Build prompt

Use this prompt with Codex, Cursor, Claude Code, Aura Build, or another coding agent. It is intentionally implementation-aware but leaves room for a new visual identity.

```text
Create an original, premium Three.js experience called “The Complete Shelf.”

GOAL
Build a warm editorial 3D library where people browse a continuous shelf of seven hardcovers and inspect each volume in detail.

THE COLLECTION
Create seven distinct clothbound books focused on Codex, Claude Code, Cursor, Antigravity, Figma, Framer, and Xcode. Give every volume its own proportion, palette, abstract foil motif, subtitle, short editorial description, binding specification, and sample page content.

BROWSING
- Navigate the shelf with wheel, arrow keys, previous/next buttons, and position markers.
- Keep the center volume clearly selected.
- Use true single-click hit targets for the books. Do not make shelf navigation depend on drag gestures.
- Hide any visible wraparound jump when the continuous shelf loops.

DETAIL VIEW
- Move the selected book from its exact shelf pose into inspection without a discontinuity.
- Keep the book responsively positioned beside the editorial information panel.
- Support orbit, pan, and zoom on the background.
- Keep the book closed by default.
- On cover hover, crack the front board open slightly.
- On cover click or drag, open to the title page.
- Let readers drag pages in both directions. Use segmented page geometry so the active sheet bends, twists, and settles with a restrained cloth-like curve.
- At the beginning, let the user drag the cover closed.
- When returning to the shelf, close the pages and animate the book, camera, shelf, and view offset to exact deterministic endpoints before reparenting the model.

BOOK CRAFT
- Model separate front and back boards, a straight spine, hinges, shoulders, endpapers, page block, individual preview sheets, page-edge layering, headbands, bookmark, foil accents, and soft contact shadows.
- Keep the silhouette sharp and book-like. Avoid pill-shaped boards and an overly rounded spine.
- Use physically based materials with restrained roughness variation.
- Generate fine cloth weave, paper grain, page-edge lines, foil roughness, wood grain, and subtle normal maps procedurally.
- Give the front, spine, and back their own correctly oriented artwork. Back-cover text must never be mirrored.

ART DIRECTION
- Aim for warm editorial minimalism: confident typography, quiet negative space, controlled color, and soft studio lighting.
- Take inspiration from high-end contemporary book publishing without copying a real cover or publisher identity.
- Theme the background and information panel from the selected book while maintaining strong text contrast.
- Keep the shelf view minimal: no decorative frame and no large overlay copy.

ENGINEERING
- Deliver one self-contained index.html file with inline CSS and JavaScript.
- Use a pinned Three.js ES-module version and OrbitControls.
- Do not use Mint, Mint MCP, runtime MCP calls, trackers, or a backend.
- Make all interaction controls accessible by name and provide live status updates.
- Respect prefers-reduced-motion.
- Use a clear interaction state machine for shelf, opening, inspection, reading, and closing.
- Prefer time-based deterministic interpolation over frame-dependent lerp cutoffs. The first and final pose of every transition must match exactly.

VERIFICATION
- Run the page from a local HTTP server.
- Test shelf navigation with wheel, keys, buttons, and markers.
- Test a single click from the shelf into detail.
- Test hover, click, and drag opening.
- Drag forward and backward through multiple pages and confirm the committed page does not spring back.
- Drag the cover closed from the first page.
- Return to the shelf from both a closed and open book.
- Sample the first, middle, penultimate, and final animation frames to rule out jumps.
- Check desktop and narrow layouts.
- Finish with zero console errors or warnings.
```

## Remix directions

Change only one or two systems at a time so the material craft remains coherent:

- Replace the seven creative tools with architecture, cinema, typography, or music volumes.
- Move from cloth and foil to translucent resin, recycled paper, or technical manuals.
- Keep the model but redesign the shelf as a reading table, archive drawer, or museum plinth.
- Replace the editorial palette while retaining the deterministic motion and page physics.
