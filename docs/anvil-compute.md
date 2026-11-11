AnVIL is NHGRI's Genomic Data Science Analysis, Visualization, and Informatics Lab-Space [Schatz MC, Philippakis AA, Afgan E, Banks E, Carey VJ, Carroll RJ, et al. Inverting the model of genomics data sharing with the NHGRI Genomic Data Science Analysis, Visualization, and Informatics Lab-space (AnVIL). Cell Genomics. 2022;2. doi:10.1016/j.xgen.2021.100085].

### [What is AnVIL](https://anvilproject.org/overview)

### [Getting started on AnVIL computing](https://anvilproject.org/learn/get-started)


# Evaluation target environment

Organizer testing uses a standardized Google Cloud environment with one NVIDIA
A100 with 40 GB of GPU memory per prediction worker. Your model must run within
one worker's resources. The organizer controls parallel execution across workers.

## Hardware specifications

- Machine type: `a2-highgpu-1g`
- Compute: 12 vCPUs
- System memory: 85 GB RAM
- GPU: one NVIDIA A100 with 40 GB of GPU memory
- Task disk: 75 GB SSD

## Deployment and optimization guidelines

- Keep model and batch allocations within the 40 GB GPU memory limit. Leave room for framework and temporary allocations.
- Use the available CPUs for data loading, preprocessing, and postprocessing to keep the GPU busy.
- Keep system memory use below the 85 GB limit.
- Use mixed precision if your model supports it. Verify that prediction quality remains acceptable.

The organizer sets execution resources and timeouts. Your submitted WDL must
specify only the container image in its runtime block. See the
[submission contract](contract.md) for the fixed WDL interface.
