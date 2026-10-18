# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: on this repository's
**Security** tab, choose **Report a vulnerability**. Do not open a public issue or
pull request for a security problem.

Include the affected version or commit, the steps to reproduce, and the impact you
expect. We aim to acknowledge reports within a few working days and will keep you
updated until a fix is released.

## Supported versions

Security fixes go into the latest release only.

## Deployment hardening

Several protections depend on how you deploy:

- **Set `CLIENT_IP_HEADER` only when the app can't be reached except through your
  proxy or CDN.** Rate limits key on that header. If clients can reach the app
  directly, they can forge it.
- **Keep the attachment bucket (`R2_STORAGE_BUCKET_NAME`) private.** Attachments
  are served through short-lived presigned URLs.
- **Run with `config.settings.production`.** The Docker image sets it by default.
  It turns on HTTPS redirects, HSTS and secure cookies.
- **`just seed` is for development only.** It refuses to run without `DEBUG=True`,
  and its admin password is public.
