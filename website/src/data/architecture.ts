// The detailed architecture, as the source implements it. Every node names
// the module that owns it so a reader can go and check.

export type Lane = 'machine' | 'network' | 'kaggle' | 'box';
export type ArchNode = { id: string; lane: Lane; t: string; s: string; src?: string; d: string; facts?: string[] };
export type ArchEdge = { id: string; from: string; to: string; label?: string };
export type FlowStep = { e: string[]; t: string; d: string };
export type Flow = { id: string; t: string; cmd: string; lede: string; steps: FlowStep[] };

export const lanes: { id: Lane; t: string; s: string }[] = [
  { id: 'machine', t: 'Your machine', s: 'laptop, any OS kdev runs on' },
  { id: 'network', t: 'The network', s: 'Cloudflare and your git host' },
  { id: 'kaggle', t: 'Kaggle', s: 'api.kaggle.com and storage' },
  { id: 'box', t: 'The box', s: 'one notebook session' },
];

export const nodes: ArchNode[] = [
  { id: 'cli', lane: 'machine', t: 'kdev CLI', s: 'cli.py · commands/', src: 'src/kdev/cli.py', d: 'One error boundary around every command. Commands parse, call the logic modules (session, persistence, quota, notebook) and render through ui.py.', facts: ['Exit codes 0 · 1 · 2 · 130', '-v logs every API call with its timing'] },
  { id: 'local', lane: 'machine', t: '~/.config/kdev', s: 'config.json · creds/', src: 'src/kdev/config.py', d: 'Settings (mode 0600) and one Kaggle OAuth credential file per account. Tokens last 12 hours; kagglesdk refreshes them from the stored refresh token.', facts: ['KDEV_CONFIG_DIR moves it', 'workspace/ holds kdev backup'] },
  { id: 'sshcfg', lane: 'machine', t: '~/.ssh/config', s: 'Host kaggle', src: 'src/kdev/sshcfg.py', d: 'A managed block: HostName is the tunnel hostname, ProxyCommand is cloudflared access ssh. No pinned host key, because every session is a new container.', facts: ['ssh-alias setting renames it'] },
  { id: 'client', lane: 'machine', t: 'ssh · VS Code', s: 'rsync · port forwards', src: 'src/kdev/session.py', d: 'Anything that speaks ssh reaches the box by name. kdev itself uses ssh for the restore, the start check and kdev stop, and rsync for backups.' },

  { id: 'cfapi', lane: 'network', t: 'Cloudflare API', s: 'tunnel + DNS', src: 'src/kdev/tunnel.py', d: 'kdev tunnel setup creates a named tunnel and the CNAME for its hostname in your own zone, once. It never repoints a hostname that belongs to something else without asking.' },
  { id: 'edge', lane: 'network', t: 'Cloudflare edge', s: 'your stable hostname', d: 'Both sides dial out to it. Your ssh arrives through cloudflared access; the box arrives through its tunnel. No port is open on the box.' },
  { id: 'git', lane: 'network', t: 'Your git remote', s: 'optional', src: 'src/kdev/bootstrap.py', d: 'Set git-remote and the box clones it at boot, commits and pushes every 15 minutes, and once more at teardown. The one copy that survives a host failure.' },

  { id: 'kapi', lane: 'kaggle', t: 'Kaggle API', s: 'JSON-RPC · Bearer token', src: 'src/kdev/api.py', d: 'POST /v1/{service}/{Method}. kdev uses KernelsApiService (save, status, output, cancel, logs, quota), IamService (sharing) and GroupApiService (members).', facts: ['SaveKernel is never retried', '429 and 5xx retried with backoff'] },
  { id: 'nb', lane: 'kaggle', t: 'Shared notebook', s: 'group has Can Edit', src: 'src/kdev/notebook.py', d: "One account owns it and shares it with the Kaggle group. kdev's cell sits on top; your own cells stay below it, untouched, and never run.", facts: ['Quota is charged to whoever starts it'] },
  { id: 'versions', lane: 'kaggle', t: 'Saved versions', s: 'v1 · v2 · … vN', src: 'src/kdev/persistence.py', d: "When a Save & Run All session ends, however it ends, Kaggle keeps /kaggle/working as that version's output: regular files only, up to 20 GB.", facts: ['Listed with signed URLs, 500 a page'] },
  { id: 'logs', lane: 'kaggle', t: 'Session log', s: 'stdout of the cell', src: 'src/kdev/session.py', d: 'The box reports through its own log: KDEV_STAGE for the board, KDEV_READY with the host, KDEV_ALIVE as a heartbeat, KDEV_DONE at the end.' },

  { id: 'cell', lane: 'box', t: "kdev's cell", s: 'bootstrap.py', src: 'src/kdev/bootstrap.py', d: 'The whole session. It installs sshd, exports the notebook environment to every ssh session, clones the repo, starts the tunnel, then holds until the deadline, a stop request or SIGTERM.', facts: ['Blanks later cells at the end'] },
  { id: 'sshd', lane: 'box', t: 'sshd + cloudflared', s: 'keys only · dials out', d: 'Password login off. Accepts the starting machine\'s key and every key in .kdev/authorized_keys. cloudflared dials out to the edge with the workspace\'s tunnel credentials.' },
  { id: 'boxpy', lane: 'box', t: 'kdev_box.py', s: 'standard library only', src: 'src/kdev/kdev_box.py', d: 'Shipped inside the cell. Restores files with 16 workers and 3 attempts each, writes to a part file and renames, never overwrites a file the session wrote, and records metadata every 30 seconds.' },
  { id: 'work', lane: 'box', t: '/kaggle/working', s: '.kdev/ state · meta · fetched', d: 'Your files, plus .kdev/state.json (the plan, written before anything slow), meta.json (symlinks, empty dirs, exec bits) and fetched (what the restore delivered).', facts: ['GPU: T4 ×2, P100, TPU…'] },
];

