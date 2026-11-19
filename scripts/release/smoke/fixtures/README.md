# Release smoke fixtures

- `smoke-document.txt` — a short plain-text document containing the unique phrase
  `lumogis release smoke marmalade lighthouse`. The smoke uploads it, then searches for
  `marmalade lighthouse` and requires a hit whose file name is `smoke-document.txt`.
  Plain text avoids the OCR and PDF paths.
- `smoke-voice.wav` — 16 kHz mono 16-bit PCM, under 5 s, saying
  "The quick brown fox jumps over the lazy dog." The `VOICE` check transcribes it and
  needs at least 7 of the 9 words.

Both are original works generated for this repository and are licensed
AGPL-3.0-only like the rest of the tree. Synthesised speech from espeak-ng / piper is
not covered by those programs' licences.

## Regenerating `smoke-voice.wav`

```sh
docker run --rm -v "$PWD:/out" debian:stable-slim sh -c \
  'apt-get update -qq && apt-get install -y -qq espeak-ng sox >/dev/null \
   && espeak-ng -s 140 -w /tmp/raw.wav "The quick brown fox jumps over the lazy dog." \
   && sox /tmp/raw.wav -r 16000 -c 1 -b 16 /out/smoke-voice.wav'
```

If espeak output transcribes below the threshold, regenerate with `piper` in a throwaway
container, same sentence, and note it here.

Check it against the engine the release ships (Speaches 0.8.3, model
`Systran/faster-whisper-base.en`) before committing a new file.
