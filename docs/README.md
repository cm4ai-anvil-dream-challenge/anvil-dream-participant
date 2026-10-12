# Challenge and participant documentation

These pages were imported from the Synapse wiki on October 12, 2026.
They are the baseline for maintaining the documentation in this repository and later
mirroring changes to Synapse. This import did not update any Synapse page.

The guides have been updated to match the current package: Python imports use
`anvil_dream_participant`, predictions use uint8 PNGs, and the ProtiCelli guide
describes explicit input normalization and nucleus-reference output scaling.
Synapse retains its original content. Review these changes before any future
sync back to Synapse.

Page IDs, hierarchy, source timestamps, and checksums are recorded in
[synapse-pages.json](synapse-pages.json). Source checksums describe the original
import; document checksums describe the current repository pages.

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
