# Security Boundary

## Trust zones

| Zone | Allowed | Explicitly disallowed |
| --- | --- | --- |
| Orivellum gateway | Authenticated user request, policy decision, private VPN route | Direct Internet exposure of Lemonade or Factory |
| Factory control plane | Job orchestration, evidence records, project metadata | Public bind, automatic deployment, storage of secrets |
| Worktree | Agent reads/writes approved project files and runs allowlisted commands | Host-profile/vault access, shell, package install, push, reset, clean, or access outside the worktree |
| Lemonade Server | Local model inference only | Cloud routing, unless separately approved and configured outside this package |
| Preview | Local/private rendered site | Production database, public URL, credentialed session |

## Hard controls in this package

- Factory refuses non-loopback host bindings.
- Model endpoint defaults to Lemonade on loopback only.
- Paths are resolved within a per-job worktree and sensitive/hidden paths are denied.
- Agent commands must be argv arrays. Shells, installers, network tools, Git push/reset/clean, and package installation are denied.
- Each build starts in a new Git worktree and creates a checkpoint after verification.
- A release decision cannot merge, push, deploy, or delete anything.

## Remaining risks

Local AI is not an absolute security boundary. Treat all imported repositories and generated code as untrusted until verified. For unknown or hostile code, run the Factory in a separate Windows Sandbox, VM, or restricted WSL/container environment with no mounted knowledge-vault or credential directories. Do not put real production secrets into prompts, website files, test artifacts, screenshots, or agent logs.
