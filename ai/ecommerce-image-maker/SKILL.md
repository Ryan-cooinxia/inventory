---
name: ecommerce-image-maker
description: Create AI ecommerce product image workflows for cross-border listings and A+ detail pages. Use when the user provides product photos, white-background product images, selling points, marketplace requirements, or asks for ecommerce image prompts, product-image planning, A+ page modules, buyer-question breakdowns, image QA, or a reusable image-generation workflow.
---

# Ecommerce Image Maker

Use this skill to turn product photos and selling points into a structured ecommerce image set plan: product understanding, buyer questions, selling point groups, image modules, generation prompts, negative constraints, and QA checks.

## Core Principle

Treat image generation as a workflow, not a one-off prompt.

Always protect the product structure first. AI may change lighting, scene, camera angle, layout, background, props, and visual style, but it must not change the product's shape, proportions, component count, component positions, material, key color, functional contact points, or stated parameters.

## Workflow

1. Inspect the input product image or product description.
2. Extract product structure, core parts, material, function, dimensions, and usage scenes.
3. Convert likely buyer doubts into image topics.
4. Group selling points into clear visual themes.
5. Build an image set with one main idea per image.
6. Write generation prompts with product-preservation constraints.
7. Add a QA checklist for structure, text, parameters, realism, and commercial usability.

Read `references/workflow.md` when the request needs full planning logic or the user is creating a repeatable process.

Read `references/modules.md` when the user asks for A+ detail pages, listing image sets, Amazon-style modules, or multiple secondary images.

Read `references/prompt-and-qa.md` when writing final prompts, negative prompts, revision prompts, or quality checks.

Read `references/device-ui-preservation.md` when the product has a screen, dial, keypad, control labels, icons, app interface, instrument panel, or any readable UI markings.

## Default Output

When the user asks for an ecommerce image plan, output these sections in order:

1. Product Understanding
2. Buyer Doubts
3. Selling Point Groups
4. Image Set Plan
5. Prompt Pack
6. Negative Constraints
7. QA Checklist
8. Recommended Next Iteration

Keep each image focused on one core message. If the user wants a full detail page, plan the modules from top to bottom.

## Product Understanding Rules

Identify:

- Product category and target user
- Visible structure and non-changeable parts
- Functional parts and user interaction points
- Material, finish, color, and texture
- Important parameters such as size, capacity, power, load, voltage, compatibility, or safety rating
- Likely use scenes and buyer concerns

If a product image is unclear, say what is uncertain and ask for the missing information only when it affects product accuracy.

## Selling Point Rules

Group selling points by buyer logic, not by random features:

- Performance: power, speed, pressure, heating, capacity, strength
- Safety and reliability: stability, material, protection, certifications
- Convenience: removable, washable, foldable, adjustable, compact
- Scene fit: kitchen, office, apartment, outdoor, travel, family, pet use
- Detail proof: close-ups, parts, texture, ports, locks, handles, buttons
- Size and compatibility: dimensions, fit, storage, applicable people/items

Avoid putting too many points in one image. Prefer one image equals one promise.

## Prompt Rules

For each image prompt, include:

- Product preservation line
- UI preservation line for screens, button labels, icons, and markings when present
- Scene and composition
- Camera angle and focal length feel
- Lighting and material rendering
- Props and environment
- Text/layout instructions if needed
- Negative constraints

If the output image model struggles with text accuracy, recommend generating clean image areas first and adding exact text in a design tool afterward.

## QA Gate

Before final delivery, check:

- Product structure did not change
- Key components are present and in the right places
- Proportions and dimensions are plausible
- Parameters are consistent across all images
- Text is minimal, readable, and not hallucinated
- Each image has a single clear selling point
- Background and props support the product instead of stealing attention
- The set feels like one coherent cross-border ecommerce listing

If any high-risk issue appears, provide a revision prompt instead of accepting the image.
