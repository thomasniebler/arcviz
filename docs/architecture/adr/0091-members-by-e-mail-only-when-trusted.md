# ADR-0091: An e-mail address adds a member only when it is trusted and unambiguous

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/workspaces-and-sharing.md`
- Amends: ADR-0085 (members are added by exact id or e-mail of a known user)

## Context

A workspace owner adds a member by user id or by e-mail address. The directory took the first record with that
address. With Easy Auth the stored address may come from the `preferred_username` or `name` claim, or from
`X-MS-CLIENT-PRINCIPAL-NAME`, none of which proves the address belongs to the user. An attacker who signs in with a
victim's address as their name could be the one added, and gain access the owner meant for the victim.

## Decision

- An identity's e-mail address is **trusted** only when it is exactly the value of the configured e-mail header
  (`ARCVIZ_AUTH_PROXY_EMAIL_HEADER`, set by the `oauth2-proxy` preset), or from the operator
  (`ARCVIZ_ADMIN_EMAIL`). An address taken from a claim or the name header is display data only. The directory
  stores the flag (`UserRecord.email_trusted`).
- Adding by e-mail matches trusted addresses only (case-insensitive). If several users have that address, the
  request is refused (400) and the owner is asked for the user id.
- The response names exactly who was added: `added` = id, name and e-mail. The members dialog shows it.
- The guide recommends adding by user id as the safe way. With Easy Auth (which has no e-mail header) adding
  by e-mail works only once an e-mail header is configured.

## Consequences

Easy Auth deployments add members by their Entra object id unless they configure an e-mail header they trust. A
record written before this decision has `email_trusted` false and is found by e-mail only after its user's next
sign-in through a trusted header.
