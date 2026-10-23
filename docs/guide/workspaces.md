# Own and shared projects

Many people can use one Arcviz installation without seeing each other's work: every project belongs to one person,
and nobody else sees it unless its owner shares it. Design: [Personal workspaces and direct
sharing](../architecture/specs/personal-workspaces.md).

## Who you are

Arcviz does not have its own login: it runs behind the organisation's sign-in (for example Microsoft Entra ID
through Azure Easy Auth, or oauth2-proxy), which tells Arcviz who you are. Your name appears at the top of the page;
its tooltip shows your e-mail address and your user id. When Arcviz runs locally without authentication (or with the
shared token), everybody is the same user, the administrator, and owns everything.

## Your projects and the ones shared with you

- The project selector in the navigator has two groups: **Meine Projekte**, the projects you own, and **Mit mir
  geteilt**, the projects others shared with you (with the owner's name after the title).
- **+** next to the selector creates a new project; it is yours. Imported projects are yours too.
- Everybody starts with an own, empty **Standardprojekt**. A diagram you create without choosing a project goes
  there.
- Your projects live in your **personal workspace**, which Arcviz creates the first time you open it. There are no
  team workspaces and no members to manage: sharing is per project.

## Sharing a project

In the project menu (**⋯** next to the project) choose **Freigeben…**:

- As the owner you see yourself and everybody you shared the project with, each with a role. Start typing a name
  (two letters are enough): people who have opened Arcviz before are suggested; pick one, choose **Betrachten** or
  **Bearbeiten** and press **Freigeben**. You can also enter a user id or an e-mail address directly. Change a role with its selector,
  remove a share with **Entfernen**.
- You can share with people who have opened Arcviz once (so that Arcviz knows them). **The user id is the safe
  way**: an e-mail address is accepted only if the sign-in proxy vouches for it (an e-mail header, for example with
  oauth2-proxy) and only one person has it; with Azure Easy Auth use the user id. After sharing, the dialog shows
  exactly with whom (name, e-mail and user id): check it.
- Everybody else sees the owner and their own role in that dialog, read-only. Only the owner shares: editors cannot
  pass a project on.
- When you remove a share, that person loses access at once: an open diagram of the project closes on their screen
  (*nicht mehr verfügbar*) and the project disappears from their selector.

## Roles

| Role | May |
|---|---|
| **Betrachten** (viewer) | open every diagram, the versions and the chat history; export; run the rule checks and the AI review; compare versions |
| **Bearbeiten** (editor) | everything a viewer may, plus change diagrams (chat, inspector, canvas, undo/redo, versions), sections, the shared model and the project chat, confirm proposals, create and import diagrams in the project |
| **Besitzen** (owner) | everything an editor may, plus share the project, move diagrams out of it into other projects, delete diagrams and delete the project |

- The owner of a project is the person whose personal workspace holds it; every project has exactly one owner.
- Viewers see the project read-only: the editing controls are disabled and the chat input says
  *Nur Lesezugriff*. A change attempted anyway (for example through the API) is refused with *Nur Lesezugriff: Ihre
  Rolle erlaubt diese Änderung nicht.*
- If the owner lowers or removes your share while the assistant is working on your message, it stops changing the
  diagram; what it already changed is kept as a version under your name, and the reply says so.
- New diagrams and projects get an id made of their name and a random suffix (`orders-k3fax2abqe`); the id you ask
  for is only its beginning. It never shows whether somebody else uses the same name.

## Data from earlier versions

When Arcviz is updated, existing data moves automatically and nobody loses or gains access: the data from before
workspaces existed, and the former *Standard-Arbeitsbereich*, belong to the administrator (`ARCVIZ_ADMIN_USER`);
the projects of every former team workspace belong to that workspace's owner, except a project a member created
there: it belongs to that member, and the former workspace owner can still edit it. Everybody else who could see a
project before keeps exactly their role as a share.

## Who changed what

Every version records who made it, and the chat shows who wrote each message. Versions and messages from before
users existed have no author.
