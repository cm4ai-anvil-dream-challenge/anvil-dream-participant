# Evaluation

## What we measure and why

Participants submit their model (code and weights), not images. We run each model on held-out test cells. For each cell it receives the nucleus, microtubule and ER channels and the amino-acid sequence of a protein, and it predicts how that protein would appear in that cell. We run the model several times per cell with independent random seeds and compare its predictions with the real image of the same protein in the same cell, which no participant has seen.

A useful model has to be right in several independent ways, so the evaluation asks four questions.

### 1. Does the model put the protein in the right place? *(Location)*

Proteins reside in specific compartments, such as the nucleus, mitochondria, the Golgi or the plasma membrane. This question asks whether the model places the signal in the same compartments as the real image of that cell. Getting location right is the most basic requirement of a useful prediction.

### 2. Does the model reproduce the protein's pattern? *(Arrangement)*

Two proteins can share a compartment and still look very different: diffuse versus punctate, filaments versus speckles, an even signal versus one concentrated at the cell edge. This question asks whether the model reproduces the pattern of *this* protein in *this* cell, beyond which compartments it occupies. It separates a model that has learned something from the protein's sequence from one that produces a generic "typical image for this compartment".

### 3. Does the model produce realistic microscopy images? *(Realism)*

A prediction can be in the right place yet look unlike real data: too smooth, too noisy, blotchy, or with the wrong brightness distribution. This question asks whether the model's predictions have the texture and fine structure of real KOLF immunofluorescence images.

### 4. Has the model learned how much a protein varies from cell to cell? *(Variability)*

The same protein never looks exactly the same in two cells. A good generative model should capture this natural variation rather than return one "average" answer. Running the model several times for the same cell shows whether its predictions are each close to the real cell *and* vary about as much as real cells of that protein do. A model that always produces the same safe, averaged image does poorly here. So does one whose predictions are diverse but wrong.

## How the scores are combined

- **Per cell.** Each prediction is compared with the real image of the same cell. The reference channels are identical for both, so only the predicted protein channel is judged.
- **Per protein, then overall.** Scores are averaged within each protein and then across proteins, so every protein counts equally.
- **Calibrated scale.** Each question is scaled so that 0 is what simple guesses achieve (for example, painting the expected compartment) and 1 is what another real cell of the same protein achieves.
- **Weighting.** The four questions are combined with fixed weights into one score. Per-question scores are reported as well, so you can see where a model is strong or weak.
- **Shortcuts.** The evaluation is designed so that shortcuts do not beat a genuine generative model. We tested it against look-up of similar real images, averaged images, patchworks of real image pieces and post-processing tricks.

*Detailed descriptions of each metric will be added soon..*
