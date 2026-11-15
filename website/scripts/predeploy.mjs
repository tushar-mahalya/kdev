// Refuse to deploy a build whose canonical URLs point at localhost.
if (!process.env.SITE_URL || /localhost|127\.0\.0\.1/.test(process.env.SITE_URL)) {
  console.error('Set SITE_URL to the public origin first, e.g.\n  SITE_URL=https://kdev-cli.pages.dev npm run deploy');
  process.exit(1);
}