export const edges: ArchEdge[] = [
  { id: 'cli-local', from: 'cli', to: 'local' },
  { id: 'cli-kapi', from: 'cli', to: 'kapi', label: 'HTTPS' },
  { id: 'cli-cfapi', from: 'cli', to: 'cfapi', label: 'tunnel setup' },
  { id: 'cli-sshcfg', from: 'cli', to: 'sshcfg' },
  { id: 'kapi-nb', from: 'kapi', to: 'nb', label: 'SaveKernel' },
  { id: 'nb-cell', from: 'nb', to: 'cell', label: 'Save & Run All' },
  { id: 'cell-sshd', from: 'cell', to: 'sshd' },
  { id: 'cell-boxpy', from: 'cell', to: 'boxpy' },
  { id: 'cell-logs', from: 'cell', to: 'logs', label: 'KDEV_*' },
  { id: 'logs-cli', from: 'logs', to: 'cli', label: 'stream' },
  { id: 'sshd-edge', from: 'sshd', to: 'edge', label: 'outbound tunnel' },
  { id: 'client-edge', from: 'client', to: 'edge', label: 'ssh kaggle' },
  { id: 'sshcfg-client', from: 'sshcfg', to: 'client' },
  { id: 'boxpy-work', from: 'boxpy', to: 'work' },
  { id: 'versions-boxpy', from: 'versions', to: 'boxpy', label: 'signed URLs' },
  { id: 'work-versions', from: 'work', to: 'versions', label: 'kept at end' },
  { id: 'kapi-versions', from: 'kapi', to: 'versions' },
  { id: 'cell-git', from: 'cell', to: 'git', label: 'push /15 min' },
  { id: 'cfapi-edge', from: 'cfapi', to: 'edge' },
];

