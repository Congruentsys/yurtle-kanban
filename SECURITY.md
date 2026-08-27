# Security Policy

## Reporting a Vulnerability

**Please do NOT report security vulnerabilities through public GitHub issues.**

We use GitHub's Private Vulnerability Reporting to handle security issues responsibly. This keeps the report confidential until a fix is available.

### How to Report

1. Navigate to the Security tab of this repository
2. Click "Report a vulnerability"
3. Fill out the vulnerability report form with as much detail as possible:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if you have one)

If Private Vulnerability Reporting is unavailable or you prefer email, contact: `security@congruentsys.com`

### What to Expect

- **Acknowledgement**: We will acknowledge receipt of your report. Our current capacity for issue response is still ramping up; we commit to acknowledgement but cannot yet commit to a specific timeframe.
- **Communication**: We will keep you informed as we investigate and work on a fix.
- **Disclosure**: We ask that you allow us time to address the vulnerability before public disclosure.
- **Credit**: We will credit you in the fix announcement unless you prefer to remain anonymous.

### Coordinated Disclosure

We believe in coordinated disclosure and ask that you:

- Give us reasonable time to investigate and fix the vulnerability before public disclosure
- Make a good faith effort to avoid privacy violations, data destruction, or service interruption
- Do not exploit the vulnerability beyond what is necessary to demonstrate it

We aim to:

- Respond to your report promptly
- Keep you updated on our progress
- Work with you to understand and resolve the issue
- Publicly credit you (if you wish) once the vulnerability is fixed

Typical disclosure timeline:
- **0-7 days**: Initial assessment and acknowledgement
- **7-90 days**: Development and testing of fix
- **After fix deployed**: Coordinated public disclosure

Complex vulnerabilities may take longer. We'll communicate timeline adjustments as needed.

## Supported Versions

This project pins dependencies by git SHA or version tags. Security fixes are applied to:

- The current `main` branch
- The most recent release tag (if applicable)

Users who pin to a specific SHA are responsible for updating to incorporate security fixes. We recommend:

- Pinning to recent commits on `main` for development
- Following release tags for production use
- Monitoring the Security Advisories for this repository

## Out of Scope

The following are **not** considered security vulnerabilities:

- Issues in dependencies that do not affect this project (report to the dependency maintainer)
- Theoretical vulnerabilities without a demonstrated attack path
- Social engineering attacks against project maintainers or users
- Denial of service via excessive API requests (rate limiting is the client's responsibility)
- Issues requiring physical access to a user's machine
- Vulnerabilities in outdated versions or forks not maintained by this project

If you're unsure whether something is in scope, please report it anyway. We'll triage it appropriately.

## Security Best Practices

When using this project:

1. **Keep dependencies updated**: Run `cargo update` (Rust) or `npm update` (Node.js) regularly
2. **Audit before deployment**: Review code and run available security scanners
3. **Principle of least privilege**: Run services with minimal required permissions
4. **Monitor advisories**: Watch this repository's Security tab for announcements

## Questions?

If you have questions about this policy or the security posture of this project, please open a public issue in the repository (for non-sensitive questions) or email `security@congruentsys.com`.
