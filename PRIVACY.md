# Privacy

funkbox runs entirely on your computer. It has no server, no account and no telemetry.

- **What it reads:** the files you point it at (images, music, logos, copy decks) and its own settings
  (`funkbox.toml`, environment variables).
- **What it writes:** renders and project files in your funkbox folders (`out/`, `inputs/`, `projects/`,
  `inbox/`, or `~/funkbox-data/` for the plugin). `install.py --register` adds funkbox's entries to your Claude
  Desktop config after backing it up; `--unregister` removes them.
- **What it sends:** nothing. The only network access is downloading fixed open-source models and tools when you
  ask for them (`install.py --with ...`, `funkbox reel setup-models`), fonts you name (`"Rubik:700"`, from the Google
  Fonts repository) and the GeneralUser GS instrument bank the first time music is rendered (from its GitHub
  repository), all over plain HTTPS without credentials.
  FastSD and GIMP are local apps listening on localhost only.
- **Claude:** whatever you or Claude pass to funkbox's tools goes through your own Claude client, under your
  Claude account's terms. funkbox itself keeps no record of it beyond the files you asked it to make.

Questions: open an issue at https://github.com/IsraelWebDev/funkbox/issues.
