# jigane Privacy Policy

Koki Takeda (kokiTakashiki) (the "Developer") publishes "jigane" (the "Software"), a Python CLI that assists with reviewing GitHub pull requests. This Privacy Policy sets out how information is handled in the Software. The conditions of use are set out in the [Terms of Use](TERMS.en.md).

## 1. Introduction

The Software is Python code that runs on the user's machine. The Developer does not have a server for the Software.

## 2. Handling of Information by the Developer

The Software does not collect any data, including personal information, does not store it, and does not send it to the Developer. The Developer does not receive the user's information through the Software.

## 3. Where Data Goes

Where data goes in the Software's operation is documented in `docs/data-destinations.md` in the repository. The Software not collecting data is a separate matter from data flowing from the user's environment to third-party services. The user decides whether content may be sent.

When the user selects Codex, the Software sends review materials and the output schema to OpenAI (`chatgpt.com`) using the ChatGPT login saved by the Codex CLI. The user consents through interactive confirmation or with `--yes` for a non-interactive run.

If the user selects an MCP server for a security review, the chosen CLI sends reference requests to that server. Retrieved material may be sent to Anthropic with Claude Code or to OpenAI with Codex.

## 4. Handling at Third-Party Services

How data is handled at the third-party services listed in `docs/data-destinations.md` (Anthropic, OpenAI, GitHub, and the MCP servers the user chooses) is governed by each service's privacy policy and terms. The Developer does not control them.

## 5. GitHub Issues

What you write in a GitHub Issue is public. Do not write secrets or personal information in it.

## 6. Deletion of the User's Information

The Developer holds no user information, so there is nothing to delete or correct on the Developer's side. Data on the user's machine is managed by the user.

## 7. Changes to this Privacy Policy

The Developer may change this Privacy Policy. When it changes, the Developer announces it by including the revised document in a GitHub release. The changed document takes effect when that release is published.

## 8. Contact

Send inquiries through GitHub Issues in the repository (<https://github.com/kokiTakashiki/jigane/issues>). Issues are public. Report vulnerabilities by the procedure in SECURITY.md.

---

Effective date: 2026-10-24
Last updated: 2026-11-20
