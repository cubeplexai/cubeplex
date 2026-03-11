# Git Commit Skill

This skill helps create well-formatted git commits following conventional commit standards.

## Usage

When the user asks to commit changes, follow these steps:

1. Check git status to see what files have changed
2. Review the changes using git diff
3. Create a commit message following the format:
   ```
   <type>(<scope>): <subject>

   <body>

   <footer>
   ```

## Commit Types

- `feat`: A new feature
- `fix`: A bug fix
- `docs`: Documentation only changes
- `style`: Code style changes (formatting, missing semicolons, etc)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

## Example

```bash
git commit -m "feat(auth): add JWT token validation

Implement token expiration check and signature verification
to improve API security.

Closes #123"
```
