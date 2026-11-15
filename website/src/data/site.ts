export const site = {
  name: 'kdev',
  title: 'kdev: SSH and VS Code into a Kaggle GPU notebook, with files that persist',
  description:
    'kdev is a free, open-source CLI that turns a Kaggle notebook into a remote dev box: SSH or VS Code Remote-SSH into free T4, P100 or TPU machines, and get your files back on the next session, from any account or machine.',
  repo: 'https://github.com/tushar-mahalya/kdev',
  pypi: 'https://pypi.org/project/kdev-cli/',
  version: '0.1.4',
  install: 'uv tool install kdev-cli',
};

export const links = {
  issues: `${site.repo}/issues`,
  discussions: `${site.repo}/discussions`,
  security: `${site.repo}/blob/main/SECURITY.md`,
  license: `${site.repo}/blob/main/LICENSE`,
  contributing: `${site.repo}/blob/main/CONTRIBUTING.md`,
  goodFirst: `${site.repo}/labels/good%20first%20issue`,
  helpWanted: `${site.repo}/labels/help%20wanted`,
  releases: `${site.repo}/releases`,
  websiteSource: `${site.repo}/tree/main/website`,
};

// The CLI's own wordmark, copied from src/kdev/ui.py (_LOGO_ROWS) so the site
// and the terminal draw the same pixels. a = accent, h = highlight, d = blue,
// f = foreground. The first 16 columns are the terminal badge.
export const LOGO_ROWS = [
  ' hhhhhhhhhhhhhh     hh    hh  ffffff    ffffffff  ff    ff',
  'hh            hh    hh   hh   fffffff   ffffffff  ff    ff',
  'hh  aa        hh    aa  aa    ff   fff  ff        ff    ff',
  'hh   aa       hh    aaaaa     ff    ff  ffffff     ff  ff ',
  'aa   aa       aa    ddddd     ff    ff  ffffff     ff  ff ',
  'aa  aa  dddd  aa    dd  dd    ff   fff  ff          ffff  ',
  'aa            aa    dd   dd   fffffff   ffffffff    ffff  ',
  ' aaaaaaaaaaaaaa     dd    dd  ffffff    ffffffff     ff   ',
];
