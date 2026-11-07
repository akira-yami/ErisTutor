# Eris lip study

The portrait is an illustrative ImageGen preview. The PNG layers come from a separate ImageGen sprite sheet and are cropped to the same 1280 × 128 canvas, preserving the source alpha. Import both at the same origin for the closed pose. The upper stroke supplies the seam and the lower stroke supplies a soft shadow. Build opening keyforms with shared corner anchors and reuse the cavity, teeth and tongue ArtMeshes behind them.

## ImageGen prompts

Built-in ImageGen was used for both outputs.

### Portrait

Use case: precise-object-edit. Edit the first image, the exact uploaded Live2D Eris screenshot. The second image is an extreme closeup of her existing mouth. Return one copy of the portrait, same original crop and composition, with ONLY the small mouth repainted. Preserve every other feature, in particular eyes, hair, chin and nose. Exact same position and 62px approximate original mouth width at the source image scale. Keep original simple 2D cel anime drawing. Draw a very gently parted mouth with a fine upper lip seam and an independent soft barely-visible warm-peach short contour of the lower lip. Match the corner positions, narrow proportions and muted warm grey-brown of the input. Part the lips a tiny bit at center, about 3–4 pixels at the source scale, corners meet. A tiny muted desaturated interior tone in the gap only. The new line is delicate and lower contrast than the lashes. Match her face and art precisely; the mouth should occupy no more than one fifth of face width. No visible teeth, no tongue, no red rim, no lipstick, no Cupid's bow, no glossy skin, no new colors elsewhere, no annotations, no extra panels. Keep original Eris expression. This is an on-face concept preview for a subsequent rig.

### Transparent layer master

Use case: stylized-concept. Transparent two-row sprite master for a Live2D anime mouth. Reference 1, the tiny image, is the original mouth line; match its simple slightly smiling curvature. Reference 2 is the proposed on-face concept; match the understated two-tone lip treatment. Draw two flat ink brush marks on transparent canvas. Upper row only: an extremely thin warm gray brown smile line tapering at both tips, like a single stroke of a manga pen. Thickness no more than 2% of length. Lower row only: an extremely thin pale peach line that is slightly thicker at center and becomes invisible toward both ends, with gently feathered edges. Thickness no more than 2.5% of length. No patches of painted skin. Minimal flat 2D anime drawing with low contrast and absolutely no specular highlights, material rendering, noise, outlining, shading effects, dark outer edges, glows or red outlines. Center both strokes horizontally. Keep generous clear margin on all four sides and 180px completely empty space between rows. Each isolated line occupies about 70% of total canvas width, lower line is about 80% as long as upper line. True transparent background with clean alpha. Two strokes only, no labels.
