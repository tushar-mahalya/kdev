// Measured on a real kdev T4 box and from `kdev account --json` on
// 2026-11-15. Kaggle changes its machines; re-measure before editing.
export const quota = { gpuHours: 30, tpuHours: 20 };
export const measuredOn = '15 Nov 2026';

export const machine = {
  gpus: [{ name: 'Tesla T4', vram: 15 }, { name: 'Tesla T4', vram: 15 }],
  cpu: 4,
  ram: 31,
  saved: 20,
  // Free space on the box's root overlay when measured; shared, not an allocation, never saved.
  scratch: 960,
  os: 'Ubuntu 22.04',
  cuda: 'CUDA 12.8',
  packages: 934,
};

export const stack = [
  { icon: 'python', t: 'Python', v: '3.12' },
  { icon: 'pytorch', t: 'PyTorch', v: '2.10' },
  { icon: 'tensorflow', t: 'TensorFlow', v: '2.20' },
  { icon: 'jax', t: 'JAX', v: '0.7' },
  { icon: 'hugging-face', t: 'Transformers', v: '5.0' },
  { icon: 'scikitlearn', t: 'scikit-learn', v: '1.6' },
  { icon: 'pandas-icon', t: 'pandas', v: '2.3' },
  { icon: 'numpy', t: 'NumPy', v: '2.0' },
  { icon: 'jupyter', t: 'Jupyter', v: '' },
  { icon: 'uv', t: 'uv', v: '' },
];

export const accelerators = [
  { k: 'none', t: 'CPU', logo: '', hours: '12h sessions', quota: 'uses no GPU quota' },
  { k: 't4', t: 'T4 ×2', logo: 'nvidia', hours: '12h sessions', quota: `${quota.gpuHours} GPU-hours a week` },
  { k: 'p100', t: 'P100', logo: 'nvidia', hours: '12h sessions', quota: `${quota.gpuHours} GPU-hours a week` },
  { k: 'tpu', t: 'TPU', logo: 'google-cloud', hours: '9h sessions', quota: `${quota.tpuHours} TPU-hours a week` },
];
