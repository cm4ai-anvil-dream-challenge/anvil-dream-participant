# Local evaluation

Use the separate [cm4ai-evaluate package](https://github.com/cm4ai-anvil-dream-challenge/cm4ai-evaluate)
to compare predicted protein images with your own ground-truth images. Its README describes
installation, manifest preparation, available metrics, and output files.

The evaluator reports raw diagnostic metrics. It does not reproduce the official combined
challenge score, which uses held-out data in the organizer environment.

`anvil-dream-participant` handles model submissions and submission status. It contains no
benchmarking or scoring code. The agreed submission archive and WDL format are unchanged.
