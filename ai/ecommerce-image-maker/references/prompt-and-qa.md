# Prompt and QA Templates

Use this file when writing final prompts or reviewing generated ecommerce images.

## Prompt Formula

Write prompts in this order:

1. Product preservation
2. Image objective
3. Composition
4. Scene
5. Lighting and material
6. Text or layout
7. Style constraints
8. Negative constraints

## Product Preservation Line

Use a strong preservation line at the beginning of every prompt:

Keep the exact product structure from the reference image: same silhouette, proportions, component count, component positions, material split lines, color blocking, buttons, ports, handles, tanks, feet, contact points, and visible functional parts. Do not redesign, simplify, or add parts.

## Device UI Preservation Line

For products with screens, button labels, icons, or printed markings, add a second preservation line:

Preserve all visible control UI details as product structure: screen layout, screen color, menu rows, button count, button shape, printed button legends, icons, dial labels, switch labels, and model markings. Do not convert the screen into a generic app UI. Do not turn button labels into random decorative text.

## General Ecommerce Prompt Template

Create a realistic cross-border ecommerce product image for [product category].

Keep the exact product structure from the reference image: same silhouette, proportions, component count, component positions, material split lines, color blocking, buttons, ports, handles, tanks, feet, contact points, and visible functional parts. Do not redesign, simplify, or add parts.

Objective: communicate [single selling point].

Composition: [main product placement], [camera angle], [close-up or full product], clear hierarchy, enough clean space for concise ecommerce copy.

Scene: [buyer-relevant environment], with props limited to [prop list]. Props must support scale and usage, not compete with the product.

Lighting and material: realistic commercial lighting, accurate material rendering, clean shadows, high-end but believable product photography.

Text/layout: [headline], [2-4 short labels], simple icons if needed. Keep text minimal and readable.

Style: premium Amazon A+ detail page, clean cross-border ecommerce layout, realistic, sharp, coherent color palette.

Negative constraints: no product deformation, no extra parts, no missing parts, no changed button count, no wrong dimensions, no fake certifications, no unreadable text, no cluttered background, no exaggerated props.

## Coffee Machine Example Image Set

Use this example as a pattern, not as a fixed template.

1. Hero: espresso coffee maker on bright kitchen countertop, headline "Espresso Coffee Maker", 3 benefits.
2. Icon strip: 20 bar pressure, 1350W power, removable water tank, steam wand, stainless steel boiler.
3. Detail grid: brushed panel, adjustable steam wand, portafilter, removable drip tray, detachable water tank.
4. Performance: transparent-style extraction/heating visual, pressure and rapid heating claims.
5. Drink variety: espresso, Americano, cappuccino, latte, flat white.
6. Milk frothing: close-up of steam wand frothing milk.
7. Compact kitchen: dimensions and countertop footprint.
8. Scene fit: home kitchen, office, apartment, coffee corner.
9. Closing: warm lifestyle scene with product and drink.

## Revision Prompt Template

Revise the previous image while preserving the approved composition.

Fix only these issues: [issue list].

Keep unchanged: product structure, product angle, lighting style, background scene, main selling point, and layout hierarchy.

Strictly avoid: [specific errors from QA].

## QA Checklist

Product accuracy:

- Same product silhouette as reference
- Same component count
- Same component positions
- No invented buttons, ports, tanks, attachments, locks, handles, feet, or accessories
- No missing functional parts
- Same material and color split

Commercial clarity:

- One image communicates one main selling point
- Product is the visual focus
- Copy is short and readable
- Icons match the claims
- Props support scale or use case

Parameter consistency:

- Size, power, capacity, load, pressure, voltage, compatibility, and material claims match the user's data
- Unknown numbers are not invented
- Numbers are repeated consistently across modules

Visual quality:

- No warped geometry
- No broken hands or unrealistic user interaction
- No cluttered background
- Lighting is believable
- Cropping leaves the product complete unless the image is an intentional close-up

Text risk:

- If AI-generated text is misspelled or distorted, regenerate without text and add exact text later in a design tool.
- For marketplace images with strict compliance, use minimal embedded text and verify policy separately.
