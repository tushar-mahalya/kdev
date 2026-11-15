// Every command, with what a user is trying to do when they reach for it.
// Flags and examples follow the README's command reference.
export type Group = 'box' | 'files' | 'accounts' | 'setup';
export type Cmd = { cmd: string; what: string; group: Group; flags?: string[]; ex: string; intents: string[]; next?: boolean };

export const groups: { id: Group; t: string; d: string }[] = [
  { id: 'box', t: 'The box', d: 'Start, reach, watch and stop it' },
  { id: 'files', t: 'Files & history', d: 'What was saved, and getting it back' },
  { id: 'accounts', t: 'Accounts & workspace', d: 'Who pays, and the shared notebook' },
  { id: 'setup', t: 'Setup & health', d: 'Once per machine, and when something is off' },
];

export const intents: { id: string; t: string }[] = [
  { id: 'start', t: 'Start or reconnect' },
  { id: 'shell', t: 'Open a shell or VS Code' },
  { id: 'port', t: 'Reach a port on the box' },
  { id: 'watch', t: "See what's running" },
  { id: 'stop', t: 'Stop and save' },
  { id: 'files', t: 'Get my files back' },
  { id: 'quota', t: 'Switch who pays' },
  { id: 'share', t: 'Share or join the workspace' },
  { id: 'tune', t: 'Change GPU or hours' },
  { id: 'new', t: 'Set up a new machine' },
  { id: 'fix', t: 'Something looks wrong' },
];

export const daily = [
  { n: 'once', cmd: 'kdev setup', d: 'SSH key, cloudflared, Kaggle sign-in, workspace, tunnel. Only what is missing.' },
  { n: 'start', cmd: 'kdev up', d: 'Starts the box, or connects to the one already running.' },
  { n: 'work', cmd: 'ssh kaggle', d: 'Or VS Code → Remote-SSH → kaggle. Leave whenever you like.' },
  { n: 'optional', cmd: 'kdev down', d: 'Stop early and hand the quota back. Files are saved anyway.' },
];

export const commands: Cmd[] = [
  { cmd: 'kdev', what: 'The overview: the box, every account’s quota, the workspace, then a menu of what makes sense now.', group: 'box', ex: 'kdev', intents: ['start', 'watch'] },
  { cmd: 'kdev up', what: 'Start the box, or connect to the one already running from any account or machine.', group: 'box', flags: ['--gpu', '--hours', '--account', '--open', '--no-restore', '--replace', '-y'], ex: 'kdev up --gpu t4 --hours 9', intents: ['start', 'shell'] },
  { cmd: 'kdev ssh', what: "A shell on the box. Pass a command after --; ssh's exit code is kdev's.", group: 'box', flags: ['-- ARGS'], ex: 'kdev ssh -- python train.py', intents: ['shell'] },
  { cmd: 'kdev forward', what: 'Forward localhost ports to services on the box, with a clear error if one is taken or nothing listens.', group: 'box', flags: ['PORT', 'LOCAL:REMOTE'], ex: 'kdev forward 8888 9000:6006', intents: ['port'], next: true },
  { cmd: 'kdev status', what: 'Running? Who started it, when it ends, whether it answers, and the restore state.', group: 'box', flags: ['--json'], ex: 'kdev status --json', intents: ['watch', 'fix'] },
  { cmd: 'kdev logs', what: 'What the box itself is saying: the session log, without the kernel’s noise.', group: 'box', flags: ['-n N', '--all'], ex: 'kdev logs -n 50', intents: ['watch', 'fix'] },
  { cmd: 'kdev down', what: 'Stop the box now. It saves /kaggle/working however it stops; cancels as the starting account if it must.', group: 'box', flags: ['--account', '--session-id'], ex: 'kdev down', intents: ['stop'] },

  { cmd: 'kdev restore', what: 'Finish or repeat the restore on the running box, or add what an older version has. Never a rollback.', group: 'files', flags: ['--from vN', '--from-backup'], ex: 'kdev restore --from v12', intents: ['files', 'fix'] },
  { cmd: 'kdev workspace files', what: 'Exactly what a saved version holds: what the next kdev up would restore.', group: 'files', flags: ['--version vN', '--json'], ex: 'kdev workspace files --version v12', intents: ['files'] },
  { cmd: 'kdev history', what: 'Recent saved sessions: who ran them, how they ended, how many files, restore state.', group: 'files', flags: ['-n N', '--json'], ex: 'kdev history -n 5', intents: ['files', 'watch'], next: true },
  { cmd: 'kdev backup', what: 'An exact copy of the running box on this machine, kept in ~/.config/kdev/workspace.', group: 'files', ex: 'kdev backup', intents: ['files'] },

  { cmd: 'kdev account', what: 'Every signed-in account with its GPU and TPU hours left this week.', group: 'accounts', flags: ['--json'], ex: 'kdev account', intents: ['quota', 'watch'] },
  { cmd: 'kdev account use', what: 'Pick the account that runs, and pays for, the next box.', group: 'accounts', ex: 'kdev account use bob', intents: ['quota'] },
  { cmd: 'kdev account add', what: 'Sign an account in through the browser; run it again to refresh a token.', group: 'accounts', ex: 'kdev account add bob', intents: ['quota', 'new', 'fix'] },
  { cmd: 'kdev workspace', what: 'The shared notebook: owner, sharing, members and saved versions.', group: 'accounts', ex: 'kdev workspace', intents: ['share', 'watch'] },
  { cmd: 'kdev workspace create', what: 'Make the group notebook and share it as Can Edit. The owner runs this once.', group: 'accounts', flags: ['--group'], ex: 'kdev workspace create --group my-team', intents: ['share', 'new'] },
  { cmd: 'kdev workspace join', what: 'Use the group notebook someone already shared with you.', group: 'accounts', ex: 'kdev workspace join', intents: ['share', 'new'] },

  { cmd: 'kdev setup', what: 'Get this machine ready. Does only what is missing, so it is safe to run again.', group: 'setup', ex: 'kdev setup', intents: ['new'] },
  { cmd: 'kdev tunnel setup', what: 'Create the Cloudflare tunnel and its DNS record in your zone, once.', group: 'setup', ex: 'kdev tunnel setup', intents: ['new'] },
  { cmd: 'kdev config set', what: 'Change a setting: gpu, hours, git-remote, ssh-alias and the rest. kdev config lists them.', group: 'setup', flags: ['KEY VALUE'], ex: 'kdev config set gpu t4', intents: ['tune'] },
  { cmd: 'kdev doctor', what: 'Check every dependency, account token, the workspace and the tunnel DNS. Exits 1 if something is broken.', group: 'setup', flags: ['--fix'], ex: 'kdev doctor --fix', intents: ['fix'] },
];
