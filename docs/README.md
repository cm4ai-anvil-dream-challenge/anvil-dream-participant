# Challenge and participant documentation

These pages were imported from the Synapse wiki on October 12, 2026.
They are the baseline for maintaining the documentation in this repository and later
mirroring changes to Synapse. This import did not update any Synapse page.

The wording is retained from Synapse. Wiki links, headings, and the banner were adapted
for GitHub; registration remains on Synapse. Existing code and deployment changes may
need to be reconciled with this imported documentation before the first reverse sync.
Page IDs, hierarchy, source timestamps, and checksums are recorded in
[synapse-pages.json](synapse-pages.json).

## Differences to reconcile before publishing back

- The imported submission instructions specify float32 TIFF predictions.
  The current adapter and organizer require uint8 PNG predictions.
- The imported CLI and benchmark examples use `dream_participant` imports.
  The current package uses `anvil_dream_participant`.
- The imported ProtiCelli guide predates the explicit ImageNormalizer and
  nucleus-reference output scaling implemented in the current adapter.

These differences are recorded here rather than silently changing the Synapse
content during the initial import. No reverse sync has run.

Organizer planning drafts and generated test results are maintained in the parent
repository under `organizer/docs/synapse-drafts/`. Reading a page with organizer
credentials does not establish that it is approved for public participant docs.

## Pages

| Page | Synapse source |
| --- | --- |
| [Challenge Overview](overview.md) | [Wiki 639954](https://www.synapse.org/Synapse:syn74338981/wiki/639954) |
| [Challenge Rules](challenge-rules.md) | [Wiki 642246](https://www.synapse.org/Synapse:syn74338981/wiki/642246) |
| [Motivation & Background](motivation-and-background.md) | [Wiki 640956](https://www.synapse.org/Synapse:syn74338981/wiki/640956) |
| [The Challenge](challenge.md) | [Wiki 642240](https://www.synapse.org/Synapse:syn74338981/wiki/642240) |
| [Participant Benchmark](benchmark.md) | [Wiki 643842](https://www.synapse.org/Synapse:syn74338981/wiki/643842) |
| [Participant CLI](cli.md) | [Wiki 643841](https://www.synapse.org/Synapse:syn74338981/wiki/643841) |
| [Participant Container Images](container-images.md) | [Wiki 643840](https://www.synapse.org/Synapse:syn74338981/wiki/643840) |
| [Participant Quickstart](quickstart.md) | [Wiki 643829](https://www.synapse.org/Synapse:syn74338981/wiki/643829) |
| [ProtiCelli Participant Example](proticelli.md) | [Wiki 643843](https://www.synapse.org/Synapse:syn74338981/wiki/643843) |
| [Reading List](reading-list.md) | [Wiki 643278](https://www.synapse.org/Synapse:syn74338981/wiki/643278) |
| [Submission Format](contract.md) | [Wiki 643828](https://www.synapse.org/Synapse:syn74338981/wiki/643828) |

[Participant package release instructions](participant-releases.md) are maintained in this repository and were not imported from Synapse.
