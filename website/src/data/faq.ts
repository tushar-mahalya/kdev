// Questions people actually type into a search box or a chat assistant.
// Every answer is checked against the code or a measurement.
export const faq = [
  {
    q: 'How do I SSH into a Kaggle notebook?',
    a: 'Install kdev (uv tool install kdev-cli), run kdev setup once, then kdev up. kdev submits your notebook with a small cell on top that starts an SSH server and a Cloudflare tunnel, then writes a host called kaggle into ~/.ssh/config. After that, ssh kaggle gives you a root shell on the Kaggle machine.',
  },
  {
    q: 'Can I use VS Code with a free Kaggle GPU?',
    a: 'Yes. Once kdev up reports ready, open VS Code, choose Remote-SSH and connect to the host kaggle. Extensions, the terminal and the debugger run on the Kaggle box with its GPUs, CUDA and Python environment.',
  },
  {
    q: 'How do I keep my files between Kaggle sessions?',
    a: 'Kaggle saves /kaggle/working as the output of every Save & Run All session, however it ends. kdev finds the version you left off at and restores it into the next box before you connect, including the symlinks, empty folders and executable bits Kaggle drops, so a virtualenv keeps working.',
  },
  {
    q: 'What hardware does a kdev box get?',
    a: 'Whatever Kaggle gives the account: CPU, two NVIDIA T4s, a P100 or a TPU, and L4, A100 or H100 where an account has them. A T4 box we measured had 2 × 15 GB of VRAM, 31 GB of RAM, 4 vCPUs, Python 3.12, CUDA 12.8 and over 900 preinstalled Python packages. Each account gets 30 GPU-hours and 20 TPU-hours a week; sessions last up to 12 hours, 9 on TPU.',
  },
  {
    q: 'Can several Kaggle accounts share one dev box?',
    a: 'Every account in a Kaggle group can run the same shared notebook, each on its own weekly quota. When the active account runs short, kdev up shows every account’s remaining hours and asks which one should run the box. Only real, distinct people’s accounts should be used: Kaggle allows one account per person.',
  },
  {
    q: 'Do I need my own domain?',
    a: 'No. Without a domain kdev uses a Cloudflare quick tunnel with a new hostname each session, and kdev up finds it for you. With a domain on Cloudflare’s free plan, kdev tunnel setup creates a named tunnel once and the hostname never changes.',
  },
  {
    q: 'Is kdev free, and is it official?',
    a: 'kdev is free and open source under the MIT licence. It is an independent project for research and education, not affiliated with or endorsed by Kaggle, Google or Cloudflare. Kaggle restricts tunnelling out of notebooks; read its terms before you use kdev.',
  },
  {
    q: 'What happens if my laptop sleeps or the Wi-Fi drops?',
    a: 'Nothing ends. The box keeps running on Kaggle until its time is up. Run kdev up again from any machine or account in the group and it connects to the running box instead of starting a second one.',
  },
];
