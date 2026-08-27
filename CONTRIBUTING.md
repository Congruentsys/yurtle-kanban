# Contributing

Thank you for your interest in contributing to this project!

## How to Contribute

### Reporting Issues

- **Search first**: Check if someone has already reported the issue
- **Be specific**: Include version numbers, error messages, and steps to reproduce
- **Security issues**: See [SECURITY.md](SECURITY.md) for reporting vulnerabilities privately

### Proposing Changes

1. **Fork** the repository
2. **Create a branch** for your changes (`git checkout -b feature/my-feature`)
3. **Make your changes** following the project's coding standards
4. **Test** your changes thoroughly
5. **Commit** with clear, descriptive messages
6. **Push** to your fork
7. **Open a Pull Request** with:
   - Clear description of what changed and why
   - Reference to any related issues
   - Test results or evidence the change works

### Code Review Process

- All submissions require review before merging
- Reviewer ≠ author (a different person reviews your code)
- We may ask for changes or clarifications
- Please be patient — reviews happen as capacity allows

### Coding Standards

- **Rust**: Follow `cargo fmt` and `cargo clippy` conventions
- **Tests**: Include tests for new functionality
- **Documentation**: Update docs for user-facing changes
- **Commits**: Write clear commit messages explaining *why*, not just *what*

### Testing

Before submitting a PR:

```bash
# Rust projects
cargo test
cargo clippy
cargo fmt --check

# Node.js projects
npm test
npm run lint
```

### License

By contributing, you agree that your contributions will be licensed under the same license as the project (see LICENSE file).

## Questions?

- Open an issue for questions about using the project
- Tag with `question` or `help wanted` for community assistance
- For project direction or policy questions, contact the maintainers

## Code of Conduct

This project follows our [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you agree to uphold it.
