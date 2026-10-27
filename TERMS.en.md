# jigane Terms of Use

## Article 1 (Purpose, Application, and Consent)

1. These Terms of Use (the "Terms") set the conditions for using "jigane" (the "Software"), a Python CLI published by Koki Takeda (kokiTakashiki) (the "Developer") to assist with reviewing GitHub pull requests.
2. By installing or using the Software, the user is deemed to have agreed to the Terms. If you do not agree, do not install it.

## Article 2 (Nature of the Software)

1. The Software is Python code that runs in the user's environment. The Developer does not have a server for the Software.
2. In the user's environment, the Software starts external tools that the user installs separately, and the MCP servers the user chooses. See README.md for the list of external tools.
3. All source code distributed as the Software is public. Before using the Software, the user can verify how it behaves and where data goes.

## Article 3 (Relationship with the MIT License)

1. The source code and distributions of the Software are provided under the MIT License (the repository's LICENSE). Permission to use, copy, modify, and redistribute is as the MIT License provides.
2. The Terms do not narrow the permissions granted by the MIT License. If they conflict, the MIT License prevails as to the scope of permission.
3. The disclaimer in Article 8 restates the MIT License's disclaimer for the situations in which the Software is used.

## Article 4 (User Responsibilities)

1. All operations, including obtaining and installing the Software, are performed at the user's own judgment and responsibility. Operations include commands, arguments, environment variables, configuration files, and installing external tools.
2. The user chooses what to make a review target and what to have read as input, and is responsible for that choice. This includes having read instructions that a third party planted in a PR description, comments, code, or the like.
3. Whether to make a working tree containing secrets a review target, and whether to use MCP servers and external languages after verifying their origin, are the user's decisions. See SECURITY.md for details.
4. Costs are borne by the user under the user's own contract. Estimates are a guide, and limits can be exceeded.
5. LLM output may contain errors. The user checks results before adopting them.
6. The user complies with the laws concerning the use of the Software and with contracts with rights holders of the review target.

## Article 5 (Third-Party Services)

1. The terms and licenses of the third-party services involved in the Software's operation apply to the user as they are. The Terms do not change, replace, or assume them. See `docs/data-destinations.md` for the list of services involved.
2. Use of data for training and retention periods at third-party services follow the settings and contract of the user's own account.
3. The Developer is not responsible for the provision, changes, suspension, fees, or terms of third-party services. The user is responsible for checking them.

## Article 6 (Information Passed to Claude Code)

1. The Software formats, on the user's behalf, data that the user could pass to Claude Code on their own, and passes it to Claude Code in the user's environment.
2. The kinds of information passed are listed in the table in the section "レビューの入力と Claude Code" (Review inputs and Claude Code) of SECURITY.md.
3. When the user answers y, the user is deemed to have understood and approved that the information shown in the table is passed to Claude Code.
4. How Claude Code handles the information it receives follows the terms of Claude Code and Anthropic and the user's settings.

## Article 7 (Handling of Data)

1. The handling of the user's data is set out in the Privacy Policy ([PRIVACY.en.md](PRIVACY.en.md)).
2. Issues in the repository are public on GitHub. Do not write secrets or personal information in them.

## Article 8 (Developer's Stance, Limits, and Disclaimer)

1. The Developer has put in place the safeguards that can be put in place. There are still limits. What the limits are is written in the public README.md, SECURITY.md, and `docs/data-destinations.md`.
2. The Software is an unofficial tool made by an individual, with no affiliation with, approval from, or sponsorship by Anthropic, GitHub, or any other third party.
3. In accordance with the MIT License, the Software is provided "as is". The Developer gives no warranty of any kind, express or implied, including warranties of merchantability, fitness for a particular purpose, and non-infringement. The Developer also does not guarantee that review results are correct or free of omissions.
4. The Developer is not responsible for consequences arising from the following.
   1. The user's operations and choices under Article 4 (including intentional operations by the user or a third party, and malicious operations)
   2. Use of a modified version of the Software, or of a version the Developer has not published
   3. The operation, changes, suspension, fees, or terms of the third-party services in Article 5
   4. Defects in the user's environment
5. Do not write vulnerabilities in a public Issue; report them by the procedure in SECURITY.md. The Developer will try to fix them but does not promise the timing or the existence of a fix.

## Article 9 (Limitation of Liability)

1. The Developer is not liable for damages arising from the use of, or inability to use, the Software (including direct, indirect, special, incidental, and consequential damages).
2. Paragraph 1 does not apply where the Developer has acted intentionally or with gross negligence, or where the law does not permit liability to be limited.

## Article 10 (Changes to and Termination of the Software)

The Developer may change the Software and end its provision without prior notice. Versions already distributed remain available under the MIT License.

## Article 11 (Changes to the Terms)

The Developer may change the Terms. When the Terms change, the Developer announces it by including the revised Terms in a GitHub release. Changed Terms take effect when that release is published, and a user who keeps using the Software after a change is deemed to have agreed to the changed Terms.

## Article 12 (Governing Law and Jurisdiction)

The Terms are governed by the laws of Japan. Disputes arising in connection with the Terms or the Software are subject to the exclusive jurisdiction, as the court of first instance, of the district court having jurisdiction over the Developer's location.

## Article 13 (Language)

The Terms are made in Japanese and English. If the two versions conflict, the Japanese version prevails.

## Article 14 (Contact)

Send inquiries through GitHub Issues in the repository (<https://github.com/kokiTakashiki/jigane/issues>). Do not write vulnerabilities in an Issue, as stated in Article 8, paragraph 5.

---

Effective date: 2026-10-24
Last updated: 2026-10-24
