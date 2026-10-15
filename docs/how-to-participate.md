Taking part in a challenge has three core parts: registration, making a submission, and reviewing your results. You can also ask questions and collaborate with the community through the challenge's discussion board.

---

## 1. Register

[Register](https://www.synapse.org/CM4AI_DREAM) for the challenge. By registering, you agree to abide by all [Challenge Rules](TODO:link).

In addition to challenge registration, sign in to [Terra](https://app.terra.bio/) with the Google account you will use for submission. Complete any registration, profile, or terms prompts.

<details>
<summary> 👥 Working in a Team? </summary>

> To enable team submissions, you must first register your group for the challenge: [TODO:link]
>
> You may also request to join an existing team that is already registered for this challenge. Visit the [Participants & Teams](TODO:link) page to view all registered teams.

</details>

## 2. Make a Submission

A submission to this challenge should include:

| Item | Required? |
|---|---|
| Task-only WDL file named `workflow.wdl` | Yes |
| Publicly accessible container image containing your model code | Yes |
| One weights file | Yes |
| Supporting files (configs, vocab files, etc.) | Optional |

You will submit using the [AnVIL DREAM participant CLI tool](https://github.com/cm4ai-anvil-dream-challenge/anvil-dream-participant), which will package these files into a private Synapse submission. Organizers will then run and evaluate your model on hidden data.

Follow the [Quickstart](https://github.com/cm4ai-anvil-dream-challenge/anvil-dream-participant/blob/main/docs/quickstart.md) for a full walkthrough, including:
* installing the CLI
* configuring authentication
* preparing your model
* building your container
* submitting to Synapse

## 3. Review Your Model's Performance

See how your model performs on the [Results](TODO:link) page. The [Evaluation](TODO:link) page explains how scores are calculated.

---

## Need help? Want to collaborate?

Use the [discussion board](https://www.synapse.org/Synapse:syn77349038/discussion/default) to connect with other participants!

You can also contact the Organizing Team by tagging `@CM4AIAnVILDREAMChallengeOrganizers` in your post.

For common errors, please check the [Troubleshooting](TODO:link) table in the Quickstart first.
