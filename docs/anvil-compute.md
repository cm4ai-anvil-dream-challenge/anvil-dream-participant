AnVIL is NHGRI's Genomic Data Science Analysis, Visualization, and Informatics Lab-Space [Schatz MC, Philippakis AA, Afgan E, Banks E, Carey VJ, Carroll RJ, et al. Inverting the model of genomics data sharing with the NHGRI Genomic Data Science Analysis, Visualization, and Informatics Lab-space (AnVIL). Cell Genomics. 2022;2. doi:10.1016/j.xgen.2021.100085].

### [What is AnVIL](https://anvilproject.org/overview)

### [Getting started on AnVIL computing](https://anvilproject.org/learn/get-started)


# Evaluation Target Environment
All submissions will be evaluated on a standardized Google Cloud Platform (GCP) g2-standard-8 instance (or equivalent). To ensure fairness and consistent benchmarking, your code must execute within the constraints of this single-node environment.
### Hardware Specifications
- Compute (vCPU): 8 vCPUs (Intel Xeon / Cascade Lake)
- System Memory: 32 GB RAM
- Accelerator (GPU): 1 × NVIDIA L4 GPU (24 GB GDDR6 VRAM, Ada Lovelace architecture)
- Network Bandwidth: Up to 16 Gbps
### Deployment & Optimization Guidelines
- VRAM Allocation: Ensure your models and batch sizes are optimized for a 24 GB VRAM ceiling. Exceeding this will result in Out-of-Memory (OOM) errors.
CPU Multi-threading: Workloads should be designed to leverage up to 8 vCPUs. Ensure your data pipelines, preprocessing, and post-processing do not bottleneck the GPU.
- System RAM: Keep your memory footprint safely under 32 GB to avoid system thrashing or process termination by the OOM killer.
- Precision: The NVIDIA L4 architecture features fourth-generation Tensor Cores. We highly encourage utilizing FP16 / BF16 mixed-precision or INT8 quantization to maximize throughput.
