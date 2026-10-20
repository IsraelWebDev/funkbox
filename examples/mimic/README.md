# Demo: a 23-second vertical carpet-cleaner ad (fictional brand)

This example follows the structure of a typical social product ad:
- 12 quick cuts on the beat;
- red caption boxes that type on;
- the product in several shots;
- a short voiceover over a music bed;
- a split brand end card.

Everything in this folder is safe to publish. The brand **GLOWMOP** is invented.

![storyboard](../../docs/mimic-storyboard.png)

```bash
python -m funkbox reel storyboard examples/mimic/mimic.reel.json -o out/mimic_sb.png   # ~2 s
python -m funkbox reel render     examples/mimic/mimic.reel.json --name glowmop-mimic   # ~3.5 min on an Intel Iris Xe laptop
```

`preview.mp4` is a downscaled copy of the full render.

## Where each asset came from

| asset | made by | notes |
|---|---|---|
| `glowmop_bottle.png` | `make_product.py`: drawn procedurally (numpy + Pillow, bundled OFL fonts) | no image model, so the label text is exact |
| `music.mp3` | `make_music.py`: synthesized (numpy), 110 BPM | royalty-free by construction |
| `shots/*.jpg` | FastSD CPU (SD-Turbo) via `studio.generate_image`, then `studio.upscale_image` (2x, EDSR) | SD-Turbo output is under the Stability AI Community License (see `THIRD_PARTY_NOTICES.md`) |
| `copy.json` | written for this demo | every caption and voiceover line comes from it by id |
| voiceover | Piper `en_US-ljspeech-medium` at render time | needs `install.py --with reel` |

## What it shows, and what it can't do

- **It shows:**
  - beat-snapped cuts;
  - parallax and Ken Burns motion on stills;
  - exact typewriter captions;
  - product compositing with contact shadows;
  - voiceover ducking the music;
  - the end card;
  - the collision rule that keeps captions off the product (shot 12's caption had to move to the top).
- **It can't:** show real motion of people. The cleaner shots are stills with camera moves, because
  local AI video needs an NVIDIA GPU. A shot's `source` can be `{"video": "clip.mp4"}`, so clips from any
  video generator drop straight into the same shot list.
- **Prompting tip:** generate scenes at `square`. SD-Turbo is trained at 512x512, and tall sizes often
  duplicate the subject.

## The scene prompts

FastSD (SD-Turbo, 1 step) with the negative prompt `text, letters, watermark, logo, label, writing, blurry,
deformed, cartoon`, two seeds each. The first round was tall (512x896): `fibers`, `floor`, `stain` and `clean` come
from it. Tall frames often duplicated people and machines, so `room`, `machine`, `worker`, `wand` and `dog` were
generated again at `square` from the same subjects (the `-a`/`-b` pairs are two takes), and `room-warm` is a warmer
take on `room`. Every chosen image was then upscaled 2x with EDSR.

| shot | prompt |
|---|---|
| `fibers` | extreme macro photo of beige wool carpet fibers, loops of yarn, soft window light, shallow depth of field, photorealistic |
| `room` | bright modern living room with beige wall-to-wall carpet, white sofa, orange cushions, large windows, morning sunlight, wide angle interior photo |
| `machine` | red and black professional carpet extractor machine standing on beige carpet in a bright living room, low angle product photo, photorealistic |
| `wand-a`, `wand-b` | close-up of a carpet cleaning wand nozzle gliding over beige carpet, water extraction, living room background blurred, photorealistic |
| `worker-a`, `worker-b` | professional cleaner in maroon polo shirt cleaning a beige carpet with a wand in a bright living room, full body, photorealistic |
| `stain` | close-up of a coffee stain on a beige carpet, natural light, photorealistic |
| `clean` | freshly cleaned beige carpet with neat stripes, sunlight from window, living room floor, photorealistic |
| `dog` | golden retriever lying on a clean beige carpet in a sunny living room, photorealistic |
| `floor` | low angle view of beige carpet floor with blurred bright living room background, empty space in the foreground, photorealistic |

To Claude, the same thing is one request: *"generate these scenes, two seeds each, and upscale the best one of
each 2x"*, followed by the list.

## The shot list at a glance

| shots | caption (copy id) | voiceover | product in shot |
|---|---|---|---|
| 1-2 fibers, room | `hook` | yes | |
| 3-4 machine, floor | `meet` | yes | yes |
| 5-8 worker, wands | `care` | yes | |
| 9-10 stain, clean | `tough` | yes | |
| 11-12 warm room, dog | `gentle` | yes | shot 12 |
| end card (3.5 s) | `end_head`, `end_sub`, `url` | no | yes |

Cut lengths are `auto` (long enough for the voiceover line) or `beats:2`, so every cut lands on the music.