export const flows: Flow[] = [
  {
    id: 'up', t: 'Start', cmd: 'kdev up',
    lede: 'From a command to a shell, with one session even when several machines ask at once.',
    steps: [
      { e: ['cli-local'], t: 'Load settings and a fresh token', d: 'The active account, accelerator and hours come from config; kagglesdk refreshes the OAuth token if it has expired.' },
      { e: ['cli-kapi'], t: 'Is a box already running?', d: 'GetKernelSessionStatus on the shared notebook. A live box from any account or machine means connect, not start.' },
      { e: ['cli-kapi', 'kapi-versions'], t: 'Pick what to build on', d: 'Walk back from the latest version past empty, source-only saves. A session cut short before its restore is stacked on its own base layers.' },
      { e: ['cli-kapi', 'kapi-nb'], t: 'Save & Run All', d: "SaveKernel uploads the notebook with kdev's cell on top, the accelerator and the timeout. Sent once: a lost response never creates a second run." },
      { e: ['nb-cell'], t: 'Kaggle starts the container', d: 'On the quota of the account that asked. Waiting for a machine shows as its own step, so a queue looks like a queue.' },
      { e: ['cell-boxpy', 'boxpy-work'], t: 'Record the plan first', d: 'kdev_box.py writes .kdev/state.json before anything slow, so even a session killed during apt-get tells the next one what it should have held.' },
      { e: ['cell-sshd', 'sshd-edge'], t: 'Become a server', d: 'sshd with your keys, the notebook environment in every session, the repo cloned, cloudflared dialling out to the edge.' },
      { e: ['cell-logs', 'logs-cli'], t: 'KDEV_READY', d: 'The CLI reads the hostname from the session log, writes the ssh config block, and verifies the session over ssh.' },
    ],
  },
  {
    id: 'restore', t: 'Restore', cmd: 'kdev restore',
    lede: 'The box fetches its own files straight from Kaggle; your laptop only sends the plan.',
    steps: [
      { e: ['cli-kapi', 'kapi-versions'], t: 'Fresh signed URLs', d: 'ListKernelSessionOutput for every layer in the plan, newest copy of each name wins.' },
      { e: ['client-edge', 'sshd-edge', 'cell-boxpy'], t: 'Send the plan over ssh', d: 'The laptop can sleep after this; the transfer never touches it.' },
      { e: ['versions-boxpy', 'boxpy-work'], t: 'Download on the box', d: '16 parallel workers, 3 attempts each, part file then rename. A file written in this session is never overwritten.' },
      { e: ['boxpy-work'], t: 'Put back what Kaggle drops', d: 'Symlinks, empty directories and exec bits from meta.json, so a virtualenv works again. Each delivered file is logged in .kdev/fetched.' },
    ],
  },
  {
    id: 'connect', t: 'Connect', cmd: 'ssh kaggle',
    lede: 'Nothing listens on a public port. Both ends dial out to Cloudflare and meet at your hostname.',
    steps: [
      { e: ['sshcfg-client'], t: 'Host kaggle', d: 'ssh and VS Code read the managed block: the hostname, user root, and cloudflared access ssh as ProxyCommand.' },
      { e: ['client-edge'], t: 'Into the edge', d: 'cloudflared on your machine carries the ssh stream to Cloudflare by hostname.' },
      { e: ['sshd-edge'], t: 'Out from the box', d: 'Only a box holding the workspace tunnel credentials can answer on that hostname.' },
      { e: ['cell-sshd'], t: 'A full environment', d: 'CUDA, Python and Kaggle variables arrive through ~/.ssh/environment. Interactive shells get all of it; non-interactive sessions can currently lose PATH (a known issue).' },
    ],
  },
  {
    id: 'running', t: 'While it runs', cmd: '',
    lede: 'The cell keeps a heartbeat and keeps your work recoverable at all times.',
    steps: [
      { e: ['boxpy-work'], t: 'Metadata every 30 s', d: 'Symlinks, empty dirs and exec bits into .kdev/meta.json. A hard kill costs at most 30 seconds of metadata, never file contents.' },
      { e: ['cell-git'], t: 'Push every 15 minutes', d: 'With a git-remote set: add, commit and push, so a host failure at hour seven does not cost the day.' },
      { e: ['cell-logs', 'logs-cli'], t: 'KDEV_ALIVE', d: 'A heartbeat with the seconds left, so the CLI tells idle from dead. Warnings reach every shell at 15 and 5 minutes (next release).' },
    ],
  },
  {
    id: 'save', t: 'Stop & save', cmd: 'kdev down',
    lede: 'However the session ends, Kaggle keeps /kaggle/working as that version.',
    steps: [
      { e: ['client-edge', 'sshd-edge', 'cell-sshd'], t: 'Ask the box to stop', d: 'kdev down writes a stop file over ssh. If the box does not answer, it cancels the session as the account that started it.' },
      { e: ['boxpy-work', 'cell-git'], t: 'Last metadata, last push', d: 'The teardown records metadata once more and pushes the repo, then stops the tunnel.' },
      { e: ['cell-logs'], t: 'KDEV_DONE, nothing below runs', d: 'Later cells are blanked for this run, so a training cell cannot start after kdev down and spend quota. The version ends COMPLETE.' },
      { e: ['work-versions'], t: 'Kaggle saves the output', d: 'About 2 minutes per GB. A kdev up straight away waits for this save, then builds on it.' },
    ],
  },
];
