# Device UI Preservation

Use this reference for products with screens, buttons, dials, printed legends, indicator icons, menus, app interfaces, measurement panels, or any readable control markings.

## Why This Matters

For electronic products, screen content and button legends are not decoration. They prove usability, compatibility, mode selection, and product authenticity. Treat them as product structure.

## Extract UI Details First

Before writing prompts, list:

- Screen shape, color, bezel, glow, and viewing angle
- Screen layout zones: header, status icons, data rows, bottom menu bar
- Visible screen text and symbols
- Button count, button shape, and exact button positions
- Printed labels next to buttons
- Icons near buttons: lock, flash, zoom, wireless, battery, speaker, channel, group
- Dial shape, ridges, center label, and surrounding marks
- Side switch labels and switch direction
- Product model marking and version code

## Prompt Rules for Screens

Use a dedicated screen line:

Preserve the screen UI as a camera flash trigger interface, not a generic smartphone display: pale blue LCD, thin black pixel-style characters, CH1 header, A/B/C/D/E group rows, TTL and M modes, power values such as 1/64 or 1/128, exposure compensation such as +0.3, small status icons along the top, and a purple-blue bottom menu strip with short labels like CH/Zoom, SYNC, Gr, MOD. Keep the screen flat inside the product bezel and aligned with the product perspective.

If text fidelity is critical, ask for one of these options:

- Generate with a realistic but simplified LCD UI, then replace the screen in post-production.
- Keep the screen slightly softened so imperfect tiny text is less noticeable.
- Create a separate clean screen UI overlay in a design tool and composite it onto the generated product.

## Prompt Rules for Buttons and Printed Labels

Use a dedicated button line:

Preserve the button system: five small rectangular side shortcut buttons on the left side of the screen, four horizontal oval function buttons directly below the screen, circular SET dial in the lower center with ridged outer ring, round function buttons around the dial, and printed legends near buttons including MODE, RST, MENU, TCM, lock icon, flash icon, and zoom/TCM style icon. Keep labels small, white, and printed on the black body; do not turn labels into random decorative text.

## Godox XPro UI Notes

For Godox XPro / XPro-C style flash trigger images, preserve these visual facts:

- Front screen is a pale blue LCD inside a black rectangular bezel.
- Typical header shows CH1.
- Main rows show groups A, B, C, D, E.
- Modes include TTL and M.
- Values may include 0.0, +0.3, 1/32, 1/64, 1/128.
- Bottom menu strip is purple-blue with short labels such as CH/Zoom, SYNC, ALL, MOD or CH/Zoom, SYNC, Gr, MOD.
- Lower controls include MODE, RST, MENU, TCM, a lock icon, a flash icon, and a circular SET dial.
- Side switch text can appear as ON/OFF style markings, but should remain small and aligned to the side switches.
- Model marking can appear as XPro-C or XPro, depending on the reference.

## Negative Constraints

Add these when generating close product images:

No smartphone-style app screen, no touchscreen icons, no colorful modern app UI, no random letters on the LCD, no missing CH1 header, no missing A/B/C/D/E rows, no wrong button count, no merged buttons, no extra buttons, no missing SET dial, no missing MODE/RST/MENU/TCM labels, no oversized body text printed on the product, no fake decorative symbols, no warped side switches.

## QA Checklist

Screen:

- LCD is inside the correct bezel and follows perspective.
- Screen looks like a flash trigger control interface.
- CH/group/mode/value/menu structure is recognizable.
- Tiny text is either readable or intentionally simplified/softened.

Buttons:

- Left side shortcut button row exists.
- Four oval buttons below the screen exist.
- SET dial exists with ridged ring.
- Round function buttons around the dial exist.
- MODE/RST/MENU/TCM and icons are placed near the correct controls.

Model and markings:

- XPro or XPro-C marking is not turned into random text.
- Side switch labels remain small and aligned.
- No extra brand/model claims are invented.
