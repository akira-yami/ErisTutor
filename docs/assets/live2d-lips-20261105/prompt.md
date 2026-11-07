# ImageGen tích hợp: nét môi

Built-in image_gen edit; transparent_background=true. Input: lip-contour-reference.png. Output: lips-generated.png. Dùng sắc độ nét vẽ mới, chuyển qua độ phủ nét gốc để giữ đăng ký contour trên mesh; không đưa shape miếng da AI trực tiếp vào atlas.

Prompt nguyên văn:

Use case: precise-object-edit. Production Live2D lip-cover texture retouch. The reference contains TWO separate pale skin covers with a small grey curved mouth line, upper lip on the left and lower lip on the right. Retouch these two existing sprites only. Preserve their exact positions, scale, skin-cover silhouette, curve position, skin RGB #fcf6f2, and empty padding. Replace each fuzzy grey curved stroke with a slender clean restrained 2D anime ink line in muted warm dark brown #76565a, with lighter antialiasing, tapering naturally at the corners. The new line follows the exact same centerline as the old grey stroke. Remove the fuzzy grey halo. Keep the upper lip quiet and understated; no extra highlights or contours. Skin stays a completely flat pale swatch. Do not draw realistic lips, lip volume, lipstick, thick borders, teeth, tongue, nostrils, a face, new mouth shapes or any additional lines. Remove labels and make background genuinely transparent. Flat matte raster asset with clean anime linework, no wet shine. Preserve layout exactly.
