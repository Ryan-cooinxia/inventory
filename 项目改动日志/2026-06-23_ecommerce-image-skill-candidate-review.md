# 2026-06-23 Ecommerce Image Skill Candidate Review Flow

## Goal

Integrate the local `ecommerce-image-maker` Skill into `G:\inventory` so Ozon image plans can generate and compare candidate ecommerce images from multiple image models, including image-2 and other OpenAI-compatible providers.

Core rule: generated images are only candidates. They do not become final listing images until the user clicks Select/Use on a candidate in the system UI.

## Implemented Changes

### 1. Candidate Image Model

File: `models.py`

Added `OzonImageCandidate` to store generated candidate images per image slot.

Main fields:

- `slot`, `draft`, `user`
- `provider`, `model_name`, `prompt_version`
- `prompt`, `negative_prompt`
- `image_url`, `local_path`
- `request_json`, `response_json`, `error_message`
- `status`: `generated`, `failed`, `selected`, `rejected`
- review scores and review notes

### 2. Database Table Registration

File: `app.py`

Added `OzonImageCandidate` to the startup table creation list.

### 3. Skill Prompt Builder

Files/directories:

- `services/ecommerce_image_skill.py`
- `ai/ecommerce-image-maker/`

Added a deterministic prompt builder based on the ecommerce image-making Skill.

It outputs:

- English image prompt
- negative prompt
- prompt version

Special Godox XPro / XPro-C protection rules were added for:

- pale blue LCD screen
- CH1 header
- A/B/C/D/E group rows
- TTL and M modes
- values such as 0.0, +0.3, 1/32, 1/64, 1/128
- purple-blue bottom menu strip, including CH/Zoom, SYNC, ALL, MOD
- MODE, RST, MENU, TCM labels
- lock icon, flash icon, SET dial
- side ON/OFF switches
- hot shoe mount
- XPro-C / XPro model marking

### 4. Image Generation Adapter

File: `services/image_generation.py`

Added a reusable image generation adapter.

It reads enabled `VisionModelConfig` records whose provider starts with `img_gen_`.

Supported behavior:

- OpenAI Images API style request
- image URL response
- base64 image response
- local file saving under `uploads/ai_generated/`

Example provider naming:

- `img_gen_openai`
- `img_gen_image2`
- `img_gen_dashscope`
- `img_gen_custom`

### 5. Ozon Routes

File: `blueprints/ozon.py`

Updated `/ozon/image-plan/<draft_id>` to load:

- image slots
- available image generation model configs
- candidate images grouped by slot

Updated `/ozon/image-plan/<draft_id>/generate` to:

- build prompts from the Skill
- generate candidates per selected model or all enabled models
- create `OzonImageCandidate` records
- save successful images locally when possible
- mark failures as candidate failures
- keep final slot image unchanged until user selection

Added `/ozon/image-candidate/<candidate_id>/select`:

- prevents failed candidates from being selected
- prevents empty candidates from being selected
- writes selected candidate image to the slot
- sets slot status to `approved`
- marks the selected candidate as `selected`

Added `/ozon/image-candidate/<candidate_id>/score`:

- saves manual score and review notes
- clamps score limits on the backend

Score limits:

- structure: 0-30
- detail: 0-25
- text: 0-15
- commercial: 0-20
- postprocess: 0-10

Added local AI image serving route:

- `/ozon/uploads/ai_generated/<filename>`

### 6. Ozon Image Plan UI

File: `templates/ozon/image_plan.html`

Added:

- model selector
- generate with selected model
- generate with all enabled `img_gen_` models
- candidate image list under each image slot
- candidate status display
- failure error display
- local save indicator
- scoring form
- review notes
- Select/Use candidate action

Important UI note added:

> Generated results enter candidate images first; only after clicking Select/Use will they become final approved images.

Final preview now prefers `slot.local_path` first, then falls back to `slot.generated_url`.

## Current Changed Files

```text
M  CLAUDE.md
M  app.py
M  blueprints/ozon.py
M  models.py
M  templates/ozon/image_plan.html
?? ai/
?? services/ecommerce_image_skill.py
?? services/image_generation.py
?? project change log directory
```

## Validation Done

Python syntax check passed:

```bash
G:\inventory\.venv\Scripts\python.exe -m py_compile G:\inventory\models.py G:\inventory\app.py G:\inventory\blueprints\ozon.py G:\inventory\services\ecommerce_image_skill.py G:\inventory\services\image_generation.py
```

Jinja template parse passed:

```text
template ok
```

Skill prompt smoke test passed:

```text
prompt version: ecommerce-image-maker-v0.2-ui-preservation
XPro guard: True
```

This confirms that Godox XPro-C screen/button/model-marking preservation rules are injected into the generated prompt.

## Not Tested Yet

Real image API calls were not executed.

Reason: this requires configured `img_gen_` model API keys and may consume model credits.

## Recommended Next Test

1. Add one enabled image model config with provider starting with `img_gen_`.
2. Open an Ozon draft image plan page.
3. Click Generate with selected model.
4. Confirm candidate image records appear.
5. Score one candidate.
6. Click Select/Use.
7. Confirm the slot becomes approved.
8. Add a second model config and test Generate with all models.

## Next Development Suggestions

1. Add a quick-create entry for `img_gen_` models on the model config page.
2. Add candidate comparison view.
3. Add Copy Prompt button for each candidate.
4. Add regenerate single slot / single model action.
5. Add candidate score sorting.
6. Add export for final 8 images and prompts.
7. Add pre-publish quality gate: block publish if required 8 images are not approved.
